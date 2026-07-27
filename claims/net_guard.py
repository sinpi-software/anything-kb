"""SSRF guard for outbound fetches of operator-supplied URLs.

Source URLs come from neonews.toml and from third-party feeds, then get fetched
server-side. Without a guard, `http://169.254.169.254/…` or an in-cluster address
like `ingestion-postgres:5432` would be fetched and its response handed onward — a
classic SSRF into the cluster network. Every hop (initial request and each redirect)
passes through `assert_public_url`.

Copied from neonews/net_guard.py — claims is a standalone project and imports nothing from it.
"""

from __future__ import annotations

import ipaddress
import socket as socket
import ssl
import urllib.request
from functools import lru_cache
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlsplit

import certifi

import config

ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedURLError(ValueError):
    """A URL was refused because it isn't a public http(s) endpoint."""


def _is_public_ip(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Return `url` if it is a public http(s) endpoint, else raise BlockedURLError.

    Every DNS answer for the host must be public, so a hostname resolving even
    partly to a private/loopback/link-local/reserved range is refused — closing
    DNS-rebinding and metadata-endpoint vectors.
    """
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"scheme not allowed: {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise BlockedURLError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        raise BlockedURLError(f"could not resolve host: {host}") from exc
    blocked = [ip for ip in {str(info[4][0]) for info in infos} if not _is_public_ip(ip)]
    if blocked:
        raise BlockedURLError(f"host {host} resolves to non-public address(es): {', '.join(blocked)}")
    return url


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the guard on every redirect target, so a public URL can't bounce the
    fetch to an internal address."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


@lru_cache(maxsize=1)
def guarded_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()), _GuardedRedirectHandler())


def fetch(url: str, timeout: int = config.FETCH_TIMEOUT_SECONDS) -> bytes:
    """Guarded GET. The single outbound fetch path for feeds and article pages alike."""
    assert_public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with guarded_opener().open(request, timeout=timeout) as response:
        return bytes(response.read())

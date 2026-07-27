import http.client
import io
import urllib.request

import pytest

import net_guard


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.0.202:8000/content",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http:///nohost",
    ],
)
def test_blocks_non_public_urls(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int | None, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        table = {
            "127.0.0.1": "127.0.0.1",
            "localhost": "127.0.0.1",
            "169.254.169.254": "169.254.169.254",
            "10.0.0.5": "10.0.0.5",
            "192.168.0.202": "192.168.0.202",
        }
        if host not in table:
            raise OSError("unresolvable")
        return [(2, 1, 6, "", (table[host], port or 80))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.assert_public_url(url)


def test_resolution_failure_is_not_a_blocked_url() -> None:
    """A host that fails to resolve is transient (HostResolutionError), distinct from
    one that resolves somewhere disallowed (BlockedURLError) — extract.py routes the
    two to different retry behavior and must not be able to confuse them."""
    assert not issubclass(net_guard.HostResolutionError, net_guard.BlockedURLError)
    assert not issubclass(net_guard.HostResolutionError, ValueError)


def test_unresolvable_host_raises_host_resolution_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int | None, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        raise OSError("unresolvable")

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(net_guard.HostResolutionError):
        net_guard.assert_public_url("https://does-not-resolve.example/x")


def test_loopback_host_still_raises_blocked_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port, **kw: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.assert_public_url("http://127.0.0.1/x")


def test_allows_a_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        net_guard.socket, "getaddrinfo", lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    assert net_guard.assert_public_url("https://example.com/feed.xml") == "https://example.com/feed.xml"


def test_blocks_when_any_dns_answer_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host resolving to both a public and a private address is a DNS-rebinding vector."""
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.assert_public_url("https://sneaky.example.com/feed.xml")


def test_redirect_handler_blocks_redirect_hop_to_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must re-run on the redirect target, not just the initial URL — a
    public URL must not be able to bounce the fetch to an internal address."""

    def fake_getaddrinfo(
        host: str, port: int | None, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        table = {"example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"}
        return [(2, 1, 6, "", (table[host], port or 80))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    handler = net_guard._GuardedRedirectHandler()
    req = urllib.request.Request("https://example.com/feed.xml")
    headers = http.client.HTTPMessage()
    with pytest.raises(net_guard.BlockedURLError):
        handler.redirect_request(
            req, io.BytesIO(b""), 302, "Found", headers, "http://169.254.169.254/latest/meta-data/"
        )


def test_redirect_handler_allows_redirect_hop_to_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect to another public address is let through — the guard discriminates
    rather than blocking every redirect."""

    def fake_getaddrinfo(
        host: str, port: int | None, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        table = {"example.com": "93.184.216.34", "cdn.example.com": "93.184.216.35"}
        return [(2, 1, 6, "", (table[host], port or 80))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
    handler = net_guard._GuardedRedirectHandler()
    req = urllib.request.Request("https://example.com/feed.xml")
    headers = http.client.HTTPMessage()
    newurl = "https://cdn.example.com/feed.xml"
    result = handler.redirect_request(req, io.BytesIO(b""), 302, "Found", headers, newurl)
    assert result is not None
    assert result.full_url == newurl

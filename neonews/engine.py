"""The knowledge-graph engine's API, and the only module that knows its HTTP shape.

Bearer-authenticated: `POST /content`, `GET /content/{job_id}`, `POST /graphql`.
neonews never touches the engine's database — this is the whole interface.
"""

from __future__ import annotations

from functools import lru_cache
from os import getenv
from typing import Any

import httpx

import config

# Strawberry camel-cases field names on the wire, so these are `publishedAt`, not
# `published_at`. Asking for snake_case is a GraphQL error, not an empty result.
_SOURCES_QUERY = """
query Sources($since: String, $limit: Int!) {
  sources(since: $since, limit: $limit) {
    id
    label
    publishedAt
    ingestedAt
    entities {
      id
      type
      name
      summary
      article
      updatedAt
    }
  }
}
"""


class EngineError(Exception):
    """The engine rejected a request, or returned GraphQL errors."""


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    base_url = getenv(config.ENGINE_URL_ENV, config.ENGINE_URL_DEFAULT)
    return httpx.Client(base_url=base_url, timeout=config.ENGINE_TIMEOUT_SECONDS)


def _auth_headers() -> dict[str, str]:
    api_key = getenv(config.ENGINE_API_KEY_ENV)
    if not api_key:
        raise EngineError(f"{config.ENGINE_API_KEY_ENV} is not set")
    return {"Authorization": f"Bearer {api_key}"}


def _json(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise EngineError(f"{what} failed: HTTP {response.status_code} {response.text[:200]}")
    return dict(response.json())


def post_content(text: str, metadata: dict[str, Any]) -> str:
    """Queue text for ingestion. Returns the engine's job_id (HTTP 202)."""
    body = _json(
        _client().post("/content", json={"text": text, "metadata": metadata}, headers=_auth_headers()),
        "post_content",
    )
    job_id = body.get("job_id")
    if not job_id:
        raise EngineError(f"post_content returned no job_id: {body}")
    return str(job_id)


def job_status(job_id: str) -> dict[str, Any]:
    """Poll a job: pending | processing | done | skipped | failed."""
    return _json(_client().get(f"/content/{job_id}", headers=_auth_headers()), "job_status")


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a read query. GraphQL errors arrive as HTTP 200 — surface them loudly, so a
    broken query can never masquerade as an empty graph."""
    body = _json(
        _client().post("/graphql", json={"query": query, "variables": variables}, headers=_auth_headers()),
        "graphql",
    )
    if body.get("errors"):
        raise EngineError(f"graphql errors: {body['errors']}")
    return dict(body.get("data") or {})


def recent_sources(since: str | None, limit: int) -> list[dict[str, Any]]:
    """Sources ingested at/after `since`, newest first, each with its mentioned entities.

    One round-trip gives the draft flow both its clustering keys (which sources share
    which entities) and its writing material (those entities' articles).
    """
    data = graphql(_SOURCES_QUERY, {"since": since, "limit": limit})
    return list(data.get("sources") or [])

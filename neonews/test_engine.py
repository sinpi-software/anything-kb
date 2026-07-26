import os
from collections.abc import Callable

os.environ["NEONEWS_ENGINE_URL"] = "https://engine.test"
os.environ["NEONEWS_ENGINE_API_KEY"] = "test-key"

import httpx
import pytest

import engine


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://engine.test")


def test_post_content_sends_bearer_and_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(202, json={"job_id": "job-1"})

    monkeypatch.setattr(engine, "_client", lambda: _client(handler))
    assert engine.post_content("hello", {"url": "https://x.test/a"}) == "job-1"
    assert seen["auth"] == "Bearer test-key"
    assert seen["url"] == "https://engine.test/content"
    assert '"url": "https://x.test/a"' in seen["body"] or '"url":"https://x.test/a"' in seen["body"]


def test_post_content_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "_client", lambda: _client(lambda r: httpx.Response(401, json={"detail": "bad key"})))
    with pytest.raises(engine.EngineError, match="401"):
        engine.post_content("hello", {})


def test_job_status_returns_the_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine,
        "_client",
        lambda: _client(lambda r: httpx.Response(200, json={"job_id": "job-1", "status": "done"})),
    )
    assert engine.job_status("job-1")["status"] == "done"


def test_job_status_raises_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "_client", lambda: _client(lambda r: httpx.Response(404, json={})))
    with pytest.raises(engine.EngineError, match="404"):
        engine.job_status("nope")


def test_graphql_returns_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine, "_client", lambda: _client(lambda r: httpx.Response(200, json={"data": {"sources": []}}))
    )
    assert engine.graphql("{ sources { id } }", {}) == {"sources": []}


def test_graphql_raises_on_errors_rather_than_returning_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GraphQL error arrives as HTTP 200. Silently returning {} would make an
    empty issue indistinguishable from a broken query."""
    monkeypatch.setattr(
        engine,
        "_client",
        lambda: _client(lambda r: httpx.Response(200, json={"errors": [{"message": "invalid `since`"}]})),
    )
    with pytest.raises(engine.EngineError, match="invalid `since`"):
        engine.graphql("{ sources { id } }", {})


def test_recent_sources_requests_camel_case_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strawberry camel-cases the wire names; a snake_case query is a GraphQL error."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": {"sources": [{"id": "job-1", "label": "L", "entities": []}]}})

    monkeypatch.setattr(engine, "_client", lambda: _client(handler))
    rows = engine.recent_sources("2026-07-01T00:00:00", 50)
    assert "publishedAt" in seen["body"] and "ingestedAt" in seen["body"] and "updatedAt" in seen["body"]
    assert "published_at" not in seen["body"]
    assert rows[0]["id"] == "job-1"


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEONEWS_ENGINE_API_KEY", raising=False)
    engine._client.cache_clear()
    with pytest.raises(engine.EngineError, match="NEONEWS_ENGINE_API_KEY"):
        engine.post_content("hello", {})
    monkeypatch.setenv("NEONEWS_ENGINE_API_KEY", "test-key")
    engine._client.cache_clear()

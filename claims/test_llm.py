from typing import Any

import httpx
import pytest
from pydantic import BaseModel

import llm
from llm import LLMError, complete, strict_schema


class _Thing(BaseModel):
    name: str
    count: int = 3


def test_strict_schema_forbids_extra_keys_and_requires_every_property() -> None:
    schema = strict_schema(_Thing.model_json_schema())
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["count", "name"]
    assert "default" not in schema["properties"]["count"]


def _response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "https://openrouter.ai"))


def _patch_post(monkeypatch: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Patch the transport and hand the test the request body that was sent."""
    sent: dict[str, Any] = {}

    def _post(url: str, **kwargs: Any) -> httpx.Response:
        sent.update(kwargs.get("json") or {})
        return _response(payload)

    monkeypatch.setattr(llm, "_post", _post)
    return sent


def test_complete_returns_content_and_no_annotations(monkeypatch: Any) -> None:
    sent = _patch_post(monkeypatch, {"choices": [{"message": {"content": '{"name": "x", "count": 1}'}}]})
    result = complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)
    assert result.content == '{"name": "x", "count": 1}'
    assert result.citation_urls == frozenset()
    assert result.had_annotations is False
    assert "plugins" not in sent


def test_complete_collects_url_citation_annotations(monkeypatch: Any) -> None:
    _patch_post(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": "{}",
                        "annotations": [
                            {"type": "url_citation", "url_citation": {"url": "https://a.test/x", "title": "A"}},
                            {"type": "url_citation", "url_citation": {"url": "https://b.test/y", "title": "B"}},
                            {"type": "file_citation", "file_citation": {"file_id": "f1"}},
                        ],
                    }
                }
            ]
        },
    )
    result = complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing, web=True)
    assert result.citation_urls == frozenset({"https://a.test/x", "https://b.test/y"})
    assert result.had_annotations is True


def test_complete_sends_the_web_plugin_only_when_asked(monkeypatch: Any) -> None:
    sent = _patch_post(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing, web=True)
    assert sent["plugins"] == [{"id": "web", "max_results": llm.config.WEB_MAX_RESULTS}]


def test_complete_raises_when_the_model_returns_no_content(monkeypatch: Any) -> None:
    _patch_post(monkeypatch, {"choices": [{"message": {"content": "   "}}]})
    with pytest.raises(LLMError):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)


def test_complete_raises_when_there_are_no_choices(monkeypatch: Any) -> None:
    _patch_post(monkeypatch, {"choices": []})
    with pytest.raises(LLMError):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)

from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError

import llm
from llm import LLMError, complete, strict_schema


class _Thing(BaseModel):
    name: str
    count: int = 3


class _Scored(BaseModel):
    score: float = Field(ge=0.0, le=1.0)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: Any) -> None:
    """Every test gets a non-empty key by default; the one test that cares about an
    empty key overrides this with its own monkeypatch.setenv."""
    monkeypatch.setenv(llm.config.OPENROUTER_API_KEY_ENV, "sk-test-not-a-real-key")


def test_strict_schema_forbids_extra_keys_and_requires_every_property() -> None:
    schema = strict_schema(_Thing.model_json_schema())
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["count", "name"]
    assert "default" not in schema["properties"]["count"]


def test_strict_schema_strips_numeric_bounds_openai_rejects() -> None:
    """OpenAI's strict structured-output mode 400s on minimum/maximum. Field(ge=, le=)
    is exactly what ExtractedClaim.checkworthiness uses, so a real extraction call
    would fail every time if these leaked into the schema sent over the wire."""
    schema = strict_schema(_Scored.model_json_schema())
    assert "minimum" not in schema["properties"]["score"]
    assert "maximum" not in schema["properties"]["score"]


def test_strict_schema_stripping_the_bound_does_not_weaken_pydantic_validation() -> None:
    """Proof that stripping minimum/maximum from the *outgoing* schema costs nothing:
    pydantic still enforces ge/le on the model itself when the response is parsed."""
    with pytest.raises(ValidationError):
        _Scored.model_validate({"score": 1.5})


def _patch_post(
    monkeypatch: Any, payload: dict[str, Any], status_code: int = 200
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Patch the transport and hand the test both the request body and the full kwargs
    _post was called with, so tests can assert on things like `timeout=` too."""
    sent: dict[str, Any] = {}
    sent_kwargs: dict[str, Any] = {}

    def _post(url: str, **kwargs: Any) -> httpx.Response:
        sent.update(kwargs.get("json") or {})
        sent_kwargs.update(kwargs)
        return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "https://openrouter.ai"))

    monkeypatch.setattr(llm, "_post", _post)
    return sent, sent_kwargs


def test_complete_returns_content_and_no_annotations(monkeypatch: Any) -> None:
    sent, _ = _patch_post(monkeypatch, {"choices": [{"message": {"content": '{"name": "x", "count": 1}'}}]})
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


def test_complete_ignores_a_url_citation_payload_under_the_wrong_type(monkeypatch: Any) -> None:
    """The type check is the security-relevant line: verify.py's grounding filter uses
    citation_urls as an allowlist, so a payload that merely carries a url_citation
    dict under some other `type` must not sneak its URL into the result."""
    _patch_post(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": "{}",
                        "annotations": [
                            {"type": "file_citation", "url_citation": {"url": "https://evil.test/x"}},
                        ],
                    }
                }
            ]
        },
    )
    result = complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing, web=True)
    assert "https://evil.test/x" not in result.citation_urls


def test_complete_skips_malformed_annotations_without_raising(monkeypatch: Any) -> None:
    _patch_post(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": "{}",
                        "annotations": [
                            {"type": "url_citation", "url_citation": "not-a-dict"},
                            {"type": "url_citation", "url_citation": ["also", "not", "a", "dict"]},
                            {"type": "url_citation", "url_citation": {"url": 12345}},
                            {"type": "url_citation", "url_citation": {"url": ""}},
                            "not-a-dict-annotation",
                            {"type": "url_citation", "url_citation": {"url": "https://good.test/x"}},
                        ],
                    }
                }
            ]
        },
    )
    result = complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing, web=True)
    assert result.citation_urls == frozenset({"https://good.test/x"})
    assert result.had_annotations is True


def test_complete_sends_the_web_plugin_only_when_asked(monkeypatch: Any) -> None:
    sent, _ = _patch_post(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing, web=True)
    assert sent["plugins"] == [{"id": "web", "max_results": llm.config.WEB_MAX_RESULTS}]


def test_complete_sends_the_configured_timeout(monkeypatch: Any) -> None:
    _, sent_kwargs = _patch_post(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)
    assert sent_kwargs["timeout"] == llm.config.LLM_TIMEOUT_SECONDS


def test_complete_raises_when_the_model_returns_no_content(monkeypatch: Any) -> None:
    _patch_post(monkeypatch, {"choices": [{"message": {"content": "   "}}]})
    with pytest.raises(LLMError):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)


def test_complete_raises_a_named_error_when_the_response_is_truncated(monkeypatch: Any) -> None:
    """A response cut off at the token limit is truncated JSON. Without reading
    finish_reason, that surfaces four layers away as a bare JSONDecodeError — this
    pins that it instead names the token limit, so a human reader never sees
    "could not be checked: Unterminated string starting at line 1 column ..."."""
    _patch_post(
        monkeypatch,
        {"choices": [{"finish_reason": "length", "message": {"content": '{"name": "x"'}}]},
    )
    with pytest.raises(LLMError, match="token limit"):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)


def test_complete_raises_when_there_are_no_choices(monkeypatch: Any) -> None:
    _patch_post(monkeypatch, {"choices": []})
    with pytest.raises(LLMError):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)


def test_complete_raises_on_an_http_error_status(monkeypatch: Any) -> None:
    _patch_post(monkeypatch, {"error": {"message": "invalid request"}}, status_code=500)
    with pytest.raises(LLMError, match="500"):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)


def test_complete_raises_when_the_api_key_is_empty(monkeypatch: Any) -> None:
    monkeypatch.setenv(llm.config.OPENROUTER_API_KEY_ENV, "")
    with pytest.raises(LLMError, match=llm.config.OPENROUTER_API_KEY_ENV):
        complete(model="m", system="s", user="u", schema_name="thing", schema=_Thing)

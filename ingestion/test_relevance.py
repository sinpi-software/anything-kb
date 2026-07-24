import os

os.environ.setdefault("INGESTION_OPENROUTER_API_KEY", "test-key-not-used")

import pytest

import relevance


class _CtxNull:
    def __enter__(self) -> "_CtxNull":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "OpenRouter", lambda **k: _CtxNull())


def test_build_relevance_messages_includes_prompt_and_content() -> None:
    msgs = relevance.build_relevance_messages("Only politics.", "A story about an election.")
    joined = " ".join(m["content"] for m in msgs)
    assert "Only politics." in joined
    assert "A story about an election." in joined
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_relevant_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: '{"relevant": true, "reason": "on topic"}')
    result = relevance.judge_relevance("prompt", "content")
    assert result.relevant is True
    assert result.reason == "on topic"


def test_relevant_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: '{"relevant": false, "reason": "off topic"}')
    assert relevance.judge_relevance("prompt", "content").relevant is False


def test_unparseable_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed judgment must NOT masquerade as relevant=False (which the worker would
    # record as `skipped`, silently dropping the content). It raises so the worker
    # retries / marks the job failed.
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: "this is not json")
    with pytest.raises(relevance.RelevanceError):
        relevance.judge_relevance("prompt", "content")


def test_empty_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relevance, "_chat", lambda *a, **k: None)
    with pytest.raises(relevance.RelevanceError):
        relevance.judge_relevance("prompt", "content")

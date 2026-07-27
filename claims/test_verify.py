import json
import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

import config
import llm
import verify
from db import get_postgres_session
from models import Claim, Document, Evidence
from verify import EvidenceItem, dedupe_evidence, ground_evidence, verify_claims


def _item(url: str, stance: str = "supports") -> EvidenceItem:
    return EvidenceItem(url=url, title="T", snippet="S", stance=stance)


def test_ground_evidence_drops_urls_absent_from_the_annotations() -> None:
    items = [_item("https://real.test/a"), _item("https://invented.test/b")]
    kept = ground_evidence(items, frozenset({"https://real.test/a"}), had_annotations=True)
    assert [item.url for item in kept] == ["https://real.test/a"]


def test_ground_evidence_keeps_everything_when_there_are_no_annotations() -> None:
    """No annotations means nothing to check against — not that everything is fake.
    Dropping here would silently discard every piece of evidence from a provider that
    does not report citations."""
    items = [_item("https://a.test/x"), _item("https://b.test/y")]
    kept = ground_evidence(items, frozenset(), had_annotations=False)
    assert len(kept) == 2


def test_ground_evidence_drops_everything_when_annotations_are_present_but_disjoint() -> None:
    items = [_item("https://invented.test/a")]
    assert ground_evidence(items, frozenset({"https://real.test/b"}), had_annotations=True) == []


def test_ground_evidence_compares_canonicalized_urls() -> None:
    """The model may echo a tracking-tagged variant of a URL OpenRouter really visited."""
    items = [_item("https://real.test/a?utm_source=x")]
    kept = ground_evidence(items, frozenset({"https://real.test/a"}), had_annotations=True)
    assert len(kept) == 1


def test_dedupe_evidence_collapses_the_same_url_and_stance() -> None:
    items = [_item("https://a.test/x"), _item("https://a.test/x/?utm_source=y")]
    assert len(dedupe_evidence(items)) == 1


def test_dedupe_evidence_keeps_one_url_under_opposing_stances() -> None:
    """A source that genuinely cuts both ways is signal the judge should see."""
    items = [_item("https://a.test/x", "supports"), _item("https://a.test/x", "contradicts")]
    assert len(dedupe_evidence(items)) == 2


def test_dedupe_evidence_preserves_order() -> None:
    items = [_item("https://b.test/y"), _item("https://a.test/x")]
    assert [item.url for item in dedupe_evidence(items)] == ["https://b.test/y", "https://a.test/x"]


@pytest.fixture
def claim() -> Any:
    """A throwaway selected claim under its own document, torn down with its evidence."""
    document_ids: list[str] = []

    def _make(**kwargs: Any) -> str:
        with get_postgres_session() as session:
            document = Document(
                url=f"https://example.com/{os.urandom(4).hex()}",
                canonical_url=f"https://example.com/{os.urandom(4).hex()}",
                full_text="Body text mentioning the claim.",
                extracted_at=datetime.now(UTC),
            )
            session.add(document)
            session.flush()
            row = Claim(
                document_id=document.id,
                text="Crime fell 20%.",
                claim_type="empirical",
                checkworthiness=0.9,
                selected_for_verification=True,
                **kwargs,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            document_ids.append(str(document.id))
            return str(row.id)

    yield _make

    with get_postgres_session() as session:
        for document_id in document_ids:
            claim_ids = session.scalars(select(Claim.id).where(Claim.document_id == document_id)).all()
            for claim_id in claim_ids:
                session.execute(delete(Evidence).where(Evidence.claim_id == claim_id))
            session.execute(delete(Claim).where(Claim.document_id == document_id))
            session.execute(delete(Document).where(Document.id == document_id))
        session.commit()


def _patch_calls(monkeypatch: Any, evidence: list[dict[str, Any]], judgment: dict[str, Any]) -> None:
    """Patch the one seam. Research calls return `evidence`; the judge returns `judgment`."""

    def _complete(**kwargs: Any) -> llm.LLMResult:
        if kwargs["schema_name"] == "research":
            return llm.LLMResult(
                content=json.dumps({"evidence": evidence}),
                citation_urls=frozenset(item["url"] for item in evidence),
                had_annotations=True,
            )
        return llm.LLMResult(content=json.dumps(judgment), citation_urls=frozenset(), had_annotations=False)

    monkeypatch.setattr(verify.llm, "complete", _complete)


def test_verify_writes_evidence_and_the_verdict(monkeypatch: Any, claim: Any) -> None:
    claim_id = claim()
    _patch_calls(
        monkeypatch,
        [{"url": "https://real.test/a", "title": "A", "snippet": "4.1% decrease", "stance": "contradicts"}],
        {"verdict": "refuted", "confidence": 0.82, "rationale": "The city's own filing says 4.1%."},
    )
    verify_claims()
    with get_postgres_session() as session:
        row = session.get(Claim, claim_id)
        assert row is not None
        assert row.verdict == "refuted"
        assert row.confidence == pytest.approx(0.82)
        assert row.verified_at is not None
        evidence = session.scalars(select(Evidence).where(Evidence.claim_id == claim_id)).all()
        assert len(evidence) == 1
        assert evidence[0].stance == "contradicts"


def test_verify_dead_letters_at_the_cap_without_setting_a_verdict(monkeypatch: Any, claim: Any) -> None:
    """A blown call and a genuine "no evidence" are different facts; the row must say which."""
    claim_id = claim(attempts=config.MAX_VERIFY_ATTEMPTS - 1)

    def _boom(**kwargs: Any) -> None:
        raise RuntimeError("provider down")

    monkeypatch.setattr(verify.llm, "complete", _boom)
    verify_claims()
    with get_postgres_session() as session:
        row = session.get(Claim, claim_id)
        assert row is not None
        assert row.attempts == config.MAX_VERIFY_ATTEMPTS
        assert row.verdict is None
        assert "provider down" in (row.error or "")


def test_verify_skips_claims_at_the_attempt_cap(monkeypatch: Any, claim: Any) -> None:
    """A dead-lettered claim must not be re-driven — that is where the money goes.

    Asserted on the row rather than by making the patched call raise: verify_claims()
    sweeps every eligible claim in the table, so a raising patch would also fire on
    rows this test does not own.
    """
    claim_id = claim(attempts=config.MAX_VERIFY_ATTEMPTS, error="provider down")
    _patch_calls(
        monkeypatch,
        [{"url": "https://real.test/a", "snippet": "x", "stance": "supports"}],
        {"verdict": "supported", "confidence": 0.9, "rationale": "Checks out."},
    )
    verify_claims()
    with get_postgres_session() as session:
        row = session.get(Claim, claim_id)
        assert row is not None
        assert row.attempts == config.MAX_VERIFY_ATTEMPTS  # untouched
        assert row.verdict is None
        assert session.scalars(select(Evidence).where(Evidence.claim_id == claim_id)).all() == []


def test_verify_writes_no_evidence_when_every_url_is_ungrounded(monkeypatch: Any, claim: Any) -> None:
    claim_id = claim()

    def _complete(**kwargs: Any) -> llm.LLMResult:
        if kwargs["schema_name"] == "research":
            return llm.LLMResult(
                content=json.dumps(
                    {"evidence": [{"url": "https://invented.test/a", "snippet": "x", "stance": "supports"}]}
                ),
                citation_urls=frozenset({"https://real.test/b"}),
                had_annotations=True,
            )
        return llm.LLMResult(
            content=json.dumps({"verdict": "unverifiable", "confidence": 0.7, "rationale": "Nothing usable."}),
            citation_urls=frozenset(),
            had_annotations=False,
        )

    monkeypatch.setattr(verify.llm, "complete", _complete)
    verify_claims()
    with get_postgres_session() as session:
        assert session.scalars(select(Evidence).where(Evidence.claim_id == claim_id)).all() == []
        row = session.get(Claim, claim_id)
        assert row is not None
        assert row.verdict == "unverifiable"


def test_judge_rejects_an_unknown_verdict(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        verify.llm,
        "complete",
        lambda **kwargs: llm.LLMResult(
            content=json.dumps({"verdict": "probably-ish", "confidence": 0.5, "rationale": "..."}),
            citation_urls=frozenset(),
            had_annotations=False,
        ),
    )
    with pytest.raises(ValueError, match="unknown verdict"):
        verify.judge("A claim.", "the article", [])

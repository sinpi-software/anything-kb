import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

import config
from db import get_postgres_session
from models import Claim, Document, Evidence, Report
from report import render_report, report_documents


def _document(**kwargs: Any) -> Document:
    return Document(url="https://example.com/a", canonical_url="https://example.com/a", title="A headline", **kwargs)


def _claim(**kwargs: Any) -> Claim:
    defaults: dict[str, Any] = {"text": "Crime fell 20%.", "claim_type": "empirical", "checkworthiness": 0.9}
    return Claim(**{**defaults, **kwargs})


def test_render_report_shows_verdict_confidence_and_evidence() -> None:
    # `id` is assigned in memory and never flushed: render_report keys evidence by
    # str(claim.id), so an unpersisted claim needs one to match against.
    claim = _claim(
        id="c1",
        selected_for_verification=True,
        verdict="refuted",
        confidence=0.82,
        rationale="The city's own filing says 4.1%.",
        attributed_to="Mayor Chen",
        attribution_type="quoted_person",
        cited_source="the 2025 city crime report",
    )
    evidence = [Evidence(url="https://real.test/a", title="UCR filing", snippet="4.1% decrease", stance="contradicts")]
    markdown = render_report(_document(), [claim], {"c1": evidence}, generated_at=datetime.now(UTC))
    assert "Crime fell 20%." in markdown
    assert "Refuted" in markdown
    assert "0.82" in markdown
    assert "Mayor Chen" in markdown
    assert "the 2025 city crime report" in markdown
    assert "https://real.test/a" in markdown


def test_render_report_separates_unchecked_claims_by_reason() -> None:
    claims = [
        _claim(text="Costs will rise.", claim_type="predictive"),
        _claim(text="The council acted shamefully.", claim_type="normative"),
        _claim(text="Turnout was low.", checkworthiness=0.1),
        _claim(text="Bonds totalled $4.2M.", selected_for_verification=True, attempts=config.MAX_VERIFY_ATTEMPTS,
               error="provider down"),
        # Clears the floor (0.85 > CHECKWORTHINESS_MIN) but was never selected: crowded
        # out by VERIFY_MAX_PER_DOCUMENT, not too weak. Must not read "below the floor".
        _claim(text="The levy raised $9M.", checkworthiness=0.85),
    ]
    markdown = render_report(_document(), claims, {}, generated_at=datetime.now(UTC))
    assert "predictive" in markdown
    assert "normative" in markdown
    assert "could not be checked" in markdown
    assert "provider down" in markdown
    assert "below the checkworthiness floor (0.10)" in markdown
    assert "not among the" in markdown and "most checkworthy claims" in markdown
    levy_line = next(line for line in markdown.splitlines() if "The levy raised $9M." in line)
    assert "below the checkworthiness floor" not in levy_line


def test_render_report_handles_a_document_with_no_claims() -> None:
    markdown = render_report(_document(), [], {}, generated_at=datetime.now(UTC))
    assert "0 claims extracted" in markdown


@pytest.fixture
def scenario() -> Any:
    document_ids: list[str] = []

    def _make(claims: list[dict[str, Any]], evidence: dict[int, list[dict[str, Any]]] | None = None) -> str:
        with get_postgres_session() as session:
            document = Document(
                url=f"https://example.com/{os.urandom(4).hex()}",
                canonical_url=f"https://example.com/{os.urandom(4).hex()}",
                title="A headline",
                full_text="Body.",
                extracted_at=datetime.now(UTC),
            )
            session.add(document)
            session.flush()
            claim_rows = []
            for spec in claims:
                claim = _claim(document_id=document.id, **spec)
                session.add(claim)
                claim_rows.append(claim)
            session.flush()
            for index, items in (evidence or {}).items():
                for item in items:
                    session.add(Evidence(claim_id=claim_rows[index].id, **item))
            session.commit()
            document_ids.append(str(document.id))
            return str(document.id)

    yield _make

    with get_postgres_session() as session:
        for document_id in document_ids:
            for claim_id in session.scalars(select(Claim.id).where(Claim.document_id == document_id)).all():
                session.execute(delete(Evidence).where(Evidence.claim_id == claim_id))
            session.execute(delete(Report).where(Report.document_id == document_id))
            session.execute(delete(Claim).where(Claim.document_id == document_id))
            session.execute(delete(Document).where(Document.id == document_id))
        session.commit()


def test_report_is_blocked_while_a_claim_is_pending(scenario: Any) -> None:
    document_id = scenario([{"selected_for_verification": True}])  # verdict NULL, attempts 0
    report_documents()
    with get_postgres_session() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.reported_at is None


def test_report_unblocks_once_a_pending_claim_dead_letters(scenario: Any) -> None:
    """A dead-lettered claim is not pending: the report ships and says it could not be checked."""
    document_id = scenario(
        [{"selected_for_verification": True, "attempts": config.MAX_VERIFY_ATTEMPTS, "error": "provider down"}]
    )
    report_documents()
    with get_postgres_session() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.reported_at is not None
        report = session.scalars(select(Report).where(Report.document_id == document_id)).one()
        assert "could not be checked" in (report.body or "")


def test_report_writes_the_row_and_stamps_the_document(scenario: Any) -> None:
    document_id = scenario(
        [{"selected_for_verification": True, "verdict": "supported", "confidence": 0.9, "rationale": "Checks out."}]
    )
    report_documents()
    with get_postgres_session() as session:
        report = session.scalars(select(Report).where(Report.document_id == document_id)).one()
        assert report.claim_count == 1
        assert report.verified_count == 1
        assert report.body
        document = session.get(Document, document_id)
        assert document is not None
        assert document.reported_at is not None


def test_report_includes_persisted_evidence(scenario: Any) -> None:
    """The flow's write side keys evidence_by_claim by str(claim.id): a raw UUID key
    would never match the lookup in render_report, and evidence would silently vanish
    from every report while both gates (mypy, the rest of the suite) stayed green."""
    document_id = scenario(
        [{"selected_for_verification": True, "verdict": "supported", "confidence": 0.9, "rationale": "Checks out."}],
        evidence={0: [{"url": "https://real.test/levy", "title": "County ledger", "stance": "supports"}]},
    )
    report_documents()
    with get_postgres_session() as session:
        report = session.scalars(select(Report).where(Report.document_id == document_id)).one()
        assert "https://real.test/levy" in (report.body or "")
        assert "County ledger" in (report.body or "")


def test_report_does_not_report_the_same_document_twice(scenario: Any) -> None:
    document_id = scenario([{"selected_for_verification": True, "verdict": "supported", "confidence": 0.9}])
    report_documents()
    report_documents()
    with get_postgres_session() as session:
        assert len(session.scalars(select(Report).where(Report.document_id == document_id)).all()) == 1

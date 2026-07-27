import json
import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

import config
import extract
import llm
from db import get_postgres_session
from extract import ExtractedClaim, extract_claims, select_for_verification
from fetch import FetchedPage, NoReadableTextError
from models import Claim, Document


def _claim(claim_type: str, checkworthiness: float, text: str = "A thing.") -> ExtractedClaim:
    return ExtractedClaim(text=text, claim_type=claim_type, checkworthiness=checkworthiness)


def test_gate_selects_only_empirical_claims() -> None:
    claims = [_claim("empirical", 0.9), _claim("predictive", 0.99), _claim("normative", 0.99)]
    assert select_for_verification(claims) == [True, False, False]


def test_gate_applies_the_checkworthiness_floor() -> None:
    below = config.CHECKWORTHINESS_MIN - 0.01
    claims = [_claim("empirical", config.CHECKWORTHINESS_MIN), _claim("empirical", below)]
    assert select_for_verification(claims) == [True, False]


def test_gate_caps_at_verify_max_per_document_keeping_the_most_checkworthy() -> None:
    claims = [_claim("empirical", 0.5 + i / 1000) for i in range(config.VERIFY_MAX_PER_DOCUMENT + 3)]
    selected = select_for_verification(claims)
    assert sum(selected) == config.VERIFY_MAX_PER_DOCUMENT
    # The three least checkworthy are the ones dropped.
    assert selected[:3] == [False, False, False]


def test_gate_is_positional_and_stable() -> None:
    """The returned list lines up with the input, so callers can zip it against rows."""
    claims = [_claim("empirical", 0.9), _claim("opinion", 0.9), _claim("empirical", 0.8)]
    assert len(select_for_verification(claims)) == len(claims)


def test_gate_selects_nothing_when_every_claim_is_weak() -> None:
    claims = [_claim("empirical", 0.1), _claim("empirical", 0.2)]
    assert select_for_verification(claims) == [False, False]


@pytest.fixture
def document() -> Any:
    """A throwaway document, torn down with its claims. `extract_claims()` sweeps every
    unextracted document in the table, so tests must tolerate other rows being visited
    alongside this one rather than assuming an empty table."""
    ids: list[str] = []

    def _make(**kwargs: Any) -> str:
        with get_postgres_session() as session:
            row = Document(
                url=f"https://example.com/{os.urandom(4).hex()}",
                canonical_url=f"https://example.com/{os.urandom(4).hex()}",
                **kwargs,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            ids.append(str(row.id))
            return str(row.id)

    yield _make

    with get_postgres_session() as session:
        for document_id in ids:
            session.execute(delete(Claim).where(Claim.document_id == document_id))
            session.execute(delete(Document).where(Document.id == document_id))
        session.commit()


def _patch_llm(monkeypatch: Any, claims: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        extract.llm,
        "complete",
        lambda **kwargs: llm.LLMResult(
            content=json.dumps({"claims": claims}), citation_urls=frozenset(), had_annotations=False
        ),
    )


def test_extract_writes_claims_and_stamps_the_document(monkeypatch: Any, document: Any) -> None:
    document_id = document(full_text="Body.", fetched_at=datetime.now(UTC))
    _patch_llm(
        monkeypatch,
        [
            {"text": "Crime fell 20%.", "claim_type": "empirical", "checkworthiness": 0.9},
            {"text": "Costs will rise.", "claim_type": "predictive", "checkworthiness": 0.9},
        ],
    )
    extract_claims()
    with get_postgres_session() as session:
        rows = session.scalars(select(Claim).where(Claim.document_id == document_id)).all()
        assert len(rows) == 2
        by_text = {row.text: row for row in rows}
        assert by_text["Crime fell 20%."].selected_for_verification is True
        assert by_text["Costs will rise."].selected_for_verification is False
        row = session.get(Document, document_id)
        assert row is not None
        assert row.extracted_at is not None


def test_extract_leaves_no_claims_when_the_llm_fails(monkeypatch: Any, document: Any) -> None:
    document_id = document(full_text="Body.", fetched_at=datetime.now(UTC))

    def _boom(**kwargs: Any) -> None:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(extract.llm, "complete", _boom)
    extract_claims()
    with get_postgres_session() as session:
        assert session.scalars(select(Claim).where(Claim.document_id == document_id)).all() == []
        row = session.get(Document, document_id)
        assert row is not None
        assert row.extracted_at is None
        assert row.attempts == 1
        assert "model exploded" in (row.error or "")


def test_extract_does_not_refetch_when_full_text_is_present(monkeypatch: Any, document: Any) -> None:
    """Fetching is stamped separately precisely so a failed LLM call is retried cheaply."""
    document_id = document(full_text="Body.", fetched_at=datetime.now(UTC))

    def _never(url: str) -> FetchedPage:
        raise AssertionError("fetch_page must not be called when full_text is present")

    monkeypatch.setattr(extract.fetch, "fetch_page", _never)
    _patch_llm(monkeypatch, [{"text": "A.", "claim_type": "empirical", "checkworthiness": 0.9}])
    extract_claims()
    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.extracted_at is not None


def test_extract_dead_letters_unreadable_pages_immediately(monkeypatch: Any, document: Any) -> None:
    document_id = document()

    def _no_text(url: str) -> FetchedPage:
        raise NoReadableTextError("no readable text")

    monkeypatch.setattr(extract.fetch, "fetch_page", _no_text)
    extract_claims()
    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.attempts == config.MAX_EXTRACT_ATTEMPTS
        assert "no readable text" in (row.error or "")


def test_extract_retries_transient_fetch_failures(monkeypatch: Any, document: Any) -> None:
    document_id = document()

    def _network_error(url: str) -> FetchedPage:
        raise OSError("connection reset")

    monkeypatch.setattr(extract.fetch, "fetch_page", _network_error)
    extract_claims()
    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.attempts == 1  # not dead-lettered — this one is worth retrying

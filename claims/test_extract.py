import json
import os
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from sqlalchemy import delete, select

import config
import extract
import llm
import net_guard
from db import get_postgres_session
from extract import ExtractedClaim, extract_claims, select_for_verification
from fetch import FetchedPage, NoReadableTextError
from models import Claim, Document

_ClaimType = Literal["empirical", "predictive", "normative", "opinion"]


def _claim(claim_type: _ClaimType, checkworthiness: float, text: str = "A thing.") -> ExtractedClaim:
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


def test_extract_treats_a_bare_value_error_as_transient(monkeypatch: Any, document: Any) -> None:
    """Guards against narrowing the dead-letter except clause to `except ValueError`.
    NoReadableTextError and BlockedURLError both subclass ValueError, but a bare
    ValueError (e.g. urlsplit's own parse error) must still be retried, not
    dead-lettered — conflating them would misfile a malformed URL as a content
    failure."""
    document_id = document()

    def _bad_url(url: str) -> FetchedPage:
        raise ValueError("bad url")

    monkeypatch.setattr(extract.fetch, "fetch_page", _bad_url)
    extract_claims()
    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.attempts == 1  # not dead-lettered


def test_extract_retries_host_resolution_failures(monkeypatch: Any, document: Any) -> None:
    """HostResolutionError is transient, unlike BlockedURLError: a name that will not
    resolve now may resolve on the retry, so a momentary DNS blip must not burn a
    document permanently."""
    document_id = document()

    def _dns_fail(url: str) -> FetchedPage:
        raise net_guard.HostResolutionError("could not resolve host")

    monkeypatch.setattr(extract.fetch, "fetch_page", _dns_fail)
    extract_claims()
    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.attempts == 1  # not dead-lettered


class _Crash(BaseException):
    """Not an Exception subclass — simulates the process dying mid-run, distinct from
    a normal component failure that the flow's `except Exception` catches and logs."""


def test_extract_fetch_survives_a_crash_during_the_llm_call(monkeypatch: Any, document: Any) -> None:
    """Fetching commits in its own transaction before the LLM call runs, so even a
    genuine crash mid-extraction (not just a caught exception) leaves the fetched page
    durably saved. Pinned by crashing with a BaseException the flow does not catch:
    if the interim commit were removed, the fetch would live only in the aborted
    transaction and vanish when the session closes on the way out."""
    document_id = document()

    def _fake_fetch(url: str) -> FetchedPage:
        return FetchedPage(title="T", author=None, published_at=None, text="Body.", truncated=False)

    monkeypatch.setattr(extract.fetch, "fetch_page", _fake_fetch)

    def _crash(**kwargs: Any) -> None:
        raise _Crash("simulated crash")

    monkeypatch.setattr(extract.llm, "complete", _crash)

    with pytest.raises(_Crash):
        extract_claims()

    with get_postgres_session() as session:
        row = session.get(Document, document_id)
        assert row is not None
        assert row.full_text == "Body."
        assert row.fetched_at is not None


def test_extract_dead_letters_the_document_when_stamping_it_fails(monkeypatch: Any, document: Any) -> None:
    """Claims and the extracted_at stamp commit together: a failure after the claims
    are staged but before the commit must roll back the whole transaction — never
    leaving orphaned Claim rows for a document that never got its stamp — and, since
    that commit is now guarded the same way verify.py's is, dead-letter the document
    for a retry instead of letting the exception escape and wedge the sweep."""
    document_id = document(full_text="Body.", fetched_at=datetime.now(UTC))
    _patch_llm(monkeypatch, [{"text": "A.", "claim_type": "empirical", "checkworthiness": 0.9}])

    class _BoomClock:
        @staticmethod
        def now(tz: Any = None) -> Any:
            raise RuntimeError("clock exploded")

    monkeypatch.setattr(extract, "datetime", _BoomClock)
    extract_claims()

    with get_postgres_session() as session:
        assert session.scalars(select(Claim).where(Claim.document_id == document_id)).all() == []
        row = session.get(Document, document_id)
        assert row is not None
        assert row.extracted_at is None
        assert row.attempts == 1
        assert "clock exploded" in (row.error or "")


def test_extract_dead_letters_a_document_whose_claim_postgres_rejects(monkeypatch: Any, document: Any) -> None:
    """The wedge the reviewer reproduced: a claim's `text` containing a NUL byte is
    legal JSON (`\\u0000`) but Postgres TEXT refuses it, so the insert raises during
    session.commit(). Without a guard around the claim writes, that exception would
    escape extract_claims() entirely: attempts stays 0 (the document is re-selected
    forever), error stays NULL (invisible to `WHERE error IS NOT NULL`), and — because
    the sweep orders by created_at — this document sits permanently at the head of the
    batch, blocking every document submitted after it. Fails without the guard: the
    RuntimeError-turned-ValueError escapes and this assertion never runs cleanly."""
    document_id = document(full_text="Body.", fetched_at=datetime.now(UTC))
    _patch_llm(monkeypatch, [{"text": "bad claim \x00 text", "claim_type": "empirical", "checkworthiness": 0.9}])

    extract_claims()

    with get_postgres_session() as session:
        assert session.scalars(select(Claim).where(Claim.document_id == document_id)).all() == []
        row = session.get(Document, document_id)
        assert row is not None
        assert row.attempts == 1
        assert row.error is not None
        assert row.extracted_at is None


def test_strict_schema_preserves_the_claim_type_enum() -> None:
    """`enum` is not in llm._UNSUPPORTED_KEYWORDS, so it survives strict_schema and
    OpenAI strict mode enforces claim_type's vocabulary at the API — the assertion
    proving the constraint actually reaches the model, not just pydantic on the way
    back in."""
    schema = llm.strict_schema(ExtractedClaim.model_json_schema())
    assert set(schema["properties"]["claim_type"]["enum"]) == {"empirical", "predictive", "normative", "opinion"}

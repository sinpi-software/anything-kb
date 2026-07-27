"""`extract-claims`: turn a submitted page into attributed claims.

The sweep is "documents not yet extracted, under the attempt cap". Fetching is
stamped separately from extraction, so a document whose *LLM call* failed is retried
without re-fetching the page, while a dead link is fetched once per attempt.

Claims are inserted and `extracted_at` stamped in one commit: a crash mid-extraction
rolls back to "not yet done" rather than leaving half a document's claims behind.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError
from pydantic import BaseModel, Field
from sqlalchemy import select

import config
import fetch as fetch  # re-exported: tests patch extract.fetch.fetch_page
import llm as llm  # re-exported: tests patch extract.llm.complete
import net_guard as net_guard  # re-exported: for BlockedURLError, a dead-letter case
from db import get_postgres_session
from models import Claim, Document

_fallback_logger = logging.getLogger("claims.extract")


def _logger() -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    """The Prefect run logger inside a flow, else a module logger — the helpers below
    are called both from the flow and directly (tests, one-off runs)."""
    try:
        return get_run_logger()
    except MissingContextError:
        return _fallback_logger


class ExtractedClaim(BaseModel):
    text: str
    quote: str | None = None
    attributed_to: str | None = None
    attribution_type: str | None = None
    cited_source: str | None = None
    cited_source_url: str | None = None
    claim_type: str
    checkworthiness: float = Field(ge=0.0, le=1.0)


class Extraction(BaseModel):
    claims: list[ExtractedClaim] = []


_SYSTEM = """You are a fact-checking analyst. Read the document and list every distinct claim it makes.

For each claim:
- `text`: the assertion, rewritten to stand alone without the article. Resolve pronouns and
  relative dates ("last Tuesday" → the actual date, if the document gives it).
- `quote`: the verbatim span the claim came from.
- `attributed_to`: who asserts it — a named person, a named organization, or "the article"
  when the piece asserts it in its own voice.
- `attribution_type`: "quoted_person", "cited_org", or "document_itself".
- `cited_source` / `cited_source_url`: the upstream study, report, dataset or filing the text
  names or links as the origin of the claim. Null unless the document actually names one; do
  not infer where a claim probably came from.
- `claim_type`: "empirical" (a checkable statement about the world, past or present),
  "predictive" (about the future), "normative" (a value judgment), or "opinion".
- `checkworthiness`: 0.0-1.0. High for specific, consequential, falsifiable assertions;
  low for the trivially true, the vague, and the merely decorative.

List claims in the order they appear. Do not invent claims the document does not make."""


def extract_document_claims(text: str, title: str | None) -> Extraction:
    """One LLM call per document."""
    header = f"# {title}\n\n" if title else ""
    result = llm.complete(
        model=config.EXTRACT_MODEL,
        system=_SYSTEM,
        user=f"{header}{text}",
        schema_name="extraction",
        schema=Extraction,
    )
    return Extraction.model_validate(json.loads(result.content))


def select_for_verification(claims: list[ExtractedClaim]) -> list[bool]:
    """The gate, decided once at extraction rather than recomputed at verify time.

    Returns a positional list lining up with `claims`. Empirical claims at or above the
    checkworthiness floor, capped at VERIFY_MAX_PER_DOCUMENT, most checkworthy first.
    Ties break toward the earlier claim, so the result is deterministic for a given input.
    """
    eligible = [
        index
        for index, claim in enumerate(claims)
        if claim.claim_type == config.CHECKABLE_CLAIM_TYPE and claim.checkworthiness >= config.CHECKWORTHINESS_MIN
    ]
    eligible.sort(key=lambda index: (-claims[index].checkworthiness, index))
    chosen = set(eligible[: config.VERIFY_MAX_PER_DOCUMENT])
    return [index in chosen for index in range(len(claims))]


def _fetch_into(document: Document) -> None:
    """Fetch and stamp. Raises NoReadableTextError for the deterministic case."""
    page = fetch.fetch_page(document.url)
    document.title = page.title
    document.author = page.author
    document.published_at = page.published_at
    document.full_text = page.text
    document.fetched_at = datetime.now(UTC)


@flow(name="extract-claims")
def extract_claims() -> dict[str, int]:
    logger = _logger()
    documents_done = 0
    claims_written = 0
    failed = 0

    with get_postgres_session() as session:
        pending = list(
            session.scalars(
                select(Document)
                .where(Document.extracted_at.is_(None), Document.attempts < config.MAX_EXTRACT_ATTEMPTS)
                .order_by(Document.created_at)
                .limit(config.EXTRACT_BATCH_SIZE)
            )
        )

        for document in pending:
            # Fetch first, in its own commit, so a later LLM failure does not re-fetch.
            if document.full_text is None:
                try:
                    _fetch_into(document)
                    session.commit()
                except (fetch.NoReadableTextError, net_guard.BlockedURLError) as exc:
                    # Both are deterministic: a page that yields no text yields none on
                    # the third try either, and a host that resolves to loopback resolves
                    # there again. Dead-letter by exhausting the attempts, which is what
                    # drops the row out of the sweep predicate — there is no separate
                    # "abandoned" flag to keep in sync. Caught by name, never as
                    # ValueError: both subclass it, as does urlsplit's own parse error.
                    document.attempts = config.MAX_EXTRACT_ATTEMPTS
                    document.error = f"{type(exc).__name__}: {exc}"
                    session.commit()
                    failed += 1
                    logger.warning("extract: %s dead-lettered — %s", document.url, exc)
                    continue
                except Exception as exc:  # transient: network, DNS, blocked URL
                    document.attempts += 1
                    document.error = str(exc)[:1000]
                    session.commit()
                    failed += 1
                    logger.warning(
                        "extract: fetch failed for %s (attempt %d): %s", document.url, document.attempts, exc
                    )
                    continue

            try:
                extraction = extract_document_claims(document.full_text or "", document.title)
            except Exception as exc:
                document.attempts += 1
                document.error = str(exc)[:1000]
                session.commit()
                failed += 1
                logger.warning("extract: LLM failed for %s (attempt %d): %s", document.url, document.attempts, exc)
                continue

            selected = select_for_verification(extraction.claims)
            for claim, is_selected in zip(extraction.claims, selected, strict=True):
                session.add(
                    Claim(
                        document_id=document.id,
                        text=claim.text,
                        quote=claim.quote,
                        attributed_to=claim.attributed_to,
                        attribution_type=claim.attribution_type,
                        cited_source=claim.cited_source,
                        cited_source_url=claim.cited_source_url,
                        claim_type=claim.claim_type,
                        checkworthiness=claim.checkworthiness,
                        selected_for_verification=is_selected,
                    )
                )
            # Claims and the stamp commit together: a crash rolls back to "not extracted".
            document.extracted_at = datetime.now(UTC)
            document.error = None
            session.commit()

            documents_done += 1
            claims_written += len(extraction.claims)
            logger.info(
                "extract: %s → %d claims, %d selected for verification",
                document.url,
                len(extraction.claims),
                sum(selected),
            )

    logger.info("extract-claims: %d documents, %d claims, %d failures", documents_done, claims_written, failed)
    return {"documents": documents_done, "claims": claims_written, "failed": failed}


if __name__ == "__main__":
    extract_claims()

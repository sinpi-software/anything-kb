"""`ingest-items`: prepare gathered items and submit them to the engine.

The sweep is "items with no job_id, under the attempt cap". Extraction is stamped
separately from submission, so an item whose *submission* failed is retried without
re-fetching the page, while a dead link is fetched exactly once.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import trafilatura
from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
import engine as engine  # re-exported: tests patch ingest.engine.post_content
import net_guard as net_guard  # re-exported: tests patch ingest.net_guard.fetch
from db import get_postgres_session
from models import Item

_fallback_logger = logging.getLogger("neonews.ingest")


def _logger() -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    """The Prefect run logger inside a flow, else a module logger. `extract_text` and
    `submit` are called both from the flow and directly (tests, one-off runs), and
    get_run_logger() raises outside a run context. Mirrors poll.py's `_logger()`."""
    try:
        return get_run_logger()
    except MissingContextError:
        return _fallback_logger


def extract_text(url: str) -> str | None:
    """Readable page text, fetched through the SSRF guard. None on any failure —
    the caller falls back to the feed's own content."""
    try:
        html = net_guard.fetch(url)
    except Exception as exc:  # a bad link is normal; fall back, don't fail the run
        _logger().warning("extract_text failed for %s: %s", url, exc)
        return None
    extracted = trafilatura.extract(html.decode("utf-8", errors="replace"))
    return extracted or None


def prepare(session: Session, item: Item) -> None:
    """Set `full_text` and stamp `extracted_at` — stamped either way, so a page that
    yields nothing is attempted once rather than on every run."""
    extracted = extract_text(item.url) if item.url else None
    item.full_text = extracted or item.content
    item.extracted_at = datetime.now(UTC)


def _metadata(item: Item) -> dict[str, Any]:
    """What the engine stores on the Source node it creates for this item.
    `worker.py`'s `process_job` reads `meta.get("source", ...)` for the label and
    `meta.get("published_at", ...)` to date the node — both keys must match that
    reader exactly, or every citation the engine produces from neonews content
    renders as "untitled"."""
    metadata: dict[str, Any] = {"source": item.title or item.url or "untitled"}
    if item.url:
        metadata["url"] = item.url
    if item.published_at:
        metadata["published_at"] = item.published_at.isoformat()
    return metadata


def submit(session: Session, item: Item) -> bool:
    """POST the item. Returns True if a job_id was stored; on failure bumps attempts
    and records the error for the next sweep to retry, up to the cap."""
    try:
        item.job_id = engine.post_content(item.full_text or "", _metadata(item))
        item.job_status = "pending"
        item.error = None
        return True
    except Exception as exc:  # recorded and retried, not raised
        item.attempts += 1
        item.error = str(exc)[:500]
        _logger().warning("submit failed for item %s (attempt %d): %s", item.id, item.attempts, exc)
        return False


@flow(name="ingest-items")
def ingest_items() -> dict[str, int]:
    logger = get_run_logger()
    submitted = 0
    with get_postgres_session() as session:
        pending = list(
            session.scalars(
                select(Item)
                .where(Item.job_id.is_(None), Item.attempts < config.MAX_SUBMIT_ATTEMPTS)
                .order_by(Item.created_at)
                .limit(config.INGEST_BATCH_SIZE)
            )
        )
        for item in pending:
            if item.extracted_at is None:
                prepare(session, item)
            if submit(session, item):
                submitted += 1
            session.commit()
        # Dead-lettered items fall out of the WHERE clause above entirely, so without
        # this a pipeline with hundreds of dead-lettered items reports identically to
        # an idle, healthy one. Surfaced, per the design, rather than re-driven forever.
        dead_lettered = (
            session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.job_id.is_(None), Item.attempts >= config.MAX_SUBMIT_ATTEMPTS)
            )
            or 0
        )
    logger.info("ingest: %d considered, %d submitted, %d dead-lettered", len(pending), submitted, dead_lettered)
    return {"considered": len(pending), "submitted": submitted, "dead_lettered": dead_lettered}


if __name__ == "__main__":
    ingest_items()

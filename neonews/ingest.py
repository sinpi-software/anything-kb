"""`ingest-items`: prepare gathered items and submit them to the engine.

The sweep is "items with no job_id, under the attempt cap". Extraction is stamped
separately from submission, so an item whose *submission* failed is retried without
re-fetching the page, while a dead link is fetched exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import trafilatura
from prefect import flow, get_run_logger
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
import engine as engine  # re-exported: tests patch ingest.engine.post_content
import net_guard as net_guard  # re-exported: tests patch ingest.net_guard.fetch
from db import get_postgres_session
from models import Item


def extract_text(url: str) -> str | None:
    """Readable page text, fetched through the SSRF guard. None on any failure —
    the caller falls back to the feed's own content."""
    try:
        html = net_guard.fetch(url)
    except Exception:  # a bad link is normal; fall back, don't fail the run
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
    """What the engine stores on the Source node it creates for this item. It reads
    `published_at` from here to date the node (worker.py), so send it when known."""
    metadata: dict[str, Any] = {"label": item.title or item.url or "untitled"}
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
    logger.info("ingest: %d considered, %d submitted", len(pending), submitted)
    return {"considered": len(pending), "submitted": submitted}


if __name__ == "__main__":
    ingest_items()

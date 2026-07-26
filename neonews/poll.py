"""`poll-sources`: fetch every active source and store new items.

A sweep, not a link in a chain: re-running it is harmless, and anything a previous
run missed is picked up here. Items are inserted with ON CONFLICT DO NOTHING and
only genuinely new rows are counted, so a feed republishing its whole history costs
one query and no duplicates.

There is deliberately no in-run fetch retry: a source that fails is retried by the
next scheduled sweep, and `failure_count` surfaces one that keeps failing. Retrying
inside the run would only shorten that gap, at the cost of holding the session open.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import config
from db import get_postgres_session
from models import Item, Source
from sources import Item as SourceItem
from sources import SourceSpec, fetch_items, load_config

_fallback_logger = logging.getLogger("neonews.poll")


def _logger() -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    """The Prefect run logger inside a flow, else a module logger. `poll_source` is
    called both from the flow and directly (tests, one-off runs), and get_run_logger()
    raises outside a run context."""
    try:
        return get_run_logger()
    except MissingContextError:
        return _fallback_logger


def upsert_sources(session: Session, specs: list[SourceSpec]) -> list[str]:
    """Reconcile neonews.toml into neonews_sources. Returns source ids in spec order."""
    ids = []
    for spec in specs:
        stmt = (
            pg_insert(Source)
            .values(kind=spec.kind, locator=spec.locator, title=spec.title)
            .on_conflict_do_update(
                constraint="neonews_sources_kind_locator",
                set_={"title": spec.title, "active": True},
            )
            .returning(Source.id)
        )
        ids.append(str(session.execute(stmt).scalar_one()))
    return ids


def deactivate_removed_sources(session: Session, specs: list[SourceSpec]) -> int:
    """Config is authoritative: a source no longer declared in neonews.toml stops
    being polled. An UPDATE, not a delete, so an item's history (and a source's
    failure_count) survive if it's added back later. Returns the number turned off."""
    keep = {(spec.kind, spec.locator) for spec in specs}
    deactivated = 0
    for source in session.scalars(select(Source).where(Source.active.is_(True))):
        if (source.kind, source.locator) not in keep:
            source.active = False
            deactivated += 1
    return deactivated


def store_items(session: Session, source_id: str, items: list[SourceItem]) -> list[str]:
    """Insert items, skipping ones already stored for this source. Returns the ids
    actually inserted — conflicts are skipped, so the count is genuinely new work."""
    if not items:
        return []
    stmt = (
        pg_insert(Item)
        .values(
            [
                {
                    "source_id": source_id,
                    "dedup_key": item.dedup_key,
                    "url": item.url,
                    "title": item.title,
                    "content": item.text,
                    "published_at": item.published_at,
                }
                for item in items
            ]
        )
        .on_conflict_do_nothing(constraint="neonews_items_source_dedup")
        .returning(Item.id)
    )
    return [str(row[0]) for row in session.execute(stmt).all()]


def poll_source(session: Session, source: Source) -> int:
    """Poll one source. Never raises: a failure bumps failure_count and returns 0,
    so one bad feed can't derail the run."""
    try:
        discovered_title, items = fetch_items(source.kind, source.locator)
        # An item with no body is nothing to submit, and storing it would burn its
        # dedup key — masking the real item if the feed later fills the body in.
        usable = [item for item in items if item.text and item.text.strip()]
        inserted = store_items(session, str(source.id), usable[: config.POLL_ITEM_LIMIT])
        source.last_polled_at = datetime.now(UTC)
        source.failure_count = 0
        source.title = source.title or discovered_title
        return len(inserted)
    except Exception as exc:  # one bad source shouldn't fail the run
        session.rollback()
        source.failure_count += 1
        session.commit()
        _logger().warning("source %s failed: %s", source.locator, exc)
        return 0


@flow(name="poll-sources")
def poll_sources() -> dict[str, int]:
    logger = get_run_logger()
    specs, _ = load_config(Path(__file__).parent / config.CONFIG_FILE)
    with get_postgres_session() as session:
        upsert_sources(session, specs)
        deactivated = deactivate_removed_sources(session, specs)
        session.commit()
        active = list(session.scalars(select(Source).where(Source.active.is_(True)).order_by(Source.created_at)))
        inserted = 0
        for source in active:
            inserted += poll_source(session, source)
            session.commit()
    logger.info("polled %d sources, %d new items, %d source(s) deactivated", len(active), inserted, deactivated)
    return {"sources": len(active), "inserted": inserted, "deactivated": deactivated}


if __name__ == "__main__":
    poll_sources()

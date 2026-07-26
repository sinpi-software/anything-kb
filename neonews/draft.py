"""`draft-issue`: read what's new in the graph and write the issue.

One GraphQL round-trip gets every source ingested since the watermark, each with its
mentioned entities — both the clustering keys and the writing material. Clusters are
written in parallel and isolated: a cluster whose LLM call fails is dropped with a
warning and the issue ships with the rest.

The watermark advances only after the file is written. A crash therefore re-covers the
same window: a duplicated issue is recoverable by hand, a silently skipped one isn't.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openrouter import OpenRouter
from prefect import flow, get_run_logger, task
from prefect.task_runners import ThreadPoolTaskRunner
from sqlalchemy.orm import Session

import config as config
import engine as engine
from cluster import Cluster, cluster_sources
from db import get_postgres_session
from models import Issue, JobState
from sources import load_config
from write import Story, assemble_issue, render_beat, write_story


def get_watermark(session: Session) -> datetime | None:
    state = session.get(JobState, config.DRAFT_WATERMARK_KEY)
    return state.ran_at if state else None


def set_watermark(session: Session, when: datetime) -> None:
    state = session.get(JobState, config.DRAFT_WATERMARK_KEY)
    if state is None:
        session.add(JobState(key=config.DRAFT_WATERMARK_KEY, ran_at=when))
    else:
        state.ran_at = when


def _llm_client() -> OpenRouter:
    return OpenRouter(api_key=os.environ[config.OPENROUTER_API_KEY_ENV])


def _oldest_ingested_at(rows: list[dict[str, Any]]) -> datetime | None:
    """The oldest `ingestedAt` (falling back to `publishedAt`) among `rows`, for the
    truncation warning below — reporting only, never control flow, so a row with
    neither field just narrows the logged boundary rather than raising. Returns None
    if no row carries a timestamp at all."""
    timestamps = []
    for row in rows:
        raw = row.get("ingestedAt") or row.get("publishedAt")
        if not raw:
            continue
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        timestamps.append(dt)
    return min(timestamps) if timestamps else None


@task(name="write-story")
def write_story_task(beat: str, cluster: Cluster) -> Story:
    return write_story(_llm_client(), beat, cluster)


@flow(name="draft-issue", task_runner=ThreadPoolTaskRunner(max_workers=4))  # type: ignore[arg-type]
def draft_issue() -> dict[str, Any]:
    logger = get_run_logger()
    run_start = datetime.now(UTC)

    with get_postgres_session() as session:
        since = get_watermark(session) or (run_start - timedelta(hours=config.DEFAULT_LOOKBACK_HOURS))
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    _, beat_template = load_config(Path(__file__).parent / config.CONFIG_FILE)
    beat = render_beat(
        beat_template,
        {"issue": {"date": run_start.date().isoformat(), "since": since.date().isoformat()}},
    )

    rows = engine.recent_sources(since.isoformat(), config.SOURCES_QUERY_LIMIT)
    clusters = cluster_sources(rows, config.CLUSTER_MAX_SOURCES)
    logger.info("draft: %d sources since %s → %d clusters", len(rows), since.isoformat(), len(clusters))

    # The engine returns sources newest-first and clamps at SOURCES_QUERY_LIMIT
    # (graph_read.py: `ORDER BY s.ingested_at DESC LIMIT $limit`). A full page means
    # the window held more than the limit: the undrafted remainder is *older* than the
    # oldest source we got back, i.e. strictly older than `since` is about to become.
    # There is no engine-side `until` or ascending order to page through it, so that
    # remainder is simply lost — permanently skipped, never retried. Advancing to
    # run_start (rather than to the oldest source seen) avoids the worse alternative:
    # re-clustering and re-writing the same sources — and paying for the LLM calls
    # again — every truncated run. Make the loss loud instead.
    truncated = len(rows) == config.SOURCES_QUERY_LIMIT
    if truncated:
        oldest_returned = _oldest_ingested_at(rows)
        logger.error(
            "draft: TRUNCATED — %d sources returned (SOURCES_QUERY_LIMIT=%d); the window %s to %s could not "
            "be covered and is now permanently skipped. Raise SOURCES_QUERY_LIMIT or tighten the draft "
            "schedule.",
            len(rows),
            config.SOURCES_QUERY_LIMIT,
            since.isoformat(),
            oldest_returned.isoformat() if oldest_returned else "unknown",
        )

    futures = [(cluster, write_story_task.submit(beat, cluster)) for cluster in clusters]
    stories: list[tuple[Story, Cluster]] = []
    for cluster, future in futures:
        try:
            stories.append((future.result(), cluster))
        except Exception as exc:  # one failed story shouldn't sink the issue
            logger.warning("draft: a cluster failed and was dropped: %s", exc)

    # A genuinely empty window (clusters == 0) is not the same as a non-empty window
    # where every cluster's LLM call failed. Conflating them — writing "No new stories"
    # and advancing anyway — would silently and permanently drop those sources: the
    # exact "skipped window" the module's own docstring calls unrecoverable.
    if clusters and not stories:
        raise RuntimeError(f"draft: all {len(clusters)} clusters failed; window not covered, watermark not advanced")

    markdown = assemble_issue(stories, generated_at=run_start, covers_since=since)
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_start.strftime('%Y-%m-%d-%H%M')}.md"
    path.write_text(markdown, encoding="utf-8")

    # Only now is the window genuinely covered (modulo the truncation loss logged
    # above, which is unrecoverable without engine-side support — see draft.py's
    # module docstring and README.md).
    with get_postgres_session() as session:
        session.add(
            Issue(
                generated_at=run_start,
                covers_since=since,
                path=str(path),
                story_count=len(stories),
            )
        )
        set_watermark(session, run_start)
        session.commit()

    logger.info("draft: wrote %d stories to %s", len(stories), path)
    return {
        "path": str(path),
        "clusters": len(clusters),
        "stories": len(stories),
        "since": since.isoformat(),
        "truncated": truncated,
    }


if __name__ == "__main__":
    draft_issue()

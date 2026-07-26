"""Deployment registration for when neonews is deployed.

Every flow is also runnable directly (`uv run python poll.py`) with no Prefect server —
each is a sweep over durable state, so nothing here is required for the pipeline to work.
This module only adds schedules.

Schedules live in code: serve() reconciles deployments on restart, so a schedule set
only in the Prefect UI is overwritten on the next deploy.
"""

from __future__ import annotations

from os import getenv
from typing import Any

from prefect import serve
from prefect.client.schemas.schedules import CronSchedule

import config
from draft import draft_issue
from ingest import ingest_items
from jobs import check_jobs
from poll import poll_sources

SCHEDULE_TZ = getenv(config.SCHEDULE_TZ_ENV, config.SCHEDULE_TZ_DEFAULT)
POLL_CRON = getenv(config.POLL_CRON_ENV, config.POLL_CRON_DEFAULT)
INGEST_CRON = getenv(config.INGEST_CRON_ENV, config.INGEST_CRON_DEFAULT)
JOBS_CRON = getenv(config.JOBS_CRON_ENV, config.JOBS_CRON_DEFAULT)
DRAFT_CRON = getenv(config.DRAFT_CRON_ENV, config.DRAFT_CRON_DEFAULT)
# Flow runs executed concurrently. Each run is a subprocess.
SERVE_CONCURRENCY = int(getenv(config.SERVE_CONCURRENCY_ENV, str(config.SERVE_CONCURRENCY_DEFAULT)))


def _schedules(cron: str) -> list[CronSchedule]:
    """Empty cron means register the deployment with no schedule — how local
    development gets the flows into the UI without them firing on their own."""
    return [CronSchedule(cron=cron, timezone=SCHEDULE_TZ)] if cron else []


def deployments() -> list[Any]:
    return [
        poll_sources.to_deployment(name="poll-sources", schedules=_schedules(POLL_CRON)),
        ingest_items.to_deployment(name="ingest-items", schedules=_schedules(INGEST_CRON)),
        check_jobs.to_deployment(name="check-jobs", schedules=_schedules(JOBS_CRON)),
        draft_issue.to_deployment(name="draft-issue", schedules=_schedules(DRAFT_CRON)),
    ]


if __name__ == "__main__":
    serve(*deployments(), limit=SERVE_CONCURRENCY)

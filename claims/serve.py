"""Deployment registration for when claims is deployed.

Every flow is also runnable directly (`uv run python extract.py`) with no Prefect
server — each is a sweep over durable state, so nothing here is required for the
pipeline to work. This module only adds schedules.

Schedules live in code: serve() reconciles deployments on restart, so a schedule set
only in the Prefect UI is overwritten on the next deploy.
"""

from __future__ import annotations

from os import getenv
from typing import Any

from prefect import serve
from prefect.client.schemas.schedules import CronSchedule

import config
from extract import extract_claims
from report import report_documents
from submit import submit_url
from verify import verify_claims

SCHEDULE_TZ = getenv(config.SCHEDULE_TZ_ENV, config.SCHEDULE_TZ_DEFAULT)
EXTRACT_CRON = getenv(config.EXTRACT_CRON_ENV, config.EXTRACT_CRON_DEFAULT)
VERIFY_CRON = getenv(config.VERIFY_CRON_ENV, config.VERIFY_CRON_DEFAULT)
REPORT_CRON = getenv(config.REPORT_CRON_ENV, config.REPORT_CRON_DEFAULT)
SERVE_CONCURRENCY = int(getenv(config.SERVE_CONCURRENCY_ENV, str(config.SERVE_CONCURRENCY_DEFAULT)))


def _schedules(cron: str) -> list[CronSchedule]:
    """Empty cron means register the deployment with no schedule — how local
    development gets the flows into the UI without them firing on their own."""
    return [CronSchedule(cron=cron, timezone=SCHEDULE_TZ)] if cron else []


def deployments() -> list[Any]:
    return [
        # submit-url takes a URL: there is nothing for a cron to submit. Trigger it
        # from the UI with a parameter, or run `uv run python submit.py <url>`.
        submit_url.to_deployment(name="submit-url"),
        # concurrency_limit=1: each sweep is a batch against a schedule that does not
        # bound how long a run takes, so overlap is expected, not exotic. Two
        # overlapping extract runs would duplicate every claim of a document (no
        # unique constraint on claims_claims); two overlapping verify runs would spend
        # twice the LLM calls the batch size budgeted for.
        extract_claims.to_deployment(name="extract-claims", schedules=_schedules(EXTRACT_CRON), concurrency_limit=1),
        verify_claims.to_deployment(name="verify-claims", schedules=_schedules(VERIFY_CRON), concurrency_limit=1),
        report_documents.to_deployment(
            name="report-documents", schedules=_schedules(REPORT_CRON), concurrency_limit=1
        ),
    ]


if __name__ == "__main__":
    serve(*deployments(), limit=SERVE_CONCURRENCY)

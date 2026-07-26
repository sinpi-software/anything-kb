"""`check-jobs`: record the engine's verdict on submitted items.

The engine's worker is asynchronous, so this is an interval sweep over items whose
job_status isn't terminal. `skipped` (judged not relevant) and `failed` are terminal
and normal — that verdict is the engine's to make, and neonews never retries it.
"""

from __future__ import annotations

from prefect import flow, get_run_logger
from sqlalchemy import or_, select

import config as config  # re-exported: tests patch jobs.config.JOBS_BATCH_SIZE
import engine
from db import get_postgres_session
from models import Item


@flow(name="check-jobs")
def check_jobs() -> dict[str, int]:
    logger = get_run_logger()
    checked = 0
    resolved = 0
    with get_postgres_session() as session:
        outstanding = list(
            session.scalars(
                select(Item)
                .where(
                    Item.job_id.is_not(None),
                    or_(Item.job_status.is_(None), Item.job_status.notin_(config.TERMINAL_JOB_STATUSES)),
                )
                .order_by(Item.created_at)
                .limit(config.JOBS_BATCH_SIZE)
            )
        )
        for item in outstanding:
            checked += 1
            try:
                status = str(engine.job_status(str(item.job_id)).get("status") or "")
            except Exception as exc:  # one unreachable job shouldn't sink the sweep
                logger.warning("job %s status check failed: %s", item.job_id, exc)
                continue
            if status:
                item.job_status = status
                if status in config.TERMINAL_JOB_STATUSES:
                    resolved += 1
            session.commit()
    logger.info("check-jobs: %d checked, %d reached a terminal state", checked, resolved)
    return {"checked": checked, "resolved": resolved}


if __name__ == "__main__":
    check_jobs()

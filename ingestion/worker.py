import logging
import os
import time as time  # re-exported so tests can monkeypatch worker.time.sleep
from datetime import UTC, datetime

import dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

import config as config  # re-exported so tests can monkeypatch worker.config.WORKER_*
from db import get_postgres_session
from knowledge import merge_content
from models import IngestJob, JobStatus, KnowledgeBaseConfig
from neo4j_client import bootstrap_schema
from relevance import judge_relevance

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")


def claim_pending_job_ids(session: Session, batch_size: int) -> list[str]:
    jobs = (
        session.execute(
            select(IngestJob)
            .where(IngestJob.status == JobStatus.PENDING.value)
            .order_by(IngestJob.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    ids = []
    for job in jobs:
        job.status = JobStatus.PROCESSING.value
        ids.append(str(job.id))
    session.commit()
    return ids


def process_job(job_id: str) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        knowledge_base_id = str(job.knowledge_base_id)
        content = job.content
        cfg = (
            session.query(KnowledgeBaseConfig)
            .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id)
            .one_or_none()
        )
        interests = cfg.interests if cfg else ""
        discover = bool(cfg.discover_types) if cfg else False
        entity_types = list(cfg.entity_types) if cfg else []
        relationship_types = list(cfg.relationship_types) if cfg else []
        source_label = (job.job_metadata or {}).get("source", "") if job.job_metadata else ""
        source_date = job.created_at.isoformat() if job.created_at else ""

    try:
        verdict = judge_relevance(interests, content)
        if not verdict.relevant:
            _finalize(job_id, JobStatus.SKIPPED, relevance_reason=verdict.reason)
            return
        result = merge_content(
            knowledge_base_id,
            content,
            entity_types,
            relationship_types,
            job_id,
            interests=interests,
            discover=discover,
            source_label=source_label,
            source_date=source_date,
        )
        _persist_new_types(knowledge_base_id, result.new_entity_types, result.new_relationship_types)
        _finalize(job_id, JobStatus.DONE, relevance_reason=verdict.reason)
    except Exception as exc:
        _record_failure(job_id, str(exc))


def _persist_new_types(
    knowledge_base_id: str, new_entities: list[dict[str, str]], new_rels: list[dict[str, str]]
) -> None:
    if not new_entities and not new_rels:
        return
    from knowledge import _norm_type

    with get_postgres_session() as session:
        cfg = (
            session.query(KnowledgeBaseConfig)
            .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id)
            .one_or_none()
        )
        if cfg is None:
            return

        def merged(existing: list[dict[str, str]], additions: list[dict[str, str]]) -> list[dict[str, str]]:
            seen = {_norm_type(t["name"]) for t in existing}
            out = list(existing)
            for t in additions:
                if _norm_type(t["name"]) not in seen:
                    seen.add(_norm_type(t["name"]))
                    out.append({"name": t["name"], "description": t.get("description", "")})
            return out

        cfg.entity_types = merged(cfg.entity_types, new_entities)
        cfg.relationship_types = merged(cfg.relationship_types, new_rels)
        session.commit()


def _finalize(job_id: str, status: JobStatus, relevance_reason: str | None = None) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        job.status = status.value
        job.relevance_reason = relevance_reason
        job.error = None
        job.processed_at = datetime.now(UTC)
        session.commit()


def _record_failure(job_id: str, error: str) -> None:
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return
        job.attempts += 1
        job.error = error
        # Retry (back to pending) while under the cap; otherwise stay failed.
        if job.attempts < config.WORKER_MAX_ATTEMPTS:
            job.status = JobStatus.PENDING.value
        else:
            job.status = JobStatus.FAILED.value
            job.processed_at = datetime.now(UTC)
        session.commit()


def run_once() -> int:
    with get_postgres_session() as session:
        job_ids = claim_pending_job_ids(session, config.WORKER_BATCH_SIZE)
    for job_id in job_ids:
        process_job(job_id)
    return len(job_ids)


def main() -> None:
    bootstrap_schema()
    while True:
        try:
            processed = run_once()
        except Exception:
            logging.getLogger("worker").exception("worker iteration failed; continuing")
            processed = 0
        if processed == 0:
            time.sleep(config.WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

"""One-off operator backfill: run once against prod (`kubectl exec` into the API pod) after the
wiki-grade-articles rollout. Existing Entity nodes have `summary` but no `article`; existing
Source nodes have no `label`/`date`. This promotes the old summary to `article` and derives a
short `summary` from it, and labels sources from their Postgres `ingest_jobs` row.
"""

import os

import dotenv

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")


def backfill_entity_articles() -> int:
    """For every Entity with a summary but no article, promote the summary to `article` and
    derive a fresh, short `summary` from it (in one SET, so no half-migrated node is ever read)."""
    from knowledge import _derive_abstract
    from neo4j_client import get_neo4j_session

    count = 0
    with get_neo4j_session() as session:
        rows = list(
            session.run(
                "MATCH (e:Entity) WHERE e.article IS NULL AND e.summary IS NOT NULL "
                "RETURN e.id AS id, e.summary AS summary"
            )
        )
        for row in rows:
            summary = row["summary"]
            session.run(
                "MATCH (e:Entity {id: $id}) SET e.article = $article, e.summary = $summary",
                {"id": row["id"], "article": summary, "summary": _derive_abstract(summary)},
            )
            count += 1
    return count


def _lookup_job_label_and_date(job_id: str) -> tuple[str, str]:
    """Look up a job's source label and date in Postgres. Returns ("", "") if the job is gone
    or was never given a `metadata.source`."""
    from db import get_postgres_session
    from models import IngestJob

    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return "", ""
        label = (job.job_metadata or {}).get("source", "") if job.job_metadata else ""
        date = job.created_at.isoformat() if job.created_at else ""
        return label, date


def backfill_source_labels() -> int:
    """For every Source with no label, resolve it from its originating ingest_jobs row and
    write `label`/`date` (empty strings when the job is gone or unlabeled)."""
    from neo4j_client import get_neo4j_session

    count = 0
    with get_neo4j_session() as session:
        job_ids = [
            row["job_id"] for row in session.run("MATCH (s:Source) WHERE s.label IS NULL RETURN s.job_id AS job_id")
        ]

    for job_id in job_ids:
        label, date = _lookup_job_label_and_date(job_id)
        with get_neo4j_session() as session:
            session.run(
                "MATCH (s:Source {job_id: $job_id}) SET s.label = $label, s.date = $date",
                {"job_id": job_id, "label": label, "date": date},
            )
        count += 1
    return count


def main() -> None:
    entities = backfill_entity_articles()
    sources = backfill_source_labels()
    print(f"entities backfilled: {entities}")
    print(f"sources labeled: {sources}")


if __name__ == "__main__":
    main()

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_knowledge_base
from db import get_postgres_session
from models import IngestJob
from sanitize import sanitize
from schemas import ContentAccepted, ContentRequest, JobStatusResponse

router = APIRouter()


def _sanitize_json(value: Any) -> Any:
    """Recursively apply `sanitize` to every string in a JSON-like structure."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {k: _sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(v) for v in value]
    return value


@router.post(
    "/content",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ContentAccepted,
    tags=["Content"],
    summary="Submit content for ingestion",
    responses={401: {"description": "Missing or invalid API key"}},
)
def post_content(body: ContentRequest, knowledge_base_id: str = Depends(require_knowledge_base)) -> ContentAccepted:
    """Queue text for relevance filtering and knowledge extraction.

    Returns immediately with a `job_id` (HTTP 202); processing happens asynchronously.
    Poll `GET /content/{job_id}` for the outcome.
    """
    with get_postgres_session() as session:
        job = IngestJob(
            knowledge_base_id=knowledge_base_id,
            content=sanitize(body.text),
            job_metadata=_sanitize_json(body.metadata),
        )
        session.add(job)
        session.flush()
        job_id = str(job.id)
        session.commit()
    return ContentAccepted(job_id=job_id)


@router.get(
    "/content/{job_id}",
    response_model=JobStatusResponse,
    tags=["Content"],
    summary="Check ingestion status",
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"description": "No such job for this knowledge_base"},
    },
)
def get_content(job_id: str, knowledge_base_id: str = Depends(require_knowledge_base)) -> JobStatusResponse:
    """Return a submitted item's status: `pending`, `processing`, `done`, `skipped`
    (judged not relevant), or `failed`. Only the owning knowledge_base can read a job."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from None
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None or str(job.knowledge_base_id) != knowledge_base_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            relevance_reason=job.relevance_reason,
            error=job.error,
        )

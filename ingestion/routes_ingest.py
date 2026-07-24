"""Session-authenticated ingestion for the dashboard UI (cookie auth via `current_user`).

Writes the same `IngestJob` the Bearer `/content` route does, so a pasted block flows
through the identical worker pipeline (relevance → extraction → Neo4j). The logged-in UI
never has to handle an API key — the knowledge base is resolved from the user's session."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from accounts import current_user, home_knowledge_base_id, require_csrf
from db import get_postgres_session
from models import IngestJob, User
from sanitize import sanitize, sanitize_json
from schemas import ContentAccepted, ContentRequest, JobStatusResponse

router = APIRouter(prefix="/api/content", tags=["Content"], dependencies=[Depends(require_csrf)])


@router.post("", response_model=ContentAccepted, status_code=status.HTTP_202_ACCEPTED)
def ingest_content(
    body: ContentRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ContentAccepted:
    """Queue pasted text for ingestion into the caller's knowledge base."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to ingest content")
    with get_postgres_session() as session:
        knowledge_base_id = home_knowledge_base_id(session, user.id)
        if knowledge_base_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no knowledge base found for this account"
            )
        job = IngestJob(
            knowledge_base_id=knowledge_base_id,
            content=sanitize(body.text),
            job_metadata=sanitize_json(body.metadata),
        )
        session.add(job)
        session.flush()
        job_id = str(job.id)
        session.commit()
    return ContentAccepted(job_id=job_id)


@router.get("/{job_id}", response_model=JobStatusResponse)
def job_status(
    job_id: str,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> JobStatusResponse:
    """Poll a submitted item's outcome. Only the owning knowledge base can read a job."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from None
    with get_postgres_session() as session:
        knowledge_base_id = home_knowledge_base_id(session, user.id)
        job = session.get(IngestJob, job_id) if knowledge_base_id is not None else None
        if job is None or str(job.knowledge_base_id) != knowledge_base_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            relevance_reason=job.relevance_reason,
            error=job.error,
        )

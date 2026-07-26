"""Session-authenticated ingestion for the dashboard UI (cookie auth via `current_user`).

Writes the same `IngestJob` the Bearer `/content` route does, so a pasted block flows
through the identical worker pipeline (relevance → extraction → Neo4j). The logged-in UI
never has to handle an API key — the knowledge base is resolved from the user's session."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession

from accounts import current_user, home_knowledge_base_id, require_csrf
from db import get_postgres_session
from memberships import require_membership
from models import IngestJob, User
from sanitize import sanitize, sanitize_json
from schemas import ContentAccepted, ContentRequest, JobStatusResponse

router = APIRouter(prefix="/api/content", tags=["Content"], dependencies=[Depends(require_csrf)])
scoped_router = APIRouter(prefix="/api/knowledge-bases", tags=["Content"], dependencies=[Depends(require_csrf)])


def _ingest_content(session: OrmSession, kb_id: str, body: ContentRequest, user: User) -> ContentAccepted:
    job = IngestJob(
        knowledge_base_id=kb_id,
        content=sanitize(body.text),
        job_metadata=sanitize_json(body.metadata),
    )
    session.add(job)
    session.flush()
    job_id = str(job.id)
    session.commit()
    return ContentAccepted(job_id=job_id)


@router.post("", response_model=ContentAccepted, status_code=status.HTTP_202_ACCEPTED)
def ingest_content(
    body: ContentRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ContentAccepted:
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to ingest content")
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "editor")
        assert kb_id is not None  # require_membership already 404s a None kb_id
        return _ingest_content(session, kb_id, body, user)


@scoped_router.post("/{kb_id}/content", response_model=ContentAccepted, status_code=status.HTTP_202_ACCEPTED)
def ingest_content_scoped(
    kb_id: str,
    body: ContentRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ContentAccepted:
    """Queue pasted text for ingestion into the given knowledge base."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to ingest content")
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "editor")
        return _ingest_content(session, kb_id, body, user)


def _job_status(session: OrmSession, kb_id: str, job_id: str) -> JobStatusResponse:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from None
    job = session.get(IngestJob, job_id)
    if job is None or str(job.knowledge_base_id) != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        relevance_reason=job.relevance_reason,
        error=job.error,
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
def job_status(
    job_id: str,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> JobStatusResponse:
    """Legacy: the knowledge base is implied. Sub-project B removes this."""
    with get_postgres_session() as session:
        kb_id = home_knowledge_base_id(session, user.id)
        require_membership(session, user.id, kb_id, "reader")
        assert kb_id is not None  # require_membership already 404s a None kb_id
        return _job_status(session, kb_id, job_id)


@scoped_router.get("/{kb_id}/content/{job_id}", response_model=JobStatusResponse)
def job_status_scoped(
    kb_id: str,
    job_id: str,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> JobStatusResponse:
    """Poll a submitted item's outcome. Only the owning knowledge base can read a job."""
    with get_postgres_session() as session:
        require_membership(session, user.id, kb_id, "reader")
        return _job_status(session, kb_id, job_id)

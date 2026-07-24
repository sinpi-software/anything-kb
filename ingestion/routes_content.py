import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_org
from db import get_postgres_session
from models import IngestJob
from schemas import ContentAccepted, ContentRequest, JobStatusResponse

router = APIRouter()


@router.post("/content", status_code=status.HTTP_202_ACCEPTED, response_model=ContentAccepted)
def post_content(body: ContentRequest, org_id: str = Depends(require_org)) -> ContentAccepted:
    with get_postgres_session() as session:
        job = IngestJob(org_id=org_id, content=body.text, job_metadata=body.metadata)
        session.add(job)
        session.flush()
        job_id = str(job.id)
        session.commit()
    return ContentAccepted(job_id=job_id)


@router.get("/content/{job_id}", response_model=JobStatusResponse)
def get_content(job_id: str, org_id: str = Depends(require_org)) -> JobStatusResponse:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from None
    with get_postgres_session() as session:
        job = session.get(IngestJob, job_id)
        if job is None or str(job.org_id) != org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            relevance_reason=job.relevance_reason,
            error=job.error,
        )

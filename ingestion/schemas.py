from typing import Any

from pydantic import BaseModel


class ContentRequest(BaseModel):
    text: str
    metadata: dict[str, Any] | None = None


class ContentAccepted(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    relevance_reason: str | None = None
    error: str | None = None


class ConfigRequest(BaseModel):
    relevance_prompt: str
    entity_types: list[str]
    relationship_types: list[str]


class ConfigResponse(BaseModel):
    org_id: str
    relevance_prompt: str
    entity_types: list[str]
    relationship_types: list[str]

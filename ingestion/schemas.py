from datetime import datetime
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


# --- accounts (email/password auth) ----------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str | None = None
    # Name for the org auto-created for this user. Defaults to "My workspace".
    org_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class OrgMembership(BaseModel):
    org_id: str
    org_name: str
    role: str


class MeResponse(BaseModel):
    id: str
    email: str
    name: str | None
    email_verified: bool
    is_admin: bool
    orgs: list[OrgMembership]


# --- API keys ----------------------------------------------------------------


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str | None
    prefix: str
    # The raw key. Shown exactly once — it is never persisted or retrievable again.
    key: str
    created_at: datetime


class ApiKeyOut(BaseModel):
    id: str
    name: str | None
    prefix: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

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


class TypeDef(BaseModel):
    """An entity or relationship type. The description guides the extractor on what
    this type means in the user's domain; it's optional."""

    name: str
    description: str = ""
    pinned: bool = False
    banned: bool = False


class ConfigRequest(BaseModel):
    interests: str
    discover_types: bool = True
    entity_types: list[TypeDef]
    relationship_types: list[TypeDef]


class ConfigResponse(BaseModel):
    knowledge_base_id: str
    interests: str
    discover_types: bool
    entity_types: list[TypeDef]
    relationship_types: list[TypeDef]


# --- accounts (email/password auth) ----------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str | None = None
    # Name for the knowledge_base auto-created for this user. Defaults to "My workspace".
    knowledge_base_name: str | None = None


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


class KnowledgeBaseMembership(BaseModel):
    knowledge_base_id: str
    knowledge_base_name: str
    role: str


class MeResponse(BaseModel):
    id: str
    email: str
    name: str | None
    email_verified: bool
    is_admin: bool
    knowledge_bases: list[KnowledgeBaseMembership]


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


# --- knowledge bases -----------------------------------------------------------


class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    charter: str | None
    role: str
    created_at: datetime


class KnowledgeBaseCreateRequest(BaseModel):
    name: str
    charter: str | None = None


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = None
    charter: str | None = None


class KnowledgeBaseDeleteRequest(BaseModel):
    confirm_name: str

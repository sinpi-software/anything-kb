"""Session-authenticated knowledge-base configuration for the dashboard UI (cookie auth).

Reads/writes the same `KnowledgeBaseConfig` the Bearer `PUT /config` route does, so the
relevance prompt and entity/relationship types set here apply to every subsequent ingest."""

from fastapi import APIRouter, Depends, HTTPException, status

from accounts import current_user, home_knowledge_base_id, require_csrf
from db import get_postgres_session
from models import KnowledgeBaseConfig, User
from sanitize import sanitize
from schemas import ConfigRequest, ConfigResponse

router = APIRouter(prefix="/api/config", tags=["Configuration"], dependencies=[Depends(require_csrf)])


def _clean_types(values: list[str]) -> list[str]:
    """Sanitize, trim, and drop blanks — the UI can submit empty chips."""
    return [cleaned for v in values if (cleaned := sanitize(v).strip())]


@router.get("", response_model=ConfigResponse)
def get_config(user: User = Depends(current_user)) -> ConfigResponse:  # noqa: B008 — FastAPI dependency idiom
    """The caller's current relevance prompt and entity/relationship types (empty if unset)."""
    with get_postgres_session() as session:
        knowledge_base_id = home_knowledge_base_id(session, user.id)
        if knowledge_base_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no knowledge base found for this account"
            )
        cfg = (
            session.query(KnowledgeBaseConfig)
            .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id)
            .one_or_none()
        )
        return ConfigResponse(
            knowledge_base_id=knowledge_base_id,
            relevance_prompt=cfg.relevance_prompt if cfg else "",
            entity_types=list(cfg.entity_types) if cfg else [],
            relationship_types=list(cfg.relationship_types) if cfg else [],
        )


@router.put("", response_model=ConfigResponse)
def put_config(
    body: ConfigRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ConfigResponse:
    """Upsert the relevance prompt and the entity/relationship types the graph captures."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to edit configuration")
    relevance_prompt = sanitize(body.relevance_prompt).strip()
    entity_types = _clean_types(body.entity_types)
    relationship_types = _clean_types(body.relationship_types)
    with get_postgres_session() as session:
        knowledge_base_id = home_knowledge_base_id(session, user.id)
        if knowledge_base_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="no knowledge base found for this account"
            )
        cfg = (
            session.query(KnowledgeBaseConfig)
            .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id)
            .one_or_none()
        )
        if cfg is None:
            cfg = KnowledgeBaseConfig(
                knowledge_base_id=knowledge_base_id,
                relevance_prompt=relevance_prompt,
                entity_types=entity_types,
                relationship_types=relationship_types,
            )
            session.add(cfg)
        else:
            cfg.relevance_prompt = relevance_prompt
            cfg.entity_types = entity_types
            cfg.relationship_types = relationship_types
        session.commit()
    return ConfigResponse(
        knowledge_base_id=knowledge_base_id,
        relevance_prompt=relevance_prompt,
        entity_types=entity_types,
        relationship_types=relationship_types,
    )

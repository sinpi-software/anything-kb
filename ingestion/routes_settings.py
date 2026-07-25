"""Session-authenticated knowledge-base configuration for the dashboard UI (cookie auth).

Reads/writes the same `KnowledgeBaseConfig` the Bearer `PUT /config` route does, so the
interests and entity/relationship types set here apply to every subsequent ingest."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from accounts import current_user, home_knowledge_base_id, require_csrf
from db import get_postgres_session
from models import KnowledgeBaseConfig, User
from sanitize import sanitize
from schemas import ConfigRequest, ConfigResponse, TypeDef

router = APIRouter(prefix="/api/config", tags=["Configuration"], dependencies=[Depends(require_csrf)])


def _clean_types(values: list[TypeDef]) -> list[dict[str, Any]]:
    """Sanitize name + description; drop entries with a blank name (the UI can submit empty rows).
    Preserves pinned/banned (stored alongside name/description in the same JSONB dict)."""
    cleaned: list[dict[str, Any]] = []
    for t in values:
        name = sanitize(t.name).strip()
        if name:
            cleaned.append(
                {"name": name, "description": sanitize(t.description).strip(), "pinned": t.pinned, "banned": t.banned}
            )
    return cleaned


@router.get("", response_model=ConfigResponse)
def get_config(user: User = Depends(current_user)) -> ConfigResponse:  # noqa: B008 — FastAPI dependency idiom
    """The caller's current interests and entity/relationship types (empty if unset)."""
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
            interests=cfg.interests if cfg else "",
            discover_types=cfg.discover_types if cfg else True,
            entity_types=[TypeDef.model_validate(t) for t in cfg.entity_types] if cfg else [],
            relationship_types=[TypeDef.model_validate(t) for t in cfg.relationship_types] if cfg else [],
        )


@router.put("", response_model=ConfigResponse)
def put_config(
    body: ConfigRequest,
    user: User = Depends(current_user),  # noqa: B008 — FastAPI dependency idiom
) -> ConfigResponse:
    """Upsert the interests and the entity/relationship types the graph captures."""
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify your email to edit configuration")
    interests = sanitize(body.interests).strip()
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
                interests=interests,
                discover_types=body.discover_types,
                entity_types=entity_types,
                relationship_types=relationship_types,
            )
            session.add(cfg)
        else:
            cfg.interests = interests
            cfg.discover_types = body.discover_types
            cfg.entity_types = entity_types
            cfg.relationship_types = relationship_types
        session.commit()
    return ConfigResponse(
        knowledge_base_id=knowledge_base_id,
        interests=interests,
        discover_types=body.discover_types,
        entity_types=[TypeDef.model_validate(t) for t in entity_types],
        relationship_types=[TypeDef.model_validate(t) for t in relationship_types],
    )

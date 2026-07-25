from fastapi import APIRouter, Depends

from auth import require_knowledge_base
from db import get_postgres_session
from models import KnowledgeBaseConfig
from schemas import ConfigRequest, ConfigResponse

router = APIRouter()


@router.put(
    "/config",
    response_model=ConfigResponse,
    tags=["Configuration"],
    summary="Set your extraction config",
    responses={401: {"description": "Missing or invalid API key"}},
)
def put_config(body: ConfigRequest, knowledge_base_id: str = Depends(require_knowledge_base)) -> ConfigResponse:
    """Upsert your knowledge_base's interests and the entity / relationship types the graph
    captures. Applies to content ingested after the change."""
    entity_types = [t.model_dump() for t in body.entity_types]
    relationship_types = [t.model_dump() for t in body.relationship_types]
    with get_postgres_session() as session:
        cfg = (
            session.query(KnowledgeBaseConfig)
            .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base_id)
            .one_or_none()
        )
        if cfg is None:
            cfg = KnowledgeBaseConfig(
                knowledge_base_id=knowledge_base_id,
                interests=body.interests,
                discover_types=body.discover_types,
                entity_types=entity_types,
                relationship_types=relationship_types,
            )
            session.add(cfg)
        else:
            cfg.interests = body.interests
            cfg.discover_types = body.discover_types
            cfg.entity_types = entity_types
            cfg.relationship_types = relationship_types
        session.commit()
    return ConfigResponse(
        knowledge_base_id=knowledge_base_id,
        interests=body.interests,
        discover_types=body.discover_types,
        entity_types=body.entity_types,
        relationship_types=body.relationship_types,
    )

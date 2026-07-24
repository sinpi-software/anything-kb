from fastapi import APIRouter, Depends

from auth import require_org
from db import get_postgres_session
from models import OrgConfig
from schemas import ConfigRequest, ConfigResponse

router = APIRouter()


@router.put(
    "/config",
    response_model=ConfigResponse,
    tags=["Configuration"],
    summary="Set your extraction config",
    responses={401: {"description": "Missing or invalid API key"}},
)
def put_config(body: ConfigRequest, org_id: str = Depends(require_org)) -> ConfigResponse:
    """Upsert your org's relevance prompt and the entity / relationship types the graph
    captures. Applies to content ingested after the change."""
    with get_postgres_session() as session:
        cfg = session.query(OrgConfig).filter(OrgConfig.org_id == org_id).one_or_none()
        if cfg is None:
            cfg = OrgConfig(
                org_id=org_id,
                relevance_prompt=body.relevance_prompt,
                entity_types=body.entity_types,
                relationship_types=body.relationship_types,
            )
            session.add(cfg)
        else:
            cfg.relevance_prompt = body.relevance_prompt
            cfg.entity_types = body.entity_types
            cfg.relationship_types = body.relationship_types
        session.commit()
    return ConfigResponse(
        org_id=org_id,
        relevance_prompt=body.relevance_prompt,
        entity_types=body.entity_types,
        relationship_types=body.relationship_types,
    )

import os

os.environ.setdefault("INGESTION_POSTGRES_URL", "postgresql://ingestion:ingestion@localhost:5432/ingestion")
os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

from main import app


def test_app_exposes_all_routes() -> None:
    # FastAPI's `include_router` no longer eagerly flattens routes onto
    # `app.router.routes` (they show up as opaque `_IncludedRouter` entries), so
    # the OpenAPI schema -- the documented, version-stable way to see the
    # effective mounted paths -- is what we assert against here.
    paths = set(app.openapi()["paths"])
    assert "/content" in paths
    assert "/content/{job_id}" in paths
    assert "/config" in paths
    assert "/graphql" in paths

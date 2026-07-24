import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import dotenv
from fastapi import FastAPI

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")

from graph_api import graphql_router  # noqa: E402
from neo4j_client import bootstrap_schema  # noqa: E402
from routes_auth import router as auth_router  # noqa: E402
from routes_config import router as config_router  # noqa: E402
from routes_content import router as content_router  # noqa: E402
from routes_keys import router as keys_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap_schema()
    yield


DESCRIPTION = """
A **knowledge base for anything**. Submit any text; a relevance filter you define keeps what
matters, entities and relationships are extracted into a typed graph, and you read it back
over GraphQL.

**Authentication** — every endpoint takes your API key as a Bearer token
(`Authorization: Bearer <key>`). Click **Authorize**, paste your key, and try requests
right from this page.

**GraphQL** — the read API and an interactive explorer live at [`/graphql`](/graphql).
"""

TAGS_METADATA = [
    {"name": "Content", "description": "Submit content for ingestion and check a job's status."},
    {"name": "Configuration", "description": "Set your relevance prompt and entity / relationship types."},
    {"name": "Accounts", "description": "Email/password auth: register, log in, verify email, reset password."},
    {"name": "API keys", "description": "Create and manage your knowledge base's API keys (session-authenticated)."},
]

app = FastAPI(
    title="Knowledge Graph Engine",
    description=DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
    docs_url="/docs",  # Swagger UI (interactive: Authorize + Try it out) at /docs
    redoc_url=None,  # ReDoc off
)
app.include_router(content_router)
app.include_router(config_router)
app.include_router(graphql_router, prefix="/graphql")
app.include_router(auth_router)
app.include_router(keys_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)

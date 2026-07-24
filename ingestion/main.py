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
from routes_config import router as config_router  # noqa: E402
from routes_content import router as content_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap_schema()
    yield


app = FastAPI(title="Knowledge Graph Engine", lifespan=lifespan)
app.include_router(content_router)
app.include_router(config_router)
app.include_router(graphql_router, prefix="/graphql")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)

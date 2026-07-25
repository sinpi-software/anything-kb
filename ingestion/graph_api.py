from typing import Any

import strawberry
from fastapi import Depends, HTTPException, status
from graphql import GraphQLError
from strawberry.fastapi import GraphQLRouter

import graph_read
from accounts import current_user, home_knowledge_base_id
from auth import require_knowledge_base
from db import get_postgres_session
from knowledge import _iso_or_empty
from models import User


def _normalize_since(since: str | None) -> str | None:
    """None/empty -> no filter. A non-empty but unparseable value is a client error, surfaced as a
    GraphQL error rather than a raw datetime() stack trace from Neo4j."""
    if not since:
        return None
    iso = _iso_or_empty(since)
    if not iso:
        raise GraphQLError(f"invalid `since` timestamp: {since!r}")
    return iso


@strawberry.type
class Reference:
    label: str
    date: str


@strawberry.type
class Node:
    id: strawberry.ID
    type: str
    name: str
    summary: str | None
    article: str | None
    created_at: str | None
    updated_at: str | None
    knowledge_base_id: strawberry.Private[str]

    @strawberry.field
    def edges(self, type: str | None = None) -> list["Edge"]:
        rows = graph_read.query_edges(self.knowledge_base_id, str(self.id), type)
        return [Edge(type=row["type"], target=_to_node(row["target"], self.knowledge_base_id)) for row in rows]

    @strawberry.field
    def references(self) -> list[Reference]:
        rows = graph_read.query_references(self.knowledge_base_id, str(self.id))
        return [Reference(label=r.get("label") or "", date=r.get("date") or "") for r in rows]

    @strawberry.field
    def related(self) -> list["Node"]:
        """Entities two hops away (second-degree neighbours), knowledge_base-scoped."""
        rows = graph_read.query_related(self.knowledge_base_id, str(self.id))
        return [_to_node(row, self.knowledge_base_id) for row in rows]


@strawberry.type
class Edge:
    type: str
    target: Node


def _to_node(row: dict[str, Any], knowledge_base_id: str) -> Node:
    return Node(
        id=strawberry.ID(str(row["id"])),
        type=row["type"],
        name=row["name"],
        summary=row["summary"],
        article=row.get("article"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        knowledge_base_id=knowledge_base_id,
    )


@strawberry.type
class SourceResult:
    id: strawberry.ID
    label: str
    published_at: str | None
    ingested_at: str | None
    entities: list[Node]


@strawberry.type
class Query:
    @strawberry.field
    def nodes(
        self,
        info: strawberry.Info,
        type: str | None = None,
        search: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[Node]:
        knowledge_base_id: str = info.context["knowledge_base_id"]
        since_iso = _normalize_since(since)
        return [
            _to_node(row, knowledge_base_id)
            for row in graph_read.query_nodes(knowledge_base_id, type, search, since_iso, limit)
        ]

    @strawberry.field
    def node(self, info: strawberry.Info, id: strawberry.ID) -> Node | None:
        knowledge_base_id: str = info.context["knowledge_base_id"]
        row = graph_read.query_node(knowledge_base_id, str(id))
        return _to_node(row, knowledge_base_id) if row else None

    @strawberry.field
    def sources(self, info: strawberry.Info, since: str | None = None, limit: int = 50) -> list[SourceResult]:
        knowledge_base_id: str = info.context["knowledge_base_id"]
        since_iso = _normalize_since(since)
        return [
            SourceResult(
                id=strawberry.ID(str(row["id"])),
                label=row["label"] or "",
                published_at=row.get("published_at"),
                ingested_at=row.get("ingested_at"),
                entities=[_to_node(e, knowledge_base_id) for e in row["entities"]],
            )
            for row in graph_read.query_sources(knowledge_base_id, since_iso, limit)
        ]


schema = strawberry.Schema(query=Query)


async def get_context(knowledge_base_id: str = Depends(require_knowledge_base)) -> dict[str, Any]:
    return {"knowledge_base_id": knowledge_base_id}


# Bearer-authed router for API clients (mounted at /graphql).
graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(schema, context_getter=get_context)


async def get_cookie_context(user: User = Depends(current_user)) -> dict[str, Any]:  # noqa: B008 — FastAPI idiom
    """Resolve the knowledge base from the session cookie, for the logged-in explorer UI."""
    with get_postgres_session() as session:
        knowledge_base_id = home_knowledge_base_id(session, user.id)
    if knowledge_base_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no knowledge base found for this account")
    return {"knowledge_base_id": knowledge_base_id}


# Session-authed router for the in-app GraphQL explorer (mounted at /api/graphql).
cookie_graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(schema, context_getter=get_cookie_context)

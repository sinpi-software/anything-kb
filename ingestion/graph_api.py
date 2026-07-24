from typing import Any

import strawberry
from fastapi import Depends
from strawberry.fastapi import GraphQLRouter

import graph_read
from auth import require_org


@strawberry.type
class Node:
    id: strawberry.ID
    type: str
    name: str
    summary: str | None
    org_id: strawberry.Private[str]

    @strawberry.field
    def edges(self, type: str | None = None) -> list["Edge"]:
        rows = graph_read.query_edges(self.org_id, str(self.id), type)
        return [Edge(type=row["type"], target=_to_node(row["target"], self.org_id)) for row in rows]


@strawberry.type
class Edge:
    type: str
    target: Node


def _to_node(row: dict[str, Any], org_id: str) -> Node:
    return Node(
        id=strawberry.ID(str(row["id"])),
        type=row["type"],
        name=row["name"],
        summary=row["summary"],
        org_id=org_id,
    )


@strawberry.type
class Query:
    @strawberry.field
    def nodes(
        self, info: strawberry.Info, type: str | None = None, search: str | None = None, limit: int = 50
    ) -> list[Node]:
        org_id: str = info.context["org_id"]
        return [_to_node(row, org_id) for row in graph_read.query_nodes(org_id, type, search, limit)]

    @strawberry.field
    def node(self, info: strawberry.Info, id: strawberry.ID) -> Node | None:
        org_id: str = info.context["org_id"]
        row = graph_read.query_node(org_id, str(id))
        return _to_node(row, org_id) if row else None


schema = strawberry.Schema(query=Query)


async def get_context(org_id: str = Depends(require_org)) -> dict[str, Any]:
    return {"org_id": org_id}


graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(schema, context_getter=get_context)

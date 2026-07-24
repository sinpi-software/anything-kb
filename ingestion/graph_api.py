from typing import Any

import strawberry
from fastapi import Depends
from strawberry.fastapi import GraphQLRouter

import graph_read
from auth import require_knowledge_base


@strawberry.type
class Node:
    id: strawberry.ID
    type: str
    name: str
    summary: str | None
    knowledge_base_id: strawberry.Private[str]

    @strawberry.field
    def edges(self, type: str | None = None) -> list["Edge"]:
        rows = graph_read.query_edges(self.knowledge_base_id, str(self.id), type)
        return [Edge(type=row["type"], target=_to_node(row["target"], self.knowledge_base_id)) for row in rows]


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
        knowledge_base_id=knowledge_base_id,
    )


@strawberry.type
class Query:
    @strawberry.field
    def nodes(
        self, info: strawberry.Info, type: str | None = None, search: str | None = None, limit: int = 50
    ) -> list[Node]:
        knowledge_base_id: str = info.context["knowledge_base_id"]
        return [
            _to_node(row, knowledge_base_id) for row in graph_read.query_nodes(knowledge_base_id, type, search, limit)
        ]

    @strawberry.field
    def node(self, info: strawberry.Info, id: strawberry.ID) -> Node | None:
        knowledge_base_id: str = info.context["knowledge_base_id"]
        row = graph_read.query_node(knowledge_base_id, str(id))
        return _to_node(row, knowledge_base_id) if row else None


schema = strawberry.Schema(query=Query)


async def get_context(knowledge_base_id: str = Depends(require_knowledge_base)) -> dict[str, Any]:
    return {"knowledge_base_id": knowledge_base_id}


graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(schema, context_getter=get_context)

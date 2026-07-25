from typing import Any

import config
from knowledge import escape_lucene
from neo4j_client import get_neo4j_session

_NODE_RETURN = (
    "RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary, e.article AS article, "
    "toString(e.created_at) AS created_at, toString(e.updated_at) AS updated_at"
)


def query_nodes(
    knowledge_base_id: str, type_: str | None, search: str | None, since: str | None, limit: int
) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), config.NODES_MAX_LIMIT)
    params: dict[str, Any] = {"knowledge_base_id": knowledge_base_id, "limit": limit}
    if since:
        params["since"] = since
    if search:
        cypher = (
            "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score "
            "WHERE e.knowledge_base_id = $knowledge_base_id "
        )
        params["q"] = escape_lucene(search)
        if type_:
            cypher += "AND e.type = $type "
            params["type"] = type_
        if since:
            cypher += f"AND e.updated_at >= datetime($since) {_NODE_RETURN} ORDER BY e.updated_at DESC LIMIT $limit"
        else:
            cypher += f"{_NODE_RETURN} ORDER BY score DESC LIMIT $limit"
    else:
        cypher = "MATCH (e:Entity {knowledge_base_id: $knowledge_base_id}) "
        conds = []
        if type_:
            conds.append("e.type = $type")
            params["type"] = type_
        if since:
            conds.append("e.updated_at >= datetime($since)")
        if conds:
            cypher += "WHERE " + " AND ".join(conds) + " "
        order = "ORDER BY e.updated_at DESC " if since else ""
        cypher += f"{_NODE_RETURN} {order}LIMIT $limit"
    with get_neo4j_session() as session:
        return [dict(r) for r in session.run(cypher, params)]


def query_sources(knowledge_base_id: str, since: str | None, limit: int) -> list[dict[str, Any]]:
    """Recent Source nodes, newest-ingested first, each with the entities that mention it.
    knowledge_base-scoped on the Source and on every mentioned Entity."""
    limit = min(max(limit, 1), config.NODES_MAX_LIMIT)
    params: dict[str, Any] = {"kb": knowledge_base_id, "limit": limit}
    where = ""
    if since:
        where = "WHERE s.ingested_at >= datetime($since) "
        params["since"] = since
    cypher = (
        "MATCH (s:Source {knowledge_base_id: $kb}) "
        f"{where}"
        "RETURN s.job_id AS id, s.label AS label, "
        "toString(s.published_at) AS published_at, toString(s.ingested_at) AS ingested_at, "
        "[(s)<-[:MENTIONED_IN]-(e:Entity {knowledge_base_id: $kb}) | "
        "{id: e.id, type: e.type, name: e.name, summary: e.summary, article: e.article, "
        "created_at: toString(e.created_at), updated_at: toString(e.updated_at)}] AS entities "
        "ORDER BY s.ingested_at DESC LIMIT $limit"
    )
    with get_neo4j_session() as session:
        return [dict(r) for r in session.run(cypher, params)]


def query_node(knowledge_base_id: str, node_id: str) -> dict[str, Any] | None:
    with get_neo4j_session() as session:
        record = session.run(
            f"MATCH (e:Entity {{id: $id, knowledge_base_id: $knowledge_base_id}}) {_NODE_RETURN}",
            {"id": node_id, "knowledge_base_id": knowledge_base_id},
        ).single()
    return dict(record) if record else None


def query_edges(knowledge_base_id: str, node_id: str, type_: str | None) -> list[dict[str, Any]]:
    cypher = (
        "MATCH (a:Entity {id: $id, knowledge_base_id: $knowledge_base_id})"
        "-[r:RELATED {knowledge_base_id: $knowledge_base_id}]->"
        "(b:Entity {knowledge_base_id: $knowledge_base_id}) "
    )
    params: dict[str, Any] = {"id": node_id, "knowledge_base_id": knowledge_base_id}
    if type_:
        cypher += "WHERE r.type = $type "
        params["type"] = type_
    cypher += "RETURN r.type AS type, b.id AS id, b.type AS ntype, b.name AS name, b.summary AS summary"
    with get_neo4j_session() as session:
        return [
            {
                "type": row["type"],
                "target": {"id": row["id"], "type": row["ntype"], "name": row["name"], "summary": row["summary"]},
            }
            for row in session.run(cypher, params)
        ]


def query_references(knowledge_base_id: str, entity_id: str) -> list[dict[str, Any]]:
    with get_neo4j_session() as session:
        return [
            dict(r)
            for r in session.run(
                "MATCH (e:Entity {id: $id, knowledge_base_id: $kb})"
                "-[:MENTIONED_IN]->(s:Source {knowledge_base_id: $kb}) "
                "RETURN s.label AS label, toString(s.published_at) AS date ORDER BY s.published_at",
                {"id": entity_id, "kb": knowledge_base_id},
            )
        ]


def query_related(knowledge_base_id: str, entity_id: str) -> list[dict[str, Any]]:
    """Entities exactly two relationship-hops away (second-degree neighbours) — excluding the
    entity itself and its direct neighbours. knowledge_base-scoped on every hop of the path."""
    with get_neo4j_session() as session:
        return [
            dict(r)
            for r in session.run(
                "MATCH path=(e:Entity {id: $id, knowledge_base_id: $kb})"
                "-[:RELATED*2..2]-(n:Entity {knowledge_base_id: $kb}) "
                "WHERE n.id <> $id AND NOT (e)-[:RELATED]-(n) "
                "AND ALL(rel IN relationships(path) WHERE rel.knowledge_base_id = $kb) "
                "RETURN DISTINCT n.id AS id, n.type AS type, n.name AS name, n.summary AS summary "
                "ORDER BY n.name",
                {"id": entity_id, "kb": knowledge_base_id},
            )
        ]

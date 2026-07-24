from typing import Any

import config
from knowledge import escape_lucene
from neo4j_client import get_neo4j_session

_NODE_RETURN = "RETURN e.id AS id, e.type AS type, e.name AS name, e.summary AS summary"


def query_nodes(org_id: str, type_: str | None, search: str | None, limit: int) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), config.NODES_MAX_LIMIT)
    params: dict[str, Any] = {"org_id": org_id, "limit": limit}
    if search:
        cypher = "CALL db.index.fulltext.queryNodes('entity_name', $q) YIELD node AS e, score WHERE e.org_id = $org_id "
        params["q"] = escape_lucene(search)
        if type_:
            cypher += "AND e.type = $type "
            params["type"] = type_
        cypher += f"{_NODE_RETURN} ORDER BY score DESC LIMIT $limit"
    else:
        cypher = "MATCH (e:Entity {org_id: $org_id}) "
        if type_:
            cypher += "WHERE e.type = $type "
            params["type"] = type_
        cypher += f"{_NODE_RETURN} LIMIT $limit"
    with get_neo4j_session() as session:
        return [dict(r) for r in session.run(cypher, params)]


def query_node(org_id: str, node_id: str) -> dict[str, Any] | None:
    with get_neo4j_session() as session:
        record = session.run(
            f"MATCH (e:Entity {{id: $id, org_id: $org_id}}) {_NODE_RETURN}",
            {"id": node_id, "org_id": org_id},
        ).single()
    return dict(record) if record else None


def query_edges(org_id: str, node_id: str, type_: str | None) -> list[dict[str, Any]]:
    cypher = "MATCH (a:Entity {id: $id, org_id: $org_id})-[r:RELATED {org_id: $org_id}]->(b:Entity {org_id: $org_id}) "
    params: dict[str, Any] = {"id": node_id, "org_id": org_id}
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

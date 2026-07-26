from functools import lru_cache
from os import getenv

from neo4j import Driver, GraphDatabase, Session

import config


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    uri = getenv(config.NEO4J_URI_ENV, "bolt://localhost:7687")
    user = getenv(config.NEO4J_USER_ENV, "neo4j")
    password = getenv(config.NEO4J_PASSWORD_ENV, "neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))


def get_neo4j_session() -> Session:
    return get_driver().session()


def bootstrap_schema() -> None:
    # Idempotent: a unique id per entity, and a full-text index over name + aliases
    # used for resolution candidate lookup. knowledge_base_id is a filter property on every query.
    with get_neo4j_session() as session:
        session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
        session.run("CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.aliases]")
        # Backs the `sources(since:)` recency query, which orders/filters on Source.ingested_at.
        session.run("CREATE RANGE INDEX source_ingested_at IF NOT EXISTS FOR (s:Source) ON (s.ingested_at)")


def purge_knowledge_base(knowledge_base_id: str) -> int:
    """Delete every node belonging to a knowledge base. Returns the node count deleted.

    Label-agnostic on purpose: both labels (Entity, Source) carry knowledge_base_id and
    a new one would too, so matching on the property cannot miss a label someone adds
    later. DETACH removes the relationships, which carry the same property.
    """
    with get_driver().session() as session:
        record = session.run(
            "MATCH (n) WHERE n.knowledge_base_id = $kb DETACH DELETE n RETURN count(n) AS deleted",
            kb=knowledge_base_id,
        ).single()
        return int(record["deleted"]) if record is not None else 0

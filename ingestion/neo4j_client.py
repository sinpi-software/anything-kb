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

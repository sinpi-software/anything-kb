import os
import uuid

os.environ.setdefault("INGESTION_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("INGESTION_NEO4J_USER", "neo4j")
os.environ.setdefault("INGESTION_NEO4J_PASSWORD", "ingestion")

import pytest


def _neo4j_available() -> bool:
    try:
        from neo4j_client import get_driver

        with get_driver().session() as s:
            s.run("RETURN 1").consume()
        return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(not _neo4j_available(), reason="Neo4j not reachable")


@requires_neo4j
def test_purge_deletes_only_the_named_knowledge_base() -> None:
    """Both labels carry knowledge_base_id and DETACH takes the relationships, so the
    purge is label-agnostic. The second knowledge base proves it is also scoped."""
    from neo4j_client import get_driver, purge_knowledge_base

    doomed, kept = str(uuid.uuid4()), str(uuid.uuid4())
    with get_driver().session() as s:
        s.run(
            "CREATE (a:Entity {id: $a, knowledge_base_id: $doomed, name: 'A'}) "
            "CREATE (b:Entity {id: $b, knowledge_base_id: $doomed, name: 'B'}) "
            "CREATE (c:Source {knowledge_base_id: $doomed, job_id: $j}) "
            "CREATE (k:Entity {id: $k, knowledge_base_id: $kept, name: 'K'}) "
            "CREATE (a)-[:RELATED {knowledge_base_id: $doomed}]->(b) "
            "CREATE (a)-[:MENTIONED_IN]->(c)",
            a=str(uuid.uuid4()), b=str(uuid.uuid4()), k=str(uuid.uuid4()),
            j=str(uuid.uuid4()), doomed=doomed, kept=kept,
        ).consume()

    try:
        deleted = purge_knowledge_base(doomed)
        assert deleted == 3
        with get_driver().session() as s:
            left_record = s.run(
                "MATCH (n) WHERE n.knowledge_base_id = $kb RETURN count(n) AS c", kb=doomed
            ).single()
            survivors_record = s.run(
                "MATCH (n) WHERE n.knowledge_base_id = $kb RETURN count(n) AS c", kb=kept
            ).single()
            assert left_record is not None
            assert survivors_record is not None
            left = left_record["c"]
            survivors = survivors_record["c"]
        assert left == 0
        assert survivors == 1, "purge must not touch another knowledge base"
    finally:
        with get_driver().session() as s:
            s.run("MATCH (n) WHERE n.knowledge_base_id IN [$a, $b] DETACH DELETE n", a=doomed, b=kept).consume()


@requires_neo4j
def test_purge_of_an_empty_knowledge_base_is_zero_not_an_error() -> None:
    """Delete must be re-runnable after a partial failure, so a second purge is a no-op."""
    from neo4j_client import purge_knowledge_base

    assert purge_knowledge_base(str(uuid.uuid4())) == 0

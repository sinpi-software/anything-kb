from typing import Any

from cluster import cluster_sources


def _source(sid: str, entity_ids: list[str], ingested: str = "2026-07-22T00:00:00") -> dict[str, Any]:
    return {
        "id": sid,
        "label": f"Source {sid}",
        "publishedAt": ingested,
        "ingestedAt": ingested,
        "entities": [{"id": e, "name": e.upper(), "type": "Thing", "summary": "s", "article": "a"} for e in entity_ids],
    }


def test_sources_sharing_an_entity_become_one_cluster() -> None:
    clusters = cluster_sources([_source("1", ["ada"]), _source("2", ["ada", "babbage"])], max_sources=10)
    assert len(clusters) == 1
    assert {s["id"] for s in clusters[0].sources} == {"1", "2"}


def test_sources_sharing_nothing_stay_separate() -> None:
    clusters = cluster_sources([_source("1", ["ada"]), _source("2", ["zeta"])], max_sources=10)
    assert len(clusters) == 2


def test_transitive_sharing_merges_a_chain() -> None:
    """A-B share x, B-C share y: all three are one story."""
    clusters = cluster_sources([_source("1", ["x"]), _source("2", ["x", "y"]), _source("3", ["y"])], max_sources=10)
    assert len(clusters) == 1
    assert {s["id"] for s in clusters[0].sources} == {"1", "2", "3"}


def test_cluster_entities_are_deduped_and_unioned() -> None:
    clusters = cluster_sources([_source("1", ["ada"]), _source("2", ["ada", "babbage"])], max_sources=10)
    assert {e["id"] for e in clusters[0].entities} == {"ada", "babbage"}


def test_a_source_with_no_entities_is_its_own_cluster() -> None:
    clusters = cluster_sources([_source("1", [])], max_sources=10)
    assert len(clusters) == 1
    assert clusters[0].entities == ()


def test_clusters_are_ordered_by_size_then_recency() -> None:
    clusters = cluster_sources(
        [
            _source("1", ["a"], "2026-07-20T00:00:00"),
            _source("2", ["b"], "2026-07-22T00:00:00"),
            _source("3", ["b"], "2026-07-21T00:00:00"),
        ],
        max_sources=10,
    )
    assert [len(c.sources) for c in clusters] == [2, 1]


def test_an_oversized_cluster_is_truncated_newest_first() -> None:
    """One hub entity mentioned by everything must not pull the whole window into a
    single unwritable story."""
    sources = [_source(str(i), ["hub"], f"2026-07-{10 + i:02d}T00:00:00") for i in range(6)]
    clusters = cluster_sources(sources, max_sources=3)
    assert len(clusters[0].sources) == 3
    assert [s["id"] for s in clusters[0].sources] == ["5", "4", "3"]


def test_empty_input_yields_no_clusters() -> None:
    assert cluster_sources([], max_sources=10) == []

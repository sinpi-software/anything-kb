"""Grouping sources into stories.

Two articles that mention the same entity are about the same story, so sources are
grouped into connected components over shared entities (union-find). Pure: no I/O,
no LLM — the clustering decision is inspectable and cheap to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Cluster:
    """One story's worth of material: the sources, and the union of their entities."""

    sources: tuple[dict[str, Any], ...]
    entities: tuple[dict[str, Any], ...]


def _find(parent: dict[str, str], key: str) -> str:
    while parent[key] != key:
        parent[key] = parent[parent[key]]
        key = parent[key]
    return key


def _union(parent: dict[str, str], a: str, b: str) -> None:
    root_a, root_b = _find(parent, a), _find(parent, b)
    if root_a != root_b:
        parent[root_b] = root_a


def _ingested(source: dict[str, Any]) -> str:
    return str(source.get("ingestedAt") or source.get("publishedAt") or "")


def cluster_sources(sources: list[dict[str, Any]], max_sources: int) -> list[Cluster]:
    """Group sources into stories. Clusters come back largest-first, ties broken by
    recency; each cluster's sources are newest-first and capped at `max_sources`."""
    if not sources:
        return []

    # Union-find over source ids, joined through the entities they share.
    parent = {str(s["id"]): str(s["id"]) for s in sources}
    seen_entity: dict[str, str] = {}
    for source in sources:
        source_id = str(source["id"])
        for entity in source.get("entities") or []:
            entity_id = str(entity["id"])
            if entity_id in seen_entity:
                _union(parent, seen_entity[entity_id], source_id)
            else:
                seen_entity[entity_id] = source_id

    grouped: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        grouped.setdefault(_find(parent, str(source["id"])), []).append(source)

    clusters = []
    for members in grouped.values():
        members.sort(key=_ingested, reverse=True)
        kept = members[:max_sources]
        entities: dict[str, dict[str, Any]] = {}
        for source in kept:
            for entity in source.get("entities") or []:
                entities.setdefault(str(entity["id"]), entity)
        clusters.append(Cluster(sources=tuple(kept), entities=tuple(entities.values())))

    clusters.sort(key=lambda c: (len(c.sources), _ingested(c.sources[0])), reverse=True)
    return clusters

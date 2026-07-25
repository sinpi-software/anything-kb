# Entity Salience Gate — Design

**Status:** Approved (design), pending spec review
**Date:** 2026-07-25

## Problem

In discover mode the extraction LLM coins hyper-granular entity types from
incidental article details. A single Harry Morgan Bridge road-work story minted
`TimeWindow` ("7:00 a.m.–3:30 p.m."), `LaneClosure`, `MilepostRange`,
`TrafficImpact`, `MaintenanceActivity`, and `MaintenanceCrew` — none of which is
a durable, wiki-worthy subject. These pollute the graph with "useless nodes."

Two attempts to fix this in the **extraction** prompt failed:

1. An instance-level quality bar (`_ENTITY_QUALITY`).
2. A type-coining constraint enumerating banned categories.

Both were confirmed deployed; the model still coined the fragment types. Bumping
the extraction model nano → mini did **not** help either — both models faithfully
decompose a detailed logistics article into typed fragments. Conclusion: a
salience bar cannot be enforced *inline during extraction* by any model, and an
enumerated ban-list is an unmaintainable whack-a-mole treadmill.

## Key insight: the gate already exists

`merge_content` already routes every newly-coined type through
`consolidate_types`, which classifies each candidate as `existing` / `new` /
**`drop`**. A dropped type's entity is never written (`merge_content`
`resolve()` returns `None`), and relationships with a dropped endpoint are
skipped cleanly (`merge_content.py:565`, `if src and tgt`).

The gate leaks only because it judges the **wrong axis**: its criterion is
"incidental or *not aligned with interests*" (relevance). Fragment types like
`LaneClosure` **are** relevant to a local-news feed — they simply are not
durable entity categories. The gate never asks the durability question.

`consolidate_types` is also a **focused, isolated per-type classification** — the
reliable "one crisp question" shape — unlike the extraction firehose. It is the
correct choke point.

## The fix

Two changes to `consolidate_types`, plus a model choice. No new gate, no
enumerated ban-list, no config field, no deterministic backstop, no "fold"
behavior.

### 1. A single general durability principle (entity kind)

Replace the relevance-only criterion with one general test, tied to the
product's own framing (the whole system is a wiki):

> Admit a candidate entity type as `new` only if **each instance of it would
> merit its own standalone wiki page** — a durable, individually-notable subject
> (a person, organization, place, work, law, lasting event…). Choose `drop` when
> instances are passing details, circumstances, measurements, time windows, or
> attributes of some *other* subject rather than subjects in their own right —
> even if topically relevant.

This is a general principle, not a list. It covers `TimeWindow`, `LaneClosure`,
`MilepostRange`, and every future fragment shape without naming any of them, and
never needs editing as new corpora arrive. If the principle itself ever needs
rewording, that is a one-line change, not an append-to-list.

The **relationship** kind keeps its existing distinctness criterion (merge
near-synonyms, keep Funds vs Sponsors separate, drop incidental) — the wiki-page
test is entity-specific and does not apply to relations.

### 2. Give the gate a concrete example instance

Today the gate judges the bare abstract word `"TimeWindow"`. Thread one
representative instance from the extraction alongside each candidate so it judges
the concrete case:

```
Candidates (with an example instance):
- TimeWindow (e.g. "7:00 a.m.–3:30 p.m. (work window)")
- Wildfire (e.g. "Deer Island lightning fire")
```

A concrete instance is what makes the durability call obvious and is the single
biggest lever for an LLM-only gate holding the line. Entity examples are entity
names; relationship examples are `"Source → Target"`.

### 3. Model

- Revert extraction (`config.LLM_MODEL`) to `openai/gpt-5-nano` — mini did not
  earn its extra cost at extraction.
- Run the gate (`consolidate_types`) on `openai/gpt-5-mini` via a new
  `config.TYPE_GATE_MODEL`. The gate is low-volume (once per *novel* type, not
  per entity) and is now the sole salience choke point, so a more capable model
  is cheap insurance for the LLM-only approach. **This is the one deliberately
  non-minimal choice; flagged for review — collapse to nano if preferred.**

## Interfaces

```python
# config.py
LLM_MODEL = "openai/gpt-5-nano"          # reverted from gpt-5-mini
TYPE_GATE_MODEL = "openai/gpt-5-mini"    # new: model for the salience/consolidation gate

# knowledge.py
def consolidate_types(
    client: OpenRouter,
    model: str,
    kind: str,                           # "entity" | "relationship"
    candidates: list[str],
    vocab: list[dict[str, Any]],
    interests: str,
    llm_params: dict[str, Any],
    examples: dict[str, str] | None = None,   # NEW: candidate-name -> one example instance
) -> dict[str, dict[str, str]]:
    ...
```

- The `examples` dict is keyed by the raw candidate string (as it appears in
  `candidates`); a missing key just omits the "e.g." clause for that candidate.
- The system prompt selects the admission criterion by `kind`: entity → the
  wiki-page durability principle; relationship → the existing distinctness
  guidance.

### Threading examples in `merge_content`

`resolve_kind` gains an `examples: dict[str, str]` parameter, built by the caller
and forwarded to `consolidate_types` (called with `config.TYPE_GATE_MODEL`):

```python
# entity examples: first-seen name per type
entity_examples: dict[str, str] = {}
for e in extraction.entities:
    entity_examples.setdefault(e.type, e.name)

# relationship examples: first-seen "source → target" per type
rel_examples: dict[str, str] = {}
for r in extraction.relationships:
    rel_examples.setdefault(r.type, f"{r.source_name} → {r.target_name}")
```

Everything else in `merge_content` is unchanged: banned/pinned flags, casing
normalization, the `drop → entity not written → dangling relationships skipped`
behavior.

## Non-goals

- No enumerated list of banned categories (the thing we are explicitly removing).
- No new KB config field / migration / API / UI (baked-in principle).
- No deterministic pattern backstop (LLM-only, per approval; kept in reserve if
  live validation shows leakage).
- No "fold the fragment into a related entity" behavior — dropped means dropped.
- No change to guided (`discover=False`) mode — types are fixed there.

## Testing

Unit tests on `consolidate_types` (monkeypatch `_chat`, assert prompt contents
and decision mapping):

- Entity fragment candidates with example instances (`TimeWindow` → "7:00–3:30",
  `MilepostRange` → "Mileposts 9.5–9.0", `LaneClosure`) map to `drop`.
- Durable entity candidates (`Wildfire` → "Deer Island fire",
  `SettlementAgreement`) map to `new`/`existing`.
- The example instance for a candidate is actually rendered into the prompt
  (`e.g.` clause present).
- Relationship kind still uses distinctness criteria (a distinct relation is not
  dropped merely for being non-"durable").
- `examples=None` / missing key degrades gracefully (no `e.g.` clause, no error).

Existing `merge_content` and `consolidate_types` tests updated for the new
signature.

## Live validation

Clean-slate KLOG re-run (worker→0, skip queue, clear graph, worker→1, ingest,
drain). Success = the Harry Morgan Bridge story no longer mints `TimeWindow`,
`LaneClosure`, `MilepostRange`, `TrafficImpact`, `MaintenanceActivity`, or
`MaintenanceCrew`, while durable subjects (people, the Deer Island wildfire, the
OPMA settlement, venues, cities) survive.

If fragments still leak on the mini gate, the reserved next step is the
deterministic pattern backstop — not another prompt edit.

## Out of scope (tracked separately)

- **Worker stale-job reclaim.** The worker only claims `PENDING`; jobs left
  `processing` when a worker dies (e.g. every deploy that rolls it mid-run) are
  orphaned forever. Found 11+ such zombies. Real reliability bug, its own fix.

# Conservative Entity Resolution — Design

**Status:** Approved (design), pending spec review
**Date:** 2026-07-25

## Problem

Entity resolution fused two unrelated subjects into one node. A KLOG ingest produced an
entity "Attorney Fees and Litigation Costs (Settlement)" (type `Work`) whose article and edges
mix a Stageworks Northwest theater production (`Held at → Stageworks Northwest`) with the OPMA
settlement's $80,000 attorney fees (`Paid → City of Longview`), citing two unrelated sources.

Two permissive paths in `resolve_entities_batch` (`knowledge.py`) allow this:

1. **No-LLM auto-merge** (`knowledge.py:260`): `if len(cands) == 1: resolved.append(str(cands[0]["id"]))`
   — a single candidate merges with *zero verification*. Candidates are gathered by
   `(knowledge_base_id, type)` plus a fuzzy full-text name match ordered by score with no
   threshold, so a weak full-text hit inside a broad type (`Work`, `Organization`, `Event`)
   becomes an automatic merge.
2. **Non-conservative resolver prompt** (`knowledge.py:276`): *"decide whether it is the SAME as
   one of its listed candidates. Return that candidate's id, or NEW if none match."* — no bias
   toward NEW, so nano picks a plausible-looking wrong match.

There is also a latent third case: even an *exact* normalized-name match auto-merges, so two
different people both named "John Smith" (type `Person`) would fuse into one node.

## The fix

Mirror the salience-gate philosophy: adversarial default, precision-critical judgment on a
capable model.

### 1. Every candidate merge is LLM-verified (kill the auto-merge)

In `resolve_entities_batch`, remove the `len(cands) == 1` fast-merge. Route **every** entity with
≥1 candidate through the batched resolver; only 0-candidate entities are new without an LLM call.

```
for i, cands in enumerate(candidates):
    resolved.append(None)      # default: new
    if cands:                  # ≥1 candidate -> verify, never auto-merge
        to_resolve.append(i)
```

Still one batched call per item (it now also covers the 1-candidate cases). The `valid`
guard that only accepts a returned id present among that entity's candidates stays.

### 2. Conservative resolver prompt (default NEW)

Replace the system prompt so a merge happens only on unmistakable same-identity:

> Answer with a candidate's id ONLY if it is unmistakably the SAME real-world entity — the same
> specific person, place, organization, work, or event — not merely the same type or a related
> topic. If they are different things that happen to share a name or category, or you are unsure,
> answer NEW. Creating a new node is always safe; fusing two different subjects is not.

### 3. Resolver runs on the capable model

The resolver is now the precision-critical merge gate. `merge_content` calls it with a new
`config.RESOLUTION_MODEL = "openai/gpt-5-mini"` (sibling to `TYPE_GATE_MODEL`) instead of
`config.LLM_MODEL`. Bounded cost: one batched call per item that has candidates. **This is the
one deliberately non-minimal choice; flagged for review — collapse to `LLM_MODEL` if preferred.**

### 4. Safe failure mode (make explicit)

If the resolver call errors or returns nothing, the affected entities keep `resolved[i] = None`
and are created as new. Worst case is a duplicate a later ingest re-merges — never a wrong
fusion. This is already the behavior; the design commits to it.

## Interfaces

```python
# config.py
RESOLUTION_MODEL = "openai/gpt-5-mini"   # new: model for the precision-critical merge resolver

# knowledge.py — resolve_entities_batch: signature unchanged; behavior changes
#   0 candidates            -> None (new), no LLM
#   >=1 candidate           -> batched conservative resolver (no more len==1 auto-merge)
#   resolver empty/error    -> None (new) for the affected entities

# knowledge.py — merge_content call site (currently ~line 531)
resolved_ids = resolve_entities_batch(neo, client, config.RESOLUTION_MODEL, knowledge_base_id, entities, llm_params)
```

`_gather_candidates`, `candidate_query`, `fulltext_candidate_query`, and
`config.KNOWLEDGE_RESOLUTION_CANDIDATES` are unchanged.

## Non-goals

- **Instance-level salience.** "Attorney Fees and Litigation Costs" being a weak, non-standalone
  entity in the first place. The salience gate judges *types* (`Work` is durable), not
  *instances* — catching weak instances of durable types is its own piece of work.
- **Candidate-gathering thresholds.** Left as-is; a conservative resolver tolerates a few weak
  candidates and rejects them rather than being handed fewer.
- No change to the 0-candidate path, tenancy scoping, or the `valid`-id guard.

## Testing

Unit tests on `resolve_entities_batch` (fake session + monkeypatched `_chat`, following the
existing `test_resolve_batch_*` pattern):

- **Regression for this bug:** a single candidate that is a *different* subject (same type,
  unrelated summary) → the resolver answers NEW → returns `None` (no merge). This is the case
  the old `len==1` auto-merge got wrong.
- A single candidate that IS the same entity → resolver returns its id → merges. The good path
  still works.
- **Replaces `test_resolve_batch_single_candidate_skips_llm`** — that test asserts a single
  candidate skips the LLM, which is exactly the behavior being removed. The new test asserts a
  single candidate now *consults* the resolver.
- Two candidates, hallucinated/out-of-set id → ignored (existing `valid` guard), returns `None`.
- Resolver returns nothing / raises → all candidate-having entities resolve to `None` (new).
- 0 candidates → `None`, no LLM (unchanged).

Existing `merge_content` integration tests must still pass; update any that relied on the
single-candidate auto-merge path.

## Live validation

Clean-slate KLOG re-ingest. The "Attorney Fees and Litigation Costs" fusion must not recur:
no single node should mix the Stageworks production with the OPMA settlement, and its edges/
sources should belong to one subject. Spot-check that legitimate recurring entities (Longview,
Cowlitz County) still merge across stories — the fix must not stop correct merges.

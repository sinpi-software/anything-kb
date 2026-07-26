# KB-scoped API and role enforcement — Design

**Status:** design. Captured 2026-07-26.

**Goal:** let a request name which knowledge base it acts on, give users endpoints to
create, rename and delete knowledge bases, and enforce the four roles that already exist
in the model — without changing anything the current desk UI does.

This is **sub-project A** of two. It ships behind the existing UI with no user-visible
change. **Sub-project B** (desk UI: selection and management) consumes it: routes move to
`/app/:kbId/*`, a switcher lands in the header, a management page appears, and B retires
the legacy routes A keeps alive. B is not designed here.

## The problem

Users already have several knowledge bases — a real account on this machine has three —
but can only ever reach one. `accounts.home_knowledge_base_id` returns the earliest
membership by `created_at`, and it is how every cookie-authenticated route resolves its
tenant: `routes_keys.py`, `routes_ingest.py`, `routes_settings.py`, and `graph_api.py:143`,
which is the entire GraphQL read path. Nothing in the UI acts on `Me.knowledge_bases`,
which `/api/auth/me` has been returning all along.

There are also no knowledge-base endpoints at all. `POST /api/auth/register` creates one
inline; nothing lists, creates, renames or deletes them.

So "let users manage many knowledge bases" contains a prerequisite: a request must be able
to *name* its knowledge base. Creating a second one is useless until then.

The four roles in `KnowledgeBaseUserRole` — owner, admin, editor, reader — are stored on
every membership and consulted nowhere.

## Carrying the knowledge base

The knowledge base is named **in the URL**: `/api/knowledge-bases/{kb_id}/...` on the API,
and `/app/:kbId/...` in the UI that B will build.

A cookie was the cheaper option — both server-side loaders and browser `fetch` carry
cookies with no plumbing — but it makes the active knowledge base a property of the
browser, so two tabs cannot show two knowledge bases. The URL makes it a property of the
page: bookmarkable, shareable, and independent per tab. The cost is that every caller
passes the id explicitly, which is exactly the plumbing the cookie avoided. That cost is
accepted.

## Authorization

One helper replaces `home_knowledge_base_id` as the thing routes call:

```python
_ROLE_RANK = {"reader": 0, "editor": 1, "admin": 2, "owner": 3}

def require_membership(session, user_id, kb_id, min_role) -> str:
    """The caller's role in kb_id, or 404 if they lack the required rank."""
```

| Capability | Minimum role |
| --- | --- |
| Read the graph, read config | `reader` |
| Submit content (ingest) | `editor` |
| Write config, manage API keys | `admin` |
| Rename, delete | `owner` |

**404, never 403.** A 403 confirms the knowledge base exists to someone with no membership
in it. Tenancy is the security boundary here — `graph_api.py` isolates tenants with nothing
but a `knowledge_base_id` property filter — so existence disclosure undermines the thing
the boundary protects. The price is that a permission error and a typo are indistinguishable
to the caller.

**`home_knowledge_base_id` stays,** used only by the legacy unscoped routes. It is how the
current UI keeps working while A ships. B deletes both it and them.

**This enforcement is invisible today.** Nothing can assign a role, so every membership is
`owner` and no request will be refused. Its value is that B and any future sharing build on
a real boundary rather than a retrofitted one. The tests are the only proof it works, which
is why they construct memberships at each role directly.

## Knowledge-base endpoints

A new `routes_knowledge_bases.py`, prefix `/api/knowledge-bases`, cookie-authenticated with
CSRF, following `routes_keys.py`'s existing shape.

| Method | Path | Role | Behaviour |
| --- | --- | --- | --- |
| `GET` | `` | any | The caller's knowledge bases with their role, oldest first. Not role-gated — it filters the caller's own memberships, so it returns exactly what they may see |
| `POST` | `` | — | Creator becomes `owner`. Requires a verified email |
| `PATCH` | `/{kb_id}` | `owner` | Partial update of `name` and/or `charter` |
| `DELETE` | `/{kb_id}` | `owner` | Body `{confirm_name}`; refuses the caller's last knowledge base |

`POST` requires a verified email because `routes_keys.py:29` already gates key creation that
way, and a knowledge base is the thing keys live in.

### One creation path

Three places would otherwise know how to build a knowledge base, and two already disagree:
`routes_auth.register` creates the knowledge base and the owner membership but **no
`KnowledgeBaseConfig`**, while `seed.py` creates all three. A registered user's knowledge
base therefore has no config — which is the standing "missing config still burns an LLM
call" follow-up, seen from the other end.

So A extracts one helper:

```python
def create_knowledge_base(session, user, name, charter=None) -> KnowledgeBase:
    """The knowledge base, the creator's owner membership, and a default config."""
```

`register`, `seed.py`, and `POST /api/knowledge-bases` all call it. The default entity and
relationship types move out of `seed.py` into one named constant. This is not unrelated
cleanup: it is the direct consequence of adding a third creation path to two that already
diverge.

### Delete

```
1. neo4j    MATCH (n) WHERE n.knowledge_base_id = $id DETACH DELETE n
2. postgres (one transaction)
              ingest_jobs, api_keys, knowledge_base_configs,
              knowledge_base_users, then the knowledge_bases row
```

Both Neo4j labels (`Entity`, `Source`) carry `knowledge_base_id`, and no node lacks one, so
the label-agnostic match purges the tenant completely; `DETACH` takes the relationships,
which carry the same property.

No `knowledge_base_id` foreign key declares `ON DELETE CASCADE` — only the `users.id` keys on
`sessions` and `email_tokens` cascade — so step 2 deletes children explicitly. Adding real
cascades was considered and rejected for now: it rewrites four constraints on live tables to
save code in exactly one endpoint.

Step 1 sits outside the Postgres transaction because it is a different store; no ordering
makes the two atomic. Neo4j first is the recoverable order. A failure between the steps
leaves a knowledge base that is empty but still listed and still deletable, and re-running
the delete converges. The reverse order strands graph nodes whose owning row is gone —
invisible to every query, reclaimable by nothing.

`confirm_name` must equal the current name exactly. The last-knowledge-base guard keeps a
user from deleting themselves into a state where B's `/app` has nowhere to redirect.

## Scoping the existing routes

Each endpoint's body becomes a plain function taking `knowledge_base_id`, with two thin
wrappers over it:

```python
def _list_keys(session, kb_id) -> list[ApiKeyOut]: ...   # the logic, knowledge-base-agnostic

@router.get("/api/keys")                                 # legacy — the knowledge base is implied
def list_keys_legacy(user = Depends(current_user)):
    kb_id = home_knowledge_base_id(session, user.id)
    require_membership(session, user.id, kb_id, "admin")
    return _list_keys(session, kb_id)

@router.get("/api/knowledge-bases/{kb_id}/keys")         # scoped — the knowledge base is explicit
def list_keys_scoped(kb_id: str, user = Depends(current_user)):
    require_membership(session, user.id, kb_id, "admin")
    return _list_keys(session, kb_id)
```

Applied to `routes_keys.py`, `routes_ingest.py`, `routes_settings.py`, and `graph_api.py`.

Mounting one router twice under `prefix="/api/knowledge-bases/{kb_id}"` would halve this and
make drift impossible, but it depends on FastAPI resolving the same parameter as a path
parameter under one mount and a query parameter under the other. Rejected as too subtle for
the saving. The explicit pair duplicates wiring only, never logic.

**Legacy routes carry the same role checks.** They differ only in how the knowledge base is
chosen, never in what is permitted. Anything less makes the unscoped path a standing
authorization bypass for as long as it exists.

## Verification

The role matrix cannot be observed in the running application, because nothing can assign a
role. Tests are the proof.

- A fixture creating a knowledge base with the caller at a **given** role — the primitive
  missing today, since every real membership is `owner`.
- Per capability, the boundary pair: the highest role refused and the lowest allowed.
  `editor` may ingest and `reader` may not; `admin` may write config and `editor` may not.
- A non-member receives **404, not 403**, asserted explicitly. It is a security property
  that would otherwise regress silently.
- Legacy and scoped paths return identical results for the same knowledge base.
- Delete purges Neo4j and every Postgres child; refuses a mismatched `confirm_name`; refuses
  the caller's last knowledge base; and converges when re-run after a simulated
  Neo4j-succeeded, Postgres-failed split.
- The existing 151 tests still pass, and the desk UI still works untouched — the check that
  A really is invisible.

Delete tests need Neo4j and follow that suite's existing convention of skipping when the
store is unreachable.

## Out of scope

- **Sub-project B**: the desk UI — `/app/:kbId/*`, the switcher, the management page, and
  retiring the legacy routes.
- Inviting users, changing a member's role, transferring ownership. These need an invite
  flow, which is its own project. Until one exists, every membership is `owner`.
- Soft delete. Delete is permanent.
- `ON DELETE CASCADE` migrations.
- Per-knowledge-base quotas, billing, or audit logging.

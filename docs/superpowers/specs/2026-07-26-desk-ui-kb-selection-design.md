# Desk UI: knowledge-base selection and management — Design

**Status:** design. Captured 2026-07-26.

**Goal:** let a user reach any of their knowledge bases from the desk UI, and create,
rename and delete them.

This is **sub-project B** of two. **Sub-project A** — merged at `e957c14`,
`docs/superpowers/specs/2026-07-26-kb-scoped-api-design.md` — made the API able to answer
"which knowledge base?" by naming it in the URL, added CRUD endpoints, and enforced the
four roles. A deliberately changed nothing a user could see. B is where it becomes
visible.

## The problem, concretely

The operator has three knowledge bases and can reach only one:

| Knowledge base | Membership | Entities |
| --- | --- | --- |
| Default Organization | 2026-07-23 | 0 |
| Default Knowledge Base | 2026-07-25 | 0 |
| HN Demo | 2026-07-26 | **360** |

`accounts.home_knowledge_base_id` returns the *earliest* membership, so every
cookie-authenticated route resolves to Default Organization — which is empty. The Explore
page has always shown zero nodes, correctly. All 360 real entities sit in HN Demo, with no
way to reach them.

## Routes

```
/app                      knowledge bases — list, create, rename, delete
/app/:kbId                API keys (today's dashboard)
/app/:kbId/ingest
/app/:kbId/config
/app/:kbId/explore
/app/:kbId/entity/:id
```

Bare `/app` becomes the list rather than redirecting somewhere. A redirect would need to
remember a "last used" knowledge base, which is per-browser hidden state — the same thing
naming the knowledge base in the URL was chosen to avoid. Making `/app` the list costs one
click on entry and removes the concept entirely. It also gives the management screen an
obvious home.

**No switcher dropdown.** The header gains a `Knowledge bases` link back to `/app` and
displays the current knowledge base's name for orientation. Switching means going back and
clicking in. A dropdown that preserves the current sub-page is a nicer flow and was
considered; it was rejected as unearned for three knowledge bases, and it needs a per-page
mapping plus a special case for `/entity/:id`, whose id cannot transfer between tenants.

## Plumbing

Narrower than the route count suggests. Every server-side read goes through
`app/lib/auth.server.ts` and every client mutation through `app/lib/api.ts`.

| Now | Becomes |
| --- | --- |
| `getKeys(request)` → `/api/keys` | `getKeys(request, kbId)` → `/api/knowledge-bases/{kbId}/keys` |
| `getConfig(request)` → `/api/config` | `getConfig(request, kbId)` → `…/{kbId}/config` |
| `getEntity(request, id)` → `/api/graphql` | `getEntity(request, kbId, id)` → `…/{kbId}/graphql` |
| `createKey`, `revokeKey`, `putConfig`, `ingest` | each takes `kbId` first |

`app/lib/nav.ts` turns from the `APP_NAV_LINKS` constant into `appNavLinks(kbId)` — one
edit, five consumers.

Two components fetch `/api/graphql` directly and are easy to miss:
`app/components/graph-explorer.tsx:39` and `app/components/graphiql-panel.tsx:39`. Both take
`kbId` as a prop.

The five existing route files change shallowly: read `params.kbId`, pass it down, call
`appNavLinks(kbId)`. They are not rewritten.

## A knowledge base that is not yours

A returns **404 for a knowledge base that does not exist, one the caller is not a member
of, and one where their role is too low** — deliberately indistinguishable, so a 403 cannot
confirm existence. The loaders therefore cannot tell those cases apart either, and should
not pretend to.

Every scoped loader treats a 404 from the API as "this knowledge base is not yours" and
**redirects to `/app`** rather than throwing a route error. A stale bookmark, a revoked
membership, and a knowledge base someone just deleted all land softly on the list — which
is also exactly where a user wants to be immediately after deleting the knowledge base they
were sitting in.

This requires a change to how `auth.server.ts` reports failure. Today each function
swallows everything into an empty default — `getKeys` returns `[]`, `getConfig` returns
`EMPTY_CONFIG` — so a loader cannot tell "not your knowledge base" from "backend
unreachable". Those two need opposite responses: the first is a redirect, the second should
degrade to an empty page rather than bounce the user somewhere confusing.

So the scoped readers distinguish the two: a **404 specifically** raises a sentinel the
loader catches and turns into `redirect("/app")`; every other failure keeps today's
behaviour and returns the empty default. Anything less either swallows the 404 — leaving
the user on a page that renders as empty rather than telling them the knowledge base is not
theirs — or redirects on a transient backend blip.

## The list page

One new route file. Each row: name, the caller's role, created date; the row links to
`/app/:kbId`.

**Create** — an inline form (name, optional charter) in the shape `dashboard.tsx` already
uses for API keys. On success it navigates into the new knowledge base; creating one and
then hunting for it in a list is a wasted step.

**Rename** — inline, per row.

**Delete** — A requires `confirm_name` to equal the name exactly, so the UI collects it: a
per-row confirmation that requires typing the name. That is the endpoint's contract, not
added ceremony; the endpoint has it because the delete is permanent and takes the graph
with it.

**Rename and delete appear only when the caller's role is `owner`.** The list already
returns `role`, and A 404s a non-owner attempting either. Every membership is `owner` today,
so this is invisible — but the UI should not offer an action the API will refuse.

**Three errors are surfaced, not swallowed.** `auth.server.ts` returns empty defaults on
failure, which is right for a read that can degrade — an empty key list is a survivable
page. It is wrong here, where the user needs to know why nothing happened:

| Response | Means | Shown as |
| --- | --- | --- |
| `409` | Deleting the caller's only knowledge base | "You can't delete your only knowledge base." |
| `422` | Typed name did not match | "That doesn't match the name." |
| `403` | Email unverified, on create | The existing `VerifyEmailBanner` |

## Retiring the legacy routes

A kept the unscoped routes alive so the UI would keep working while A shipped. Once B's UI
calls only the scoped paths, they are dead. B deletes them:

- `routes_keys.py` — 3 legacy wrappers
- `routes_settings.py` — 2
- `routes_ingest.py` — 2
- `graph_api.py` — `get_cookie_context` and `cookie_graphql_router`
- `accounts.py` — `home_knowledge_base_id`
- their tests

Verified safe: the only callers are in `app/`. neonews uses the Bearer-authenticated
`/content` and `/graphql` routes, which have no `/api` prefix and are untouched by either
sub-project.

This also removes the legacy preamble that was copy-pasted across eight call sites. A
refactor collapsing it into a `require_home_membership` helper was queued and deliberately
skipped: B deletes all eight sites, so collapsing them first would have been work with a
lifetime of one sub-project.

Leaving the legacy routes in place instead was considered and rejected. They would remain a
second, wrong way to reach data — `/api/keys` answering for "your earliest knowledge base"
forever, which is the exact bug B exists to fix.

## Verification

`app/` has no test setup — no vitest config and no test script, unlike `neonews-site`.
Adding one is out of scope. B is verified in a browser against the running stack:

- Every page loads under a knowledge base: keys, ingest, config, explore, an entity page.
- `/app` lists all three knowledge bases with roles.
- Create lands in the new knowledge base. Rename shows the new name. Delete refuses a
  mismatched name and refuses the caller's only knowledge base.
- A foreign or malformed `kbId` redirects to `/app`.
- **`/app/<HN Demo id>/explore` shows 360 nodes** — the thing that has never worked, and the
  single check that proves the whole two-project effort.

After the legacy deletion, the engine's own suite must still be green — that is what proves
nothing outside `app/` depended on those routes.

## Out of scope

- Inviting users, changing a member's role, transferring ownership. These need an invite
  flow, which is its own project. Until one exists, every membership is `owner`.
- Entity counts or last-activity in the list. Answering "which knowledge base has my data"
  from the list would need a Neo4j aggregate per listing; B stays out of the backend except
  to delete.
- A switcher dropdown.
- A test harness for `app/`.

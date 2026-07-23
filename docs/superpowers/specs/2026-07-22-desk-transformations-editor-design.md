# Desk — Transformations Editor (`/desk/:org_id/transformations`)

**Status:** approved design
**Date:** 2026-07-22

## Goal

A full CRUD editor for an org's transform chain. The view lists the org's
`transformations` rows and lets a user create, edit, delete, and drag-to-reorder
them, writing changes back to Postgres. It is the first real screen in the `app/`
frontend (currently a bootstrap with one index route and one `Button`).

## Shape

- **Full CRUD**, scoped to the `:org_id` path param.
- The view is a rich, client-interactive editor. The route **SSR-renders its
  initial data** (consistent with the isomorphic app), then hydrates and performs
  all subsequent mutations client-side via `fetch` — no full page reloads after
  first paint, so it feels like an SPA while staying SSR-native.
- **No auth yet.** Resource routes scope every query by `:org_id`. Auth is a
  separate later design; nothing here should assume a session exists.

## Architecture & data flow

```
Browser (hydrated editor)
   │  fetch (GET/POST/PATCH/DELETE)  via useFetcher
   ▼
RR8 resource routes  ── app/routes/api.transformations.ts, api.transformations.$id.ts
   │  call
   ▼
app/services/transformations.server.ts   (pure-ish data ops)
   │  Drizzle
   ▼
Postgres (transformations)   ← Python/Alembic owns the schema; Node never migrates
```

- **Initial load:** the view route `loader` (server) calls `listTransformations(orgId)`
  directly for SSR.
- **All mutations:** client `useFetcher` → resource routes → the same service
  functions. One data-ops code path, exercised from both SSR and client.
- **DB access is server-only** (`.server.ts` modules + resource routes/loaders), so
  Drizzle and `postgres` never enter the client bundle.
- **Pure core, side effects at the edges:** `transformations.server.ts` holds the
  data operations; routes are the IO boundary.

## REST surface (flat)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/transformations?org_id=…` | list, ordered by `position` |
| POST | `/api/transformations` | create (server assigns `position = max+1`) |
| PATCH | `/api/transformations` | batch reorder (body: ordered id list) |
| PATCH | `/api/transformations/:id` | update one row |
| DELETE | `/api/transformations/:id` | delete one row |

### Reorder atomicity (the one non-obvious piece)

The table has `UNIQUE(org_id, position)`. A naïve row-by-row position rewrite
collides mid-update. `reorderTransformations(orgId, orderedIds)` runs in **one
transaction with a two-phase renumber**: offset all affected rows to a
non-colliding range (e.g. negative positions), then set the final positions.

## Query & validation

- **Drizzle, introspected.** `drizzle-kit introspect` generates a read-only
  `app/db/schema.ts` mirroring the live tables. Node gets compile-time column types
  but is never a migration authority; regenerate when Alembic changes the schema.
  `app/db/client.server.ts` builds the connection from a `DATABASE_URL` env var
  (same Postgres instance, web-owned `.env`, gitignored).
- **One shared Zod schema** (`app/schemas/transformation.ts`) validates in the
  resource-route actions *and* drives the client form → isomorphic validation,
  single source of truth.
- **Deviation from house style (accepted):** the style guide names Yup; we use Zod
  here because it pairs natively with Drizzle (`drizzle-zod`) and RR8.

### Field rules (from `models.py`)

Required on create: `org_id`, `position`, `type`, `prompt`. Optional: `model`,
`params`. Server-defaulted: `id`, `created_at`, `updated_at`. `created_by_id` /
`updated_by_id` are nullable — left null until auth exists. `type` ∈
{`score`, `summarize`, `classify`}. `params` is JSONB (nullable).

## Editor UI

`app/routes/desk.transformations.tsx` → `/desk/:org_id/transformations`, registered
explicitly in `routes.ts`.

- **One inline table.** Columns: drag handle · position · type (`select`) · model
  (`input`) · prompt (`textarea`) · params · row actions (delete).
- **Params cell:** typed inputs for `temperature / top_p / top_k / max_tokens` plus a
  raw-JSON `textarea` for extra keys (backend `LLMParams` allows extras). All under
  the shared Zod schema.
- **Drag-to-reorder** via `@dnd-kit/sortable` (focused, accessible; justified over
  fiddly non-a11y native HTML5 drag).
- **Editing model:** per-row dirty state, autosave on blur with a subtle saving
  indicator; an "add" row appends via POST; delete confirms inline.
- **No custom CSS:** shadcn `table/input/select/textarea/label` + `sonner` toasts,
  Tailwind utilities only.

### Error handling

- **Validation errors** (Zod): surfaced inline on the offending cell — specific and
  actionable (which field, why).
- **System errors:** resource routes return a generic message; client shows a toast
  ("Couldn't save — try again"). Internals never leak to the user.
- **Optimistic reorder:** reorder updates the client list immediately; on failure it
  reverts and toasts.

## Dependencies & setup

- **Runtime:** `drizzle-orm`, `postgres`, `zod`, `@dnd-kit/core`,
  `@dnd-kit/sortable`, `@dnd-kit/utilities`
- **Dev:** `drizzle-kit`, plus a minimal **Vitest + React Testing Library** setup
  (none exists yet)
- **shadcn components:** `table input select textarea label sonner`

## Testing

Behavior-first, snapshot where it fits, proportional to risk:

- **Shared Zod schema** — unit tests (valid/invalid params, required fields, type enum).
- **`reorderTransformations`** — the two-phase renumber against the unique constraint
  (the highest-risk logic).
- **Editor component** — a snapshot of the default rendered table, then one variable
  changed (add row, edit cell) to verify primary/secondary effects.

## Out of scope (explicit)

- Auth / sessions / authorization.
- Managing anything other than `transformations` (feeds, runs, artifacts).
- Sharing validation schemas across the Node web app and the Python service — Zod
  lives in `app/` only; Python keeps pydantic.

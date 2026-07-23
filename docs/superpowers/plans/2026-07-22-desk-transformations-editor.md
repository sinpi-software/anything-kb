# Desk Transformations Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full CRUD editor at `/desk/:org_id/transformations` that lists an org's transform chain and lets a user create, edit, delete, and drag-to-reorder transformations, persisting to Postgres.

**Architecture:** The route SSR-renders its initial data via a server `loader`, then hydrates into an interactive client editor that mutates through flat RR8 resource routes (`/api/transformations`). Resource routes call a server-only service module that talks to Postgres via Drizzle (schema introspected from the Python-owned DB). One shared Zod schema validates on both server and client.

**Tech Stack:** React Router 8, React 19, Vite 8, TypeScript (strict), Drizzle ORM + postgres.js, Zod, @dnd-kit, shadcn/ui (base-ui), Tailwind v4, Vitest + React Testing Library.

## Global Constraints

- Node `>=22.22.0`; run all `npm`/`npx` in `app/` under nvm 22.23.1 (`cd app && nvm use` first). The shell default (22.13.1) is too old.
- TypeScript strict; no `any`; explicit return types on exported functions.
- No custom CSS — shadcn components + Tailwind utilities only.
- Environment-driven config: DB URL from `DATABASE_URL`, never hardcoded. `app/.env` is gitignored.
- Flat REST: resources at top-level `/api/transformations` and `/api/transformations/:id`.
- One shared Zod schema (`app/schemas/transformation.ts`) is the single source of validation truth, used by resource routes and the client form.
- DB access is server-only (`*.server.ts` modules, loaders, resource routes) — Drizzle/postgres never enter the client bundle.
- **Prerequisite already satisfied:** `transformations.transformations_org_id_position_key` is `DEFERRABLE INITIALLY DEFERRED` (verified `condeferrable=t`). Reorder relies on this — do not add a two-phase renumber.
- Python/Alembic owns the schema. Node never migrates; it only introspects.

---

## File Structure

- `app/.env` — `DATABASE_URL` (gitignored)
- `drizzle.config.ts` — introspect config (camelCase keys)
- `app/db/schema.ts` — **generated** by `drizzle-kit introspect`; do not hand-edit
- `app/db/client.server.ts` — postgres.js + Drizzle client; `db`, `closeDb`
- `app/schemas/transformation.ts` — Zod schemas, types, `TRANSFORMATION_TYPES`, `parseParams`
- `app/services/transformations.server.ts` — `list/create/update/delete/reorderTransformations`
- `app/routes/api.transformations.ts` — GET list, POST create, PATCH reorder
- `app/routes/api.transformations.$id.ts` — PATCH update, DELETE
- `app/routes/desk.transformations.tsx` — view: `loader` + editor component
- `app/components/transformations/ParamsFields.tsx` — typed param inputs + raw-JSON fallback
- `app/routes.ts` — register routes (modify)
- `app/root.tsx` — add `<Toaster />` (modify)
- `app/components/ui/*` — shadcn: table, input, select, textarea, label, sonner
- `vitest.config.ts`, `app/test/setup.ts` — test harness
- `app/schemas/transformation.test.ts`, `app/services/transformations.reorder.test.ts`, `app/routes/desk.transformations.test.tsx` — tests

---

## Task 1: Drizzle + DB client foundation

**Files:**
- Create: `app/.env`, `drizzle.config.ts`, `app/db/client.server.ts`
- Generated: `app/db/schema.ts`
- Modify: `app/.gitignore`

**Interfaces:**
- Produces: `db` (Drizzle instance) and `closeDb(): Promise<void>` from `~/db/client.server`; generated `transformations` table object from `~/db/schema` with camelCase columns: `id, createdAt, updatedAt, orgId, position, type, model, prompt, params, createdById, updatedById`.

- [ ] **Step 1: Install dependencies**

```bash
cd app && nvm use
npm install drizzle-orm postgres dotenv
npm install -D drizzle-kit
```

- [ ] **Step 2: Create `app/.env` and gitignore it**

Copy the same connection string the ingestion service uses (`INGESTION_POSTGRES_URL` in the repo-root `.env`) into `app/.env` as `DATABASE_URL`. Do not print the password to the terminal.

`app/.env`:
```
DATABASE_URL=postgresql://ingestion:PASSWORD@localhost:5432/ingestion
```

Append to `app/.gitignore` (create the line if absent):
```
.env
```

- [ ] **Step 3: Write `drizzle.config.ts`**

```ts
import "dotenv/config";
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./app/db/schema.ts",
  out: "./app/db",
  dbCredentials: { url: process.env.DATABASE_URL as string },
  introspect: { casing: "camel" },
});
```

- [ ] **Step 4: Introspect the live schema**

Run: `cd app && npx drizzle-kit introspect`
Expected: writes `app/db/schema.ts` (plus `relations.ts` and a `meta/` folder). Open `app/db/schema.ts` and confirm it exports a `transformations` table with camelCase columns (`orgId`, `position`, `type`, `model`, `prompt`, `params`, `createdAt`, `updatedAt`, `createdById`, `updatedById`). If casing differs, fix `introspect.casing` and re-run.

- [ ] **Step 5: Write `app/db/client.server.ts`**

```ts
import "dotenv/config";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

const url = process.env.DATABASE_URL;
if (!url) throw new Error("DATABASE_URL is not set");

const client = postgres(url);

export const db = drizzle(client);

export async function closeDb(): Promise<void> {
  await client.end();
}
```

- [ ] **Step 6: Smoke-test the connection**

Run:
```bash
cd app && npx tsx -e "import { db, closeDb } from './app/db/client.server.ts'; import { transformations } from './app/db/schema.ts'; const rows = await db.select().from(transformations); console.log('rows:', rows.length); await closeDb();"
```
(If `tsx` is unavailable, `npm i -D tsx` first.)
Expected: prints `rows: 2` (the seeded summarize→score chain).

- [ ] **Step 7: Commit**

```bash
git add app/drizzle.config.ts app/db/client.server.ts app/db/schema.ts app/db/relations.ts app/db/meta app/.gitignore app/package.json app/package-lock.json
git commit -m "feat(app): drizzle client + introspected schema"
```

---

## Task 2: Shared Zod schema + params parsing

**Files:**
- Create: `app/schemas/transformation.ts`
- Test: `app/schemas/transformation.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TRANSFORMATION_TYPES: readonly ["score","summarize","classify"]`
  - `type TransformationType`
  - `transformationInputSchema` (Zod) and `type TransformationInput = { type: TransformationType; model?: string | null; prompt: string; params?: Record<string, unknown> | null }`
  - `reorderSchema` (Zod) → `{ ids: string[] }`
  - `parseParams(raw: string): { ok: true; value: Record<string, unknown> | null } | { ok: false; error: string }`

Vitest harness is not set up until Task 5. To keep this task independently testable now, run its test with a one-off Vitest invocation; Task 5 formalizes the config.

- [ ] **Step 1: Install Zod and Vitest**

```bash
cd app && npm install zod && npm install -D vitest
```

- [ ] **Step 2: Write the failing test**

`app/schemas/transformation.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { transformationInputSchema, reorderSchema, parseParams, TRANSFORMATION_TYPES } from "./transformation";

describe("transformationInputSchema", () => {
  const valid = { type: "score", model: "openai/gpt-4o", prompt: "Rate it", params: { temperature: 0.2 } };

  it("accepts a valid input", () => {
    expect(transformationInputSchema.safeParse(valid).success).toBe(true);
  });

  it("rejects an unknown type", () => {
    const r = transformationInputSchema.safeParse({ ...valid, type: "nope" });
    expect(r.success).toBe(false);
  });

  it("requires a non-empty prompt", () => {
    const r = transformationInputSchema.safeParse({ ...valid, prompt: "  " });
    expect(r.success).toBe(false);
  });

  it("allows null model and null params", () => {
    expect(transformationInputSchema.safeParse({ type: "summarize", model: null, prompt: "x", params: null }).success).toBe(true);
  });

  it("rejects temperature above range", () => {
    const r = transformationInputSchema.safeParse({ ...valid, params: { temperature: 9 } });
    expect(r.success).toBe(false);
  });

  it("keeps unknown param keys (passthrough)", () => {
    const r = transformationInputSchema.parse({ ...valid, params: { seed: 42 } });
    expect(r.params).toEqual({ seed: 42 });
  });
});

describe("reorderSchema", () => {
  it("accepts a list of uuids", () => {
    expect(reorderSchema.safeParse({ ids: ["11111111-1111-1111-1111-111111111111"] }).success).toBe(true);
  });
  it("rejects an empty list", () => {
    expect(reorderSchema.safeParse({ ids: [] }).success).toBe(false);
  });
});

describe("parseParams", () => {
  it("returns null for empty input", () => {
    expect(parseParams("   ")).toEqual({ ok: true, value: null });
  });
  it("parses a JSON object", () => {
    expect(parseParams('{"top_k":5}')).toEqual({ ok: true, value: { top_k: 5 } });
  });
  it("errors on invalid JSON", () => {
    const r = parseParams("{not json");
    expect(r.ok).toBe(false);
  });
  it("errors on non-object JSON", () => {
    const r = parseParams("42");
    expect(r.ok).toBe(false);
  });
});

it("exposes the three transform types", () => {
  expect(TRANSFORMATION_TYPES).toEqual(["score", "summarize", "classify"]);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd app && npx vitest run app/schemas/transformation.test.ts`
Expected: FAIL — cannot resolve `./transformation`.

- [ ] **Step 4: Write the implementation**

`app/schemas/transformation.ts`:
```ts
import { z } from "zod";

export const TRANSFORMATION_TYPES = ["score", "summarize", "classify"] as const;
export type TransformationType = (typeof TRANSFORMATION_TYPES)[number];

export const llmParamsSchema = z
  .object({
    temperature: z.number().min(0).max(2).optional(),
    top_p: z.number().min(0).max(1).optional(),
    top_k: z.number().int().min(0).optional(),
    max_tokens: z.number().int().positive().optional(),
  })
  .passthrough();

export const transformationInputSchema = z.object({
  type: z.enum(TRANSFORMATION_TYPES),
  model: z.string().trim().min(1).nullable().optional(),
  prompt: z.string().trim().min(1, "Prompt is required"),
  params: llmParamsSchema.nullable().optional(),
});
export type TransformationInput = z.infer<typeof transformationInputSchema>;

export const reorderSchema = z.object({
  ids: z.array(z.string().uuid()).min(1),
});

type ParseParamsResult =
  | { ok: true; value: Record<string, unknown> | null }
  | { ok: false; error: string };

export function parseParams(raw: string): ParseParamsResult {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: null };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: "Params must be a JSON object" };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, error: "Invalid JSON" };
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd app && npx vitest run app/schemas/transformation.test.ts`
Expected: PASS (all cases).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/transformation.ts app/schemas/transformation.test.ts app/package.json app/package-lock.json
git commit -m "feat(app): shared zod schema for transformations"
```

---

## Task 3: Transformations service (CRUD + reorder)

**Files:**
- Create: `app/services/transformations.server.ts`
- Test: `app/services/transformations.reorder.test.ts` (integration — hits the docker DB)

**Interfaces:**
- Consumes: `db`, `closeDb` from `~/db/client.server`; `transformations` from `~/db/schema`; `TransformationInput` from `~/schemas/transformation`.
- Produces:
  - `type TransformationRow = typeof transformations.$inferSelect`
  - `listTransformations(orgId: string): Promise<TransformationRow[]>`
  - `createTransformation(orgId: string, input: TransformationInput): Promise<TransformationRow>`
  - `updateTransformation(id: string, input: TransformationInput): Promise<TransformationRow | null>`
  - `deleteTransformation(id: string): Promise<void>`
  - `reorderTransformations(orgId: string, ids: string[]): Promise<void>`

- [ ] **Step 1: Set up the Vitest harness (first task whose tests need `~` alias resolution)**

The service uses `~/db/…` alias imports, so tests loading it need `vite-tsconfig-paths`. Establish the shared harness here; Task 5 reuses it.

```bash
cd app && nvm use
npm install -D vite-tsconfig-paths jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Add a `test` script to `app/package.json` `scripts`: `"test": "vitest run"`.

Create `app/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./app/test/setup.ts"],
  },
});
```

Create `app/app/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

The `environment: "jsdom"` default is for later component tests; the DB integration test below opts out per-file with `// @vitest-environment node`.

- [ ] **Step 2: Write the implementation**

`app/app/services/transformations.server.ts`:
```ts
import { and, asc, eq, sql } from "drizzle-orm";
import { db } from "~/db/client.server";
import { transformations } from "~/db/schema";
import type { TransformationInput } from "~/schemas/transformation";

export type TransformationRow = typeof transformations.$inferSelect;

export function listTransformations(orgId: string): Promise<TransformationRow[]> {
  return db
    .select()
    .from(transformations)
    .where(eq(transformations.orgId, orgId))
    .orderBy(asc(transformations.position));
}

export async function createTransformation(orgId: string, input: TransformationInput): Promise<TransformationRow> {
  const [{ next }] = await db
    .select({ next: sql<number>`coalesce(max(${transformations.position}) + 1, 0)` })
    .from(transformations)
    .where(eq(transformations.orgId, orgId));

  const [row] = await db
    .insert(transformations)
    .values({
      orgId,
      position: next,
      type: input.type,
      model: input.model ?? null,
      prompt: input.prompt,
      params: input.params ?? null,
    })
    .returning();
  return row;
}

export async function updateTransformation(id: string, input: TransformationInput): Promise<TransformationRow | null> {
  const [row] = await db
    .update(transformations)
    .set({
      type: input.type,
      model: input.model ?? null,
      prompt: input.prompt,
      params: input.params ?? null,
      updatedAt: new Date(),
    })
    .where(eq(transformations.id, id))
    .returning();
  return row ?? null;
}

export async function deleteTransformation(id: string): Promise<void> {
  await db.delete(transformations).where(eq(transformations.id, id));
}

export async function reorderTransformations(orgId: string, ids: string[]): Promise<void> {
  // The unique(org_id, position) constraint is DEFERRABLE INITIALLY DEFERRED,
  // so we can reassign positions row-by-row inside one transaction; Postgres
  // validates uniqueness once at COMMIT.
  await db.transaction(async (tx) => {
    for (let i = 0; i < ids.length; i++) {
      await tx
        .update(transformations)
        .set({ position: i })
        .where(and(eq(transformations.id, ids[i]), eq(transformations.orgId, orgId)));
    }
  });
}
```

If TypeScript complains that `params` is `unknown` on insert/update (jsonb columns infer `unknown`), cast the value: `params: (input.params ?? null) as typeof transformations.$inferInsert["params"]`.

- [ ] **Step 3: Write the failing integration test**

`app/app/services/transformations.reorder.test.ts` (note the `@vitest-environment node` pragma — this test hits Postgres and does not need jsdom):
```ts
// @vitest-environment node
import { afterAll, describe, expect, it } from "vitest";
import { and, asc, eq } from "drizzle-orm";
import { db, closeDb } from "~/db/client.server";
import { orgs, transformations } from "~/db/schema";
import { reorderTransformations } from "./transformations.server";

const TEST_ORG = "reorder-test-org";

async function seedChain(): Promise<{ orgId: string; ids: string[] }> {
  const [org] = await db.insert(orgs).values({ name: TEST_ORG }).returning();
  const rows = await db
    .insert(transformations)
    .values([
      { orgId: org.id, position: 0, type: "summarize", prompt: "a" },
      { orgId: org.id, position: 1, type: "score", prompt: "b" },
      { orgId: org.id, position: 2, type: "classify", prompt: "c" },
    ])
    .returning();
  return { orgId: org.id, ids: rows.map((r) => r.id) };
}

async function cleanup(orgId: string): Promise<void> {
  await db.delete(transformations).where(eq(transformations.orgId, orgId));
  await db.delete(orgs).where(eq(orgs.id, orgId));
}

afterAll(async () => {
  await closeDb();
});

describe("reorderTransformations (integration)", () => {
  it("reverses order without tripping the unique constraint", async () => {
    const { orgId, ids } = await seedChain();
    try {
      const reversed = [...ids].reverse();
      await reorderTransformations(orgId, reversed);

      const after = await db
        .select()
        .from(transformations)
        .where(eq(transformations.orgId, orgId))
        .orderBy(asc(transformations.position));

      expect(after.map((r) => r.id)).toEqual(reversed);
      expect(after.map((r) => r.position)).toEqual([0, 1, 2]);
    } finally {
      await cleanup(orgId);
    }
  });
});
```

- [ ] **Step 4: Run test to verify it fails, then passes**

Run: `cd app && npx vitest run app/app/services/transformations.reorder.test.ts`
Write the test first and confirm it fails (RED) — before the service exists it fails to import `./transformations.server`. With the service written it should PASS. If it errors that `orgs` is not exported from `~/db/schema`, open `app/app/db/schema.ts` and use the actual exported name for the orgs table.
Expected: PASS. (Requires the docker Postgres running.)

- [ ] **Step 5: Commit**

```bash
git add app/app/services/transformations.server.ts app/app/services/transformations.reorder.test.ts app/vitest.config.ts app/app/test/setup.ts app/package.json app/package-lock.json
git commit -m "feat(app): transformations service with deferred-constraint reorder"
```

---

## Task 4: REST resource routes

**Files:**
- Create: `app/routes/api.transformations.ts`, `app/routes/api.transformations.$id.ts`
- Modify: `app/routes.ts`

**Interfaces:**
- Consumes: service functions from `~/services/transformations.server`; `transformationInputSchema`, `reorderSchema` from `~/schemas/transformation`.
- Produces HTTP endpoints:
  - `GET /api/transformations?org_id=…` → `TransformationRow[]`
  - `POST /api/transformations` (body `{ org_id, type, model?, prompt, params? }`) → 201 `TransformationRow`
  - `PATCH /api/transformations` (body `{ org_id, ids: string[] }`) → `{ ok: true }`
  - `PATCH /api/transformations/:id` (body `{ type, model?, prompt, params? }`) → `TransformationRow`
  - `DELETE /api/transformations/:id` → `{ ok: true }`
  - Validation failures → 422 `{ errors }`; missing org → 400.

- [ ] **Step 1: Register the routes**

`app/routes.ts` (replace entire file):
```ts
import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("desk/:org_id/transformations", "routes/desk.transformations.tsx"),
  route("api/transformations", "routes/api.transformations.ts"),
  route("api/transformations/:id", "routes/api.transformations.$id.ts"),
] satisfies RouteConfig;
```

Note: `desk.transformations.tsx` doesn't exist yet — Task 5 creates it. Typegen will error until then; that's fine within this task's boundary since resource routes are what's tested here. If you want a green typecheck at this task's end, create a placeholder `app/routes/desk.transformations.tsx` exporting `export default function Placeholder() { return null; }` and replace it in Task 5.

- [ ] **Step 2: Write the collection route**

`app/routes/api.transformations.ts`:
```ts
import type { Route } from "./+types/api.transformations";
import { reorderSchema, transformationInputSchema } from "~/schemas/transformation";
import { createTransformation, listTransformations, reorderTransformations } from "~/services/transformations.server";

export async function loader({ request }: Route.LoaderArgs) {
  const orgId = new URL(request.url).searchParams.get("org_id");
  if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
  return Response.json(await listTransformations(orgId));
}

export async function action({ request }: Route.ActionArgs) {
  const body = (await request.json()) as Record<string, unknown>;
  const orgId = typeof body.org_id === "string" ? body.org_id : null;

  if (request.method === "POST") {
    if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    return Response.json(await createTransformation(orgId, parsed.data), { status: 201 });
  }

  if (request.method === "PATCH") {
    const parsed = reorderSchema.safeParse(body);
    if (!orgId || !parsed.success) return Response.json({ error: "invalid reorder request" }, { status: 422 });
    await reorderTransformations(orgId, parsed.data.ids);
    return Response.json({ ok: true });
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}
```

- [ ] **Step 3: Write the item route**

`app/routes/api.transformations.$id.ts`:
```ts
import type { Route } from "./+types/api.transformations.$id";
import { transformationInputSchema } from "~/schemas/transformation";
import { deleteTransformation, updateTransformation } from "~/services/transformations.server";

export async function action({ request, params }: Route.ActionArgs) {
  const { id } = params;

  if (request.method === "PATCH") {
    const body = (await request.json()) as Record<string, unknown>;
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    const row = await updateTransformation(id, parsed.data);
    if (!row) return Response.json({ error: "not found" }, { status: 404 });
    return Response.json(row);
  }

  if (request.method === "DELETE") {
    await deleteTransformation(id);
    return Response.json({ ok: true });
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}
```

- [ ] **Step 4: Manually verify against the running server**

Start the dev server (`cd app && nvm use && npm run dev`), then in another shell (use the real seeded org id — get it with the psql query below):
```bash
ORG=$(docker exec -i $(docker compose -f ../docker-compose.yml ps -q postgres) psql -U ingestion -d ingestion -tAc "SELECT id FROM orgs LIMIT 1;")
curl -s "http://localhost:5173/api/transformations?org_id=$ORG" | head -c 400
```
Expected: a JSON array with the 2 seeded transformations.

- [ ] **Step 5: Commit**

```bash
git add app/routes/api.transformations.ts app/routes/api.transformations.\$id.ts app/routes.ts
git commit -m "feat(app): transformations REST resource routes"
```

---

## Task 5: Test harness + shadcn + read-only view

**Files:**
- Create: `app/app/routes/desk.transformations.tsx`, `app/app/components/transformations/ParamsFields.tsx`
- Modify: `app/app/root.tsx` (add `<Toaster />`)
- Add shadcn components: table, input, select, textarea, label, sonner
- Add deps: `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`

Note: the Vitest harness (`vitest.config.ts`, `app/app/test/setup.ts`, `test` script, `vite-tsconfig-paths`, jsdom, testing-library) was already established in Task 3 — do not recreate it.

**Interfaces:**
- Consumes: `listTransformations` from `~/services/transformations.server`; `TRANSFORMATION_TYPES` from `~/schemas/transformation`.
- Produces: route `desk.transformations` whose `loader` returns `{ orgId: string; transformations: TransformationRow[] }`; a `<ParamsFields>` component (used in Task 6); a passing snapshot test.

- [ ] **Step 1: Install UI deps and add shadcn components**

```bash
cd app && nvm use
npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
npx shadcn@latest add table input select textarea label sonner
```
If shadcn prompts, accept defaults (it writes to `app/app/components/ui`, matching the existing `button.tsx` there). `sonner` also adds the `sonner` package. The Vitest harness already exists from Task 3.

- [ ] **Step 2: Add `<Toaster />` to the root**

In `app/root.tsx`, import and render the toaster once inside the app body:
```tsx
import { Toaster } from "~/components/ui/sonner";
```
Add `<Toaster />` just before the closing tag that wraps `<Outlet />` (typically right after `{children}` in the `Layout` body). Keep it a single instance.

- [ ] **Step 3: Write `ParamsFields.tsx`**

`app/app/components/transformations/ParamsFields.tsx`:
```tsx
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Textarea } from "~/components/ui/textarea";

const KNOWN_KEYS = ["temperature", "top_p", "top_k", "max_tokens"] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

export type ParamsFieldsValue = {
  known: Record<KnownKey, string>;
  extraJson: string;
};

export const EMPTY_PARAMS: ParamsFieldsValue = {
  known: { temperature: "", top_p: "", top_k: "", max_tokens: "" },
  extraJson: "",
};

export function paramsFromRecord(params: Record<string, unknown> | null): ParamsFieldsValue {
  if (!params) return EMPTY_PARAMS;
  const known: Record<KnownKey, string> = { temperature: "", top_p: "", top_k: "", max_tokens: "" };
  const extra: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if ((KNOWN_KEYS as readonly string[]).includes(k)) known[k as KnownKey] = String(v);
    else extra[k] = v;
  }
  return { known, extraJson: Object.keys(extra).length ? JSON.stringify(extra) : "" };
}

export function ParamsFields({
  value,
  onChange,
}: {
  value: ParamsFieldsValue;
  onChange: (next: ParamsFieldsValue) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        {KNOWN_KEYS.map((key) => (
          <div key={key} className="flex flex-col gap-1">
            <Label htmlFor={`param-${key}`} className="text-xs text-muted-foreground">{key}</Label>
            <Input
              id={`param-${key}`}
              inputMode="decimal"
              value={value.known[key]}
              onChange={(e) => onChange({ ...value, known: { ...value.known, [key]: e.target.value } })}
            />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="param-extra" className="text-xs text-muted-foreground">extra (JSON)</Label>
        <Textarea
          id="param-extra"
          rows={2}
          placeholder='{"seed": 42}'
          value={value.extraJson}
          onChange={(e) => onChange({ ...value, extraJson: e.target.value })}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write the view route (read-only table for now, replacing the Task 4 placeholder)**

`app/app/routes/desk.transformations.tsx`:
```tsx
import type { Route } from "./+types/desk.transformations";
import { listTransformations } from "~/services/transformations.server";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export async function loader({ params }: Route.LoaderArgs) {
  const orgId = params.org_id;
  return { orgId, transformations: await listTransformations(orgId) };
}

export default function TransformationsPage({ loaderData }: Route.ComponentProps) {
  const { transformations } = loaderData;
  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Transformations</h1>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">#</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Model</TableHead>
            <TableHead>Prompt</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {transformations.map((t) => (
            <TableRow key={t.id}>
              <TableCell>{t.position}</TableCell>
              <TableCell>{t.type}</TableCell>
              <TableCell>{t.model ?? "—"}</TableCell>
              <TableCell className="max-w-md truncate">{t.prompt}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </main>
  );
}
```

- [ ] **Step 5: Write the snapshot test**

`app/app/routes/desk.transformations.test.tsx` (note: `createdAt`/`updatedAt` are **string**-mode timestamps in the introspected schema — use ISO strings, not `new Date()`, so the fixtures type-check):
```tsx
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import TransformationsPage from "./desk.transformations";

const rows = [
  { id: "1", position: 0, type: "summarize", model: null, prompt: "Summarize the article", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
  { id: "2", position: 1, type: "score", model: "openai/gpt-4o", prompt: "Score it", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
];

describe("TransformationsPage", () => {
  it("renders the transform chain table", () => {
    const Stub = createRoutesStub([
      { path: "/desk/:org_id/transformations", Component: TransformationsPage, loader: () => ({ orgId: "o", transformations: rows }) },
    ]);
    const { container } = render(<Stub initialEntries={["/desk/o/transformations"]} />);
    expect(container).toMatchSnapshot();
  });
});
```

- [ ] **Step 6: Run the tests**

Run: `cd app && npm test`
Expected: PASS — the snapshot is written on first run; schema and reorder tests still green. (Docker Postgres must be running for the reorder test.)

- [ ] **Step 7: Verify in the browser**

With `npm run dev` running, open `http://localhost:5173/desk/<seeded-org-id>/transformations`. Expected: a table listing the 2 seeded transformations (summarize at #0, score at #1). Look at it — the table renders, not a blank page or error boundary.

- [ ] **Step 8: Commit**

```bash
git add app/app/routes/desk.transformations.tsx app/app/routes/desk.transformations.test.tsx app/app/routes/__snapshots__ app/app/components/transformations/ParamsFields.tsx app/app/components/ui app/app/root.tsx app/package.json app/package-lock.json app/app/app.css app/components.json
git commit -m "feat(app): read-only transformations view + shadcn components"
```

---

## Task 6: Editing — add, inline edit, delete

**Files:**
- Modify: `app/routes/desk.transformations.tsx`

**Interfaces:**
- Consumes: `<ParamsFields>`, `paramsFromRecord`, `EMPTY_PARAMS` from `~/components/transformations/ParamsFields`; `parseParams`, `TRANSFORMATION_TYPES`, `transformationInputSchema` from `~/schemas/transformation`; the resource routes from Task 4 via `useFetcher`.
- Produces: an editor where each row edits type/model/prompt/params and autosaves on blur (PATCH `/api/transformations/:id`), an "Add transformation" control (POST `/api/transformations`), and per-row delete (DELETE) with inline confirm. Field-level Zod errors surface on the offending cell; a failed save toasts.

- [ ] **Step 1: Build the row editor and mutation helpers**

Replace the component in `app/routes/desk.transformations.tsx` with an editing version. Key pieces (write them in full):

```tsx
import { useState } from "react";
import { useFetcher, useRevalidator } from "react-router";
import { toast } from "sonner";
import { Trash2, Plus } from "lucide-react";
import type { Route } from "./+types/desk.transformations";
import { listTransformations, type TransformationRow } from "~/services/transformations.server";
import { TRANSFORMATION_TYPES, parseParams, transformationInputSchema } from "~/schemas/transformation";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { ParamsFields, paramsFromRecord, EMPTY_PARAMS, type ParamsFieldsValue } from "~/components/transformations/ParamsFields";

export async function loader({ params }: Route.LoaderArgs) {
  return { orgId: params.org_id, transformations: await listTransformations(params.org_id) };
}

type Draft = {
  type: (typeof TRANSFORMATION_TYPES)[number];
  model: string;
  prompt: string;
  params: ParamsFieldsValue;
};

function toDraft(row: TransformationRow): Draft {
  return {
    type: row.type as Draft["type"],
    model: row.model ?? "",
    prompt: row.prompt,
    params: paramsFromRecord(row.params as Record<string, unknown> | null),
  };
}

function buildPayload(draft: Draft): { ok: true; value: object } | { ok: false; error: string } {
  const paramsResult = parseParams(draft.params.extraJson);
  if (!paramsResult.ok) return { ok: false, error: paramsResult.error };
  const known: Record<string, number> = {};
  for (const [k, v] of Object.entries(draft.params.known)) {
    if (v.trim() !== "") {
      const n = Number(v);
      if (Number.isNaN(n)) return { ok: false, error: `${k} must be a number` };
      known[k] = n;
    }
  }
  const params = { ...(paramsResult.value ?? {}), ...known };
  const candidate = {
    type: draft.type,
    model: draft.model.trim() || null,
    prompt: draft.prompt,
    params: Object.keys(params).length ? params : null,
  };
  const parsed = transformationInputSchema.safeParse(candidate);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Invalid input" };
  return { ok: true, value: parsed.data };
}
```

- [ ] **Step 2: Implement the editor component with autosave, add, delete**

Continue in the same file:
```tsx
export default function TransformationsPage({ loaderData }: Route.ComponentProps) {
  const { orgId, transformations } = loaderData;
  const fetcher = useFetcher();
  const revalidator = useRevalidator();

  function save(row: TransformationRow, draft: Draft) {
    const payload = buildPayload(draft);
    if (!payload.ok) {
      toast.error(payload.error);
      return;
    }
    fetcher.submit(payload.value as Record<string, string>, {
      method: "PATCH",
      action: `/api/transformations/${row.id}`,
      encType: "application/json",
    });
  }

  function add() {
    fetcher.submit(
      { org_id: orgId, type: "summarize", prompt: "New transformation" },
      { method: "POST", action: "/api/transformations", encType: "application/json" },
    );
  }

  function remove(row: TransformationRow) {
    fetcher.submit(null, { method: "DELETE", action: `/api/transformations/${row.id}`, encType: "application/json" });
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Transformations</h1>
        <Button onClick={add}><Plus className="mr-1 size-4" /> Add</Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead className="w-40">Type</TableHead>
            <TableHead className="w-56">Model</TableHead>
            <TableHead>Prompt</TableHead>
            <TableHead>Params</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {transformations.map((row) => (
            <EditableRow key={row.id} row={row} onSave={save} onDelete={remove} />
          ))}
        </TableBody>
      </Table>
    </main>
  );
}

function EditableRow({
  row,
  onSave,
  onDelete,
}: {
  row: TransformationRow;
  onSave: (row: TransformationRow, draft: Draft) => void;
  onDelete: (row: TransformationRow) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(row));
  const [confirming, setConfirming] = useState(false);
  const commit = () => onSave(row, draft);

  return (
    <TableRow>
      <TableCell>{row.position}</TableCell>
      <TableCell>
        <Select value={draft.type} onValueChange={(type) => { const next = { ...draft, type: type as Draft["type"] }; setDraft(next); onSave(row, next); }}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {TRANSFORMATION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} onBlur={commit} placeholder="openai/gpt-4o" />
      </TableCell>
      <TableCell>
        <Textarea rows={2} value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} onBlur={commit} />
      </TableCell>
      <TableCell>
        <ParamsFields value={draft.params} onChange={(params) => setDraft({ ...draft, params })} />
        <Button variant="ghost" size="sm" className="mt-1" onClick={commit}>Save params</Button>
      </TableCell>
      <TableCell>
        {confirming ? (
          <Button variant="destructive" size="sm" onClick={() => onDelete(row)} onBlur={() => setConfirming(false)}>Sure?</Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}><Trash2 className="size-4" /></Button>
        )}
      </TableCell>
    </TableRow>
  );
}
```

Note on revalidation: RR8 automatically revalidates the loader after a fetcher submission completes, so the table refreshes after each mutation. `revalidator` is imported for explicit use if needed; remove the import if unused to satisfy the linter.

- [ ] **Step 3: Update the snapshot test for the new markup**

The Task 5 snapshot will no longer match. Run `cd app && npx vitest run app/routes/desk.transformations.test.tsx -u` to update it, then open the updated snapshot and confirm it shows the editable controls (a select, inputs, the Add button). Keep the same test file; only the snapshot changes.

- [ ] **Step 4: Run tests**

Run: `cd app && npm test`
Expected: PASS.

- [ ] **Step 5: Verify in the browser**

With the dev server running, open the view. Add a transformation (new row appears), edit a prompt and blur (persists across reload), change a type (persists), delete the added row (click trash → "Sure?" → click). Reload after each to confirm persistence.

- [ ] **Step 6: Commit**

```bash
git add app/routes/desk.transformations.tsx app/routes/__snapshots__ app/routes/desk.transformations.test.tsx
git commit -m "feat(app): editable transformations (add, inline edit, delete)"
```

---

## Task 7: Drag-to-reorder

**Files:**
- Modify: `app/routes/desk.transformations.tsx`

**Interfaces:**
- Consumes: `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`; the PATCH `/api/transformations` reorder endpoint.
- Produces: a drag handle per row that reorders rows; on drop, the client list updates optimistically and a PATCH with the new id order persists; on failure it reverts and toasts.

- [ ] **Step 1: Add optimistic ordering state and DnD context**

In `app/routes/desk.transformations.tsx`, wrap the table body in a dnd-kit sortable context and keep a local ordered copy of the rows. Replace the `TransformationsPage` body's table section with:

```tsx
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy, arrayMove, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";
import { useEffect } from "react";
```

Inside `TransformationsPage`, add:
```tsx
const [order, setOrder] = useState<TransformationRow[]>(transformations);
useEffect(() => setOrder(transformations), [transformations]);
const sensors = useSensors(useSensor(PointerSensor));

function onDragEnd(event: DragEndEvent) {
  const { active, over } = event;
  if (!over || active.id === over.id) return;
  const oldIndex = order.findIndex((r) => r.id === active.id);
  const newIndex = order.findIndex((r) => r.id === over.id);
  const next = arrayMove(order, oldIndex, newIndex);
  setOrder(next); // optimistic
  const reorderFetcher = fetcher; // reuse the page fetcher
  reorderFetcher.submit(
    { org_id: orgId, ids: next.map((r) => r.id) },
    { method: "PATCH", action: "/api/transformations", encType: "application/json" },
  );
}
```

Render the table body over `order` (not `transformations`) and wrap:
```tsx
<DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
  <SortableContext items={order.map((r) => r.id)} strategy={verticalListSortingStrategy}>
    <Table>
      {/* header unchanged, add a leading <TableHead className="w-8" /> for the handle */}
      <TableBody>
        {order.map((row) => (
          <EditableRow key={row.id} row={row} onSave={save} onDelete={remove} />
        ))}
      </TableBody>
    </Table>
  </SortableContext>
</DndContext>
```

- [ ] **Step 2: Make `EditableRow` sortable with a drag handle**

Update `EditableRow` to use `useSortable` and render a handle as the first cell:
```tsx
function EditableRow({ row, onSave, onDelete }: { row: TransformationRow; onSave: (row: TransformationRow, draft: Draft) => void; onDelete: (row: TransformationRow) => void; }) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: row.id });
  const style = { transform: CSS.Transform.toString(transform), transition };
  // ...existing draft/confirming state...
  return (
    <TableRow ref={setNodeRef} style={style}>
      <TableCell>
        <button className="cursor-grab text-muted-foreground" {...attributes} {...listeners} aria-label="Drag to reorder">
          <GripVertical className="size-4" />
        </button>
      </TableCell>
      {/* ...existing cells... */}
    </TableRow>
  );
}
```

- [ ] **Step 3: Revert + toast on reorder failure**

React to the fetcher result. After the `onDragEnd` submit, add an effect that watches `fetcher` for an error and restores server order:
```tsx
useEffect(() => {
  if (fetcher.state === "idle" && fetcher.data && (fetcher.data as { ok?: boolean }).ok === undefined && (fetcher.data as { error?: string }).error) {
    setOrder(transformations); // revert to last known-good
    toast.error("Couldn't reorder — try again");
  }
}, [fetcher.state, fetcher.data, transformations]);
```

- [ ] **Step 4: Update the snapshot**

Run: `cd app && npx vitest run app/routes/desk.transformations.test.tsx -u`
Open the snapshot; confirm each row now has a drag-handle button. `createRoutesStub` renders dnd-kit statically fine (no drag simulation needed for the snapshot).

- [ ] **Step 5: Run tests**

Run: `cd app && npm test`
Expected: PASS.

- [ ] **Step 6: Verify in the browser**

Drag a row by its handle to a new position. It moves immediately; reload and confirm the new order persisted (positions renumbered 0..n). Verify the seeded chain still runs correctly by checking the ingestion pipeline uses `order by position`.

- [ ] **Step 7: Commit**

```bash
git add app/routes/desk.transformations.tsx app/routes/__snapshots__
git commit -m "feat(app): drag-to-reorder transformations"
```

---

## Self-Review Notes

- **Spec coverage:** SSR-then-hydrate view (Task 5–7), full CRUD (Tasks 4, 6), drag reorder via dnd-kit + deferred constraint (Tasks 3, 7), resource-route REST surface (Task 4), Drizzle introspected (Task 1), shared Zod schema (Task 2, used in Tasks 4/6), typed params + raw fallback (Task 5 `ParamsFields`, Task 6 `buildPayload`), trust-`:org_id`/no-auth (loaders/routes read the param directly), no-custom-CSS (shadcn + Tailwind), env-driven DB URL (Task 1). Testing: schema unit (Task 2), reorder integration (Task 3), component snapshot (Tasks 5–7).
- **Known executor watch-points:** exact identifiers in the generated `app/db/schema.ts` (confirm `orgs`/`transformations` export names and camelCase columns at Task 1/3); shadcn base-ui `Select` API surface (Task 6) — adjust import names to whatever `shadcn add select` generates; jsonb `params` inferring `unknown` (cast noted in Task 3).

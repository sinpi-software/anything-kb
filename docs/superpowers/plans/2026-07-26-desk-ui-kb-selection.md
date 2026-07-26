# Desk UI: Knowledge-Base Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user reach any of their knowledge bases from the desk UI, and create, rename and delete them.

**Architecture:** Routes gain the knowledge base as a path segment (`/app/:kbId/…`) and bare `/app` becomes the knowledge-base list. Migration is route-by-route because each server reader in `auth.server.ts` has exactly one consumer, so no intermediate state is broken. The legacy unscoped API routes stay live throughout and are deleted in the final task.

**Tech Stack:** React Router 8 (SSR), TypeScript, Vite, Tailwind v4, pnpm. Backend is FastAPI (final task only).

**Spec:** `docs/superpowers/specs/2026-07-26-desk-ui-kb-selection-design.md` — read it first.

## Global Constraints

- **This is sub-project B of two.** Sub-project A (merged at `e957c14`) already built and tested every scoped endpoint. Do not add backend features; the only Python change in this plan is the deletion in Task 6.
- **The app has no test harness** — no vitest config, no `test` script in `app/package.json`. Adding one is out of scope. Verification is `pnpm typecheck` plus browser checks.
- **Run typecheck inside the container**, never on the host: `docker compose exec app pnpm typecheck`. The dev server runs as root and its generated `.react-router` types are root-owned, so a host run fails with `EACCES`.
- **A 404 from a scoped endpoint means "not your knowledge base"** — it does not exist, is not yours, or your role is too low, and the API deliberately does not distinguish these. Loaders redirect to `/app`. Any other failure keeps returning an empty default so an unreachable backend degrades the page instead of bouncing the user.
- **Scoped endpoint paths, exact:** `/api/knowledge-bases/{kbId}/keys`, `/api/knowledge-bases/{kbId}/keys/{keyId}`, `/api/knowledge-bases/{kbId}/config`, `/api/knowledge-bases/{kbId}/content`, `/api/knowledge-bases/{kbId}/content/{jobId}`, `/api/knowledge-bases/{kbId}/graphql`.
- **Knowledge-base endpoints, exact:** `GET /api/knowledge-bases`, `POST /api/knowledge-bases`, `PATCH /api/knowledge-bases/{id}`, `DELETE /api/knowledge-bases/{id}` with body `{"confirm_name": "..."}`.
- **Rename and delete are owner-only.** The list returns `role`; hide those actions unless it is `"owner"`.
- **Do not run `docker compose down -v`, `docker volume rm`, or `docker volume prune`** — the volumes hold real data. The operator's real graph is in the `HN Demo` knowledge base (360 entities); both `Default*` knowledge bases are empty.

---

### Task 1: Route restructure, the knowledge-base list page, and the lib foundation

Everything moves under `/app/:kbId` and `/app` becomes the list. The five existing pages keep fetching through the legacy unscoped endpoints for now — those still work, so the app stays usable while later tasks migrate them one at a time.

**Files:**
- Modify: `app/app/routes.ts`
- Modify: `app/app/lib/nav.ts`
- Modify: `app/app/lib/types.ts`
- Modify: `app/app/lib/auth.server.ts`
- Modify: `app/app/lib/api.ts`
- Create: `app/app/routes/knowledge-bases.tsx`
- Modify: `app/app/components/site-header.tsx` — optional `kbName` prop
- Modify: `app/app/routes/{dashboard,ingest,config,explore,entity}.tsx` — nav call, login-redirect paths, and `kbName` only

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `appNavLinks(kbId: string): { href: string; label: string }[]` (replaces `APP_NAV_LINKS`)
  - `KnowledgeBase` type — `{ id, name, charter, role, created_at }`
  - `KbNotFound` error class, exported from `lib/auth.server.ts`
  - `listKnowledgeBases(request: Request): Promise<KnowledgeBase[]>` in `lib/auth.server.ts`
  - `createKnowledgeBase(name, charter?)`, `renameKnowledgeBase(id, name)`, `deleteKnowledgeBase(id, confirmName)` in `lib/api.ts` — mutations only; the list is read by the loader
  - `SiteHeaderProps.kbName?: string`

- [ ] **Step 1: Add the `KnowledgeBase` type**

Append to `app/app/lib/types.ts`:

```ts
export interface KnowledgeBase {
  id: string;
  name: string;
  charter: string | null;
  role: string;
  created_at: string;
}
```

- [ ] **Step 2: Turn the nav constant into a function of the knowledge base**

Replace the entire contents of `app/app/lib/nav.ts`:

```ts
// Links shown in the header on every authenticated (dashboard) page. The knowledge
// base is part of the path now, so these are a function of it rather than a constant.
export function appNavLinks(kbId: string) {
  return [
    { href: "/app", label: "Knowledge bases" },
    { href: `/app/${kbId}/ingest`, label: "Ingest" },
    { href: `/app/${kbId}/explore`, label: "Explore" },
    { href: `/app/${kbId}/config`, label: "Configure" },
    { href: `/app/${kbId}`, label: "API keys" },
  ];
}
```

- [ ] **Step 3: Add the server-side list reader and the not-found sentinel**

Append to `app/app/lib/auth.server.ts`, and add `KnowledgeBase` to the `./types` import:

```ts
/**
 * A scoped endpoint answered 404: the knowledge base does not exist, is not the
 * caller's, or their role is too low. The API deliberately does not distinguish those
 * — a 403 would confirm the knowledge base exists to someone who may not see it.
 * Loaders catch this and redirect to /app.
 *
 * Every other failure keeps returning an empty default instead, because an unreachable
 * backend should degrade the page rather than bounce the user somewhere confusing.
 */
export class KbNotFound extends Error {}

export async function listKnowledgeBases(request: Request): Promise<KnowledgeBase[]> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases`, {
      headers: forwardCookie(request),
    });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}
```

- [ ] **Step 4: Add the knowledge-base mutations to the browser client**

Append to `app/app/lib/api.ts`, and add `KnowledgeBase` to the `./types` import:

There is deliberately no client-side list function: the page reads the list in its loader, and `useRevalidator()` re-runs that loader after every mutation, so a second fetch path would be dead code.

```ts
export const createKnowledgeBase = (name: string, charter?: string): Promise<KnowledgeBase> =>
  apiFetch<KnowledgeBase>("/knowledge-bases", {
    method: "POST",
    body: JSON.stringify({ name, charter: charter?.trim() || null }),
  });

export const renameKnowledgeBase = (id: string, name: string): Promise<KnowledgeBase> =>
  apiFetch<KnowledgeBase>(`/knowledge-bases/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });

// The API requires confirm_name to equal the current name exactly — the delete is
// permanent and takes the knowledge base's graph with it.
export const deleteKnowledgeBase = (id: string, confirmName: string): Promise<void> =>
  apiFetch<void>(`/knowledge-bases/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ confirm_name: confirmName }),
  });
```

- [ ] **Step 5: Restructure the routes**

In `app/app/routes.ts`, replace the five `app*` route lines with:

```ts
  route("app", "routes/knowledge-bases.tsx"),
  route("app/:kbId", "routes/dashboard.tsx"),
  route("app/:kbId/ingest", "routes/ingest.tsx"),
  route("app/:kbId/config", "routes/config.tsx"),
  route("app/:kbId/explore", "routes/explore.tsx"),
  route("app/:kbId/entity/:id", "routes/entity.tsx"),
```

Leave the non-app routes untouched.

- [ ] **Step 6: Write the knowledge-base list page**

Create `app/app/routes/knowledge-bases.tsx`:

```tsx
import { useState } from "react";
import { redirect, useNavigate, useRevalidator } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  ApiError,
  createKnowledgeBase,
  deleteKnowledgeBase,
  logout,
  renameKnowledgeBase,
} from "~/lib/api";
import { getMe, listKnowledgeBases } from "~/lib/auth.server";
import type { KnowledgeBase } from "~/lib/types";
import type { Route } from "./+types/knowledge-bases";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Knowledge bases — anything/kb" }];
}

export async function loader({ request }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect("/login?next=/app");
  const knowledgeBases = await listKnowledgeBases(request);
  return { me, knowledgeBases };
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function CreateForm({ disabled }: { disabled: boolean }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [charter, setCharter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const kb = await createKnowledgeBase(name, charter);
      // Straight into the new knowledge base — creating one and then hunting for it
      // in the list is a wasted step.
      await navigate(`/app/${kb.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Field>
        <FieldLabel>Name</FieldLabel>
        <Input value={name} onValueChange={setName} required placeholder="e.g. Competitor research" />
      </Field>
      <Field>
        <FieldLabel>Charter (optional)</FieldLabel>
        <Input value={charter} onValueChange={setCharter} placeholder="What this knowledge base is for" />
      </Field>
      <Button type="submit" disabled={disabled || busy || !name.trim()} className="self-start">
        {busy ? "Creating…" : "Create knowledge base"}
      </Button>
    </form>
  );
}

function RenameRow({ kb, onDone }: { kb: KnowledgeBase; onDone: () => void }) {
  const [name, setName] = useState(kb.name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await renameKnowledgeBase(kb.id, name);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Input value={name} onValueChange={setName} required />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function DeleteRow({ kb, onDone }: { kb: KnowledgeBase; onDone: () => void }) {
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await deleteKnowledgeBase(kb.id, confirmName);
      onDone();
    } catch (err) {
      // 409 "only knowledge base" and 422 "name mismatch" both arrive as ApiError with
      // the API's own detail text, which says the useful thing already.
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Alert variant="error">
        This deletes the knowledge base and everything in its graph, permanently. Type{" "}
        <strong>{kb.name}</strong> to confirm.
      </Alert>
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Input value={confirmName} onValueChange={setConfirmName} placeholder={kb.name} />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || confirmName !== kb.name}>
          {busy ? "Deleting…" : "Delete permanently"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

type RowMode = "view" | "rename" | "delete";

function KnowledgeBaseRow({ kb }: { kb: KnowledgeBase }) {
  const revalidator = useRevalidator();
  const [mode, setMode] = useState<RowMode>("view");
  const isOwner = kb.role === "owner";

  function done() {
    setMode("view");
    revalidator.revalidate();
  }

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>
            <a href={`/app/${kb.id}`} className="hover:underline">
              {kb.name}
            </a>
          </CardTitle>
          <CardDescription>
            {kb.role} · created {formatDate(kb.created_at)}
            {kb.charter ? ` · ${kb.charter}` : ""}
          </CardDescription>
        </div>
        {mode === "view" && isOwner ? (
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setMode("rename")}>
              Rename
            </Button>
            <Button variant="ghost" onClick={() => setMode("delete")}>
              Delete
            </Button>
          </div>
        ) : null}
      </div>
      {mode === "rename" ? <RenameRow kb={kb} onDone={done} /> : null}
      {mode === "delete" ? <DeleteRow kb={kb} onDone={done} /> : null}
    </Card>
  );
}

export default function KnowledgeBases({ loaderData }: Route.ComponentProps) {
  const { me, knowledgeBases } = loaderData;
  const navigate = useNavigate();

  async function signOut() {
    await logout();
    await navigate("/login");
  }

  return (
    <>
      <SiteHeader
        actions={
          <Button variant="ghost" onClick={signOut}>
            Log out
          </Button>
        }
      />
      <main className="mx-auto flex max-w-(--maxw) flex-col gap-8 px-5 py-10 sm:px-7">
        {me.email_verified ? null : <VerifyEmailBanner />}
        <div>
          <h1 className="font-display text-2xl font-semibold text-ink">Knowledge bases</h1>
          <p className="text-muted">Each one is a separate graph, with its own config and API keys.</p>
        </div>
        <div className="flex flex-col gap-3">
          {knowledgeBases.map((kb) => (
            <KnowledgeBaseRow key={kb.id} kb={kb} />
          ))}
        </div>
        <Card>
          <CardTitle>New knowledge base</CardTitle>
          <CreateForm disabled={!me.email_verified} />
        </Card>
      </main>
    </>
  );
}
```

If `Card`, `CardTitle`, `CardDescription`, `Alert`, `Field`, `FieldLabel`, `Input` or `Button` do not accept the props used above, read `app/app/routes/dashboard.tsx` and match its usage exactly rather than inventing new props — it uses all of them.

- [ ] **Step 7: Point the five existing pages at the new nav and login paths**

In each of `dashboard.tsx`, `ingest.tsx`, `config.tsx`, `explore.tsx`, `entity.tsx`:

1. Change the import `import { APP_NAV_LINKS } from "~/lib/nav";` to `import { appNavLinks } from "~/lib/nav";`.
2. Change `navLinks={APP_NAV_LINKS}` to `navLinks={appNavLinks(params.kbId)}`, taking `params` from the component's loader data or props as that file already does. Where the component does not already have `kbId`, return it from the loader (`return { me, kbId: params.kbId }`) and read it from `loaderData`.
3. Update the login redirect to include the knowledge base, e.g. in `config.tsx` change `redirect("/login?next=/app/config")` to `` redirect(`/login?next=/app/${params.kbId}/config`) ``. Each loader already receives `params` or must add it to its signature: `({ request, params }: Route.LoaderArgs)`.

Do **not** change any data fetching in this step — the five pages keep calling the legacy unscoped endpoints, which still work. Later tasks migrate them one at a time.

- [ ] **Step 7b: Show which knowledge base you are in**

Without a switcher, the header is the only thing telling a user which knowledge base they are looking at, and the pages are otherwise identical between them. The name is already available — `getMe` returns `me.knowledge_bases`, each entry carrying `knowledge_base_id` and `knowledge_base_name`.

In `app/app/components/site-header.tsx`, add an optional prop and render it between the logo and the nav:

```tsx
export interface SiteHeaderProps {
  navLinks?: { href: string; label: string }[];
  actions?: ReactNode;
  /** Name of the knowledge base the page belongs to, shown for orientation. */
  kbName?: string;
}
```

```tsx
      {kbName ? (
        <span className="font-display text-sm text-muted" title="Current knowledge base">
          {kbName}
        </span>
      ) : null}
```

Then in each of the five pages, pass it:

```tsx
kbName={me.knowledge_bases.find((kb) => kb.knowledge_base_id === kbId)?.knowledge_base_name}
```

`kbName` is optional so `home.tsx` and the knowledge-base list page — which have no single current knowledge base — pass nothing and render as before.

- [ ] **Step 8: Typecheck**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose exec app pnpm typecheck
```

Expected: clean. If `Route.ComponentProps` or `Route.LoaderArgs` are unresolved for the new route, run `docker compose exec app pnpm exec react-router typegen` first — the generated types lag a new route file.

- [ ] **Step 9: Verify in a browser**

Open `http://localhost:5173/app`. Expected: all three knowledge bases listed with roles and created dates — `Default Organization`, `Default Knowledge Base`, `HN Demo`. Click into one; the API-keys page loads at `/app/<id>` and the header shows the Knowledge bases link plus Ingest/Explore/Configure/API keys, all pointing under that id. Visit Ingest, Explore and Configure; each still loads (via the legacy endpoints).

Then exercise the management actions: create a knowledge base named `scratch-b` and confirm it lands you in `/app/<new id>`; go back to `/app`, rename it to `scratch-b2` and confirm the new name shows; delete it, first typing a wrong name to confirm the button stays disabled, then the right one. Finally attempt to delete one of your remaining knowledge bases only if you have more than one — do not delete `HN Demo` or either `Default*` knowledge base.

- [ ] **Step 10: Commit**

```bash
git add app/app/routes.ts app/app/routes/knowledge-bases.tsx app/app/lib/nav.ts app/app/components/site-header.tsx \
        app/app/lib/types.ts app/app/lib/auth.server.ts app/app/lib/api.ts \
        app/app/routes/dashboard.tsx app/app/routes/ingest.tsx app/app/routes/config.tsx \
        app/app/routes/explore.tsx app/app/routes/entity.tsx
git commit -m "feat(app): knowledge bases in the URL, and a page to manage them

/app becomes the knowledge-base list; every other app page moves under
/app/:kbId. The pages still fetch through the legacy unscoped endpoints —
later tasks migrate them one at a time, so the app stays usable throughout."
```

---

### Task 2: API keys read and write the named knowledge base

**Files:**
- Modify: `app/app/lib/auth.server.ts` — `getKeys`
- Modify: `app/app/lib/api.ts` — `createKey`, `revokeKey`, `listKeys`
- Modify: `app/app/routes/dashboard.tsx`

**Interfaces:**
- Consumes: `KbNotFound` (Task 1).
- Produces: `getKeys(request, kbId)`, `createKey(kbId, name)`, `revokeKey(kbId, id)`.

- [ ] **Step 1: Scope the server reader**

In `app/app/lib/auth.server.ts`, replace `getKeys`:

```ts
export async function getKeys(request: Request, kbId: string): Promise<ApiKey[]> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases/${kbId}/keys`, {
      headers: forwardCookie(request),
    });
    if (res.status === 404) throw new KbNotFound();
    if (!res.ok) return [];
    return await res.json();
  } catch (err) {
    // A 404 means the knowledge base is not the caller's — the loader redirects. Any
    // other failure degrades to an empty list rather than bouncing the user.
    if (err instanceof KbNotFound) throw err;
    return [];
  }
}
```

- [ ] **Step 2: Scope the browser mutations**

In `app/app/lib/api.ts`, replace the three key functions:

```ts
export const listKeys = (kbId: string): Promise<ApiKey[]> =>
  apiFetch<ApiKey[]>(`/knowledge-bases/${kbId}/keys`);

export const createKey = (kbId: string, name: string): Promise<CreatedApiKey> =>
  apiFetch<CreatedApiKey>(`/knowledge-bases/${kbId}/keys`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const revokeKey = (kbId: string, id: string): Promise<void> =>
  apiFetch<void>(`/knowledge-bases/${kbId}/keys/${id}`, { method: "DELETE" });
```

- [ ] **Step 3: Thread the knowledge base through the dashboard**

In `app/app/routes/dashboard.tsx`, change the loader to pass and return `kbId`, and catch the sentinel:

```ts
export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}`);
  try {
    const keys = await getKeys(request, params.kbId);
    return { me, keys, kbId: params.kbId };
  } catch (err) {
    if (err instanceof KbNotFound) throw redirect("/app");
    throw err;
  }
}
```

Import `KbNotFound` alongside `getKeys` and `getMe`. Then pass `kbId` from `loaderData` into every `createKey(...)` and `revokeKey(...)` call site in the component — the file calls them inside `CreateKeyForm` and the revoke handler, so both need `kbId` as a prop or closure value.

- [ ] **Step 4: Typecheck**

```bash
docker compose exec app pnpm typecheck
```

Expected: clean. Any error naming `createKey`, `revokeKey` or `getKeys` means a call site was missed — fix it rather than widening a type.

- [ ] **Step 5: Verify in a browser**

Open `/app/<HN Demo id>` — get the id from `/app`. Expected: the API-keys page lists that knowledge base's keys. Create a key named `b-test`, confirm it appears, then revoke it and confirm it shows as revoked. Then open `/app/<id of a different knowledge base>` and confirm the key list differs — that is the check proving keys are now per-knowledge-base rather than always the earliest.

Finally, visit `/app/00000000-0000-0000-0000-000000000000`. Expected: redirected to `/app`, not an error page.

- [ ] **Step 6: Commit**

```bash
git add app/app/lib/auth.server.ts app/app/lib/api.ts app/app/routes/dashboard.tsx
git commit -m "feat(app): API keys read and write the named knowledge base

A 404 from the scoped endpoint means the knowledge base is not the
caller's, so the loader redirects to /app; other failures still degrade to
an empty list."
```

---

### Task 3: Config reads and writes the named knowledge base

**Files:**
- Modify: `app/app/lib/auth.server.ts` — `getConfig`
- Modify: `app/app/lib/api.ts` — `updateConfig`
- Modify: `app/app/routes/config.tsx`

**Interfaces:**
- Consumes: `KbNotFound` (Task 1).
- Produces: `getConfig(request, kbId)`, `updateConfig(kbId, config)`.

- [ ] **Step 1: Scope the server reader**

In `app/app/lib/auth.server.ts`, replace `getConfig`:

```ts
export async function getConfig(request: Request, kbId: string): Promise<KbConfig> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases/${kbId}/config`, {
      headers: forwardCookie(request),
    });
    if (res.status === 404) throw new KbNotFound();
    if (!res.ok) return EMPTY_CONFIG;
    return await res.json();
  } catch (err) {
    if (err instanceof KbNotFound) throw err;
    return EMPTY_CONFIG;
  }
}
```

- [ ] **Step 2: Scope the browser mutation**

In `app/app/lib/api.ts`, replace `updateConfig`:

```ts
export const updateConfig = (kbId: string, config: KbConfig): Promise<KbConfig> =>
  apiFetch<KbConfig>(`/knowledge-bases/${kbId}/config`, {
    method: "PUT",
    body: JSON.stringify(config),
  });
```

- [ ] **Step 3: Thread the knowledge base through the config page**

In `app/app/routes/config.tsx`, change the loader:

```ts
export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/config`);
  try {
    const config = await getConfig(request, params.kbId);
    return { me, config, kbId: params.kbId };
  } catch (err) {
    if (err instanceof KbNotFound) throw redirect("/app");
    throw err;
  }
}
```

Import `KbNotFound`, and pass `kbId` from `loaderData` into the `updateConfig(...)` call in the component.

- [ ] **Step 4: Typecheck**

```bash
docker compose exec app pnpm typecheck
```

Expected: clean.

- [ ] **Step 5: Verify in a browser**

Open `/app/<HN Demo id>/config`. Expected: the interests field and the entity/relationship type editors load. Change the interests text, save, reload the page and confirm it persisted. Then open `/app/<a Default* knowledge base id>/config` and confirm its interests text is **different** — that proves config is per-knowledge-base. Change nothing there.

Visit `/app/00000000-0000-0000-0000-000000000000/config`. Expected: redirected to `/app`.

- [ ] **Step 6: Commit**

```bash
git add app/app/lib/auth.server.ts app/app/lib/api.ts app/app/routes/config.tsx
git commit -m "feat(app): config reads and writes the named knowledge base"
```

---

### Task 4: Ingestion targets the named knowledge base

**Files:**
- Modify: `app/app/lib/api.ts` — `ingestContent`, `getJob`
- Modify: `app/app/routes/ingest.tsx`

**Interfaces:**
- Consumes: nothing beyond Task 1's nav change, already applied.
- Produces: `ingestContent(kbId, text, source?)`, `getJob(kbId, jobId)`.

- [ ] **Step 1: Scope the browser calls**

In `app/app/lib/api.ts`, replace both content functions:

```ts
export const ingestContent = (kbId: string, text: string, source?: string): Promise<JobAccepted> =>
  apiFetch<JobAccepted>(`/knowledge-bases/${kbId}/content`, {
    method: "POST",
    body: JSON.stringify({ text, metadata: source ? { source } : {} }),
  });

export const getJob = (kbId: string, jobId: string): Promise<JobStatus> =>
  apiFetch<JobStatus>(`/knowledge-bases/${kbId}/content/${jobId}`);
```

- [ ] **Step 2: Thread the knowledge base through the ingest page**

In `app/app/routes/ingest.tsx`, change the loader to return `kbId`:

```ts
export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/ingest`);
  return { me, kbId: params.kbId };
}
```

This loader fetches nothing scoped, so it needs no `KbNotFound` handling — a bad `kbId` surfaces when the first `ingestContent` call 404s. Pass `kbId` from `loaderData` into every `ingestContent(...)` and `getJob(...)` call site; the file polls job status, so the polling closure needs it too.

- [ ] **Step 3: Typecheck**

```bash
docker compose exec app pnpm typecheck
```

Expected: clean.

- [ ] **Step 4: Verify in a browser**

Open `/app/<HN Demo id>/ingest`. Paste a short distinctive sentence — for example `Testing sub-project B ingestion into HN Demo.` — and submit. Expected: a job id appears and its status polls through to a terminal state (`done` or `skipped`; either proves the round trip, since relevance may reject it).

Then confirm it landed in the right knowledge base:

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose exec -T postgres psql -U ingestion -d ingestion -c \
  "SELECT kb.name, j.status, left(j.content, 40) FROM ingest_jobs j
   JOIN knowledge_bases kb ON kb.id = j.knowledge_base_id
   ORDER BY j.created_at DESC LIMIT 3;"
```

Expected: the newest row's knowledge base is `HN Demo`. Before this task it would have been `Default Organization`.

- [ ] **Step 5: Commit**

```bash
git add app/app/lib/api.ts app/app/routes/ingest.tsx
git commit -m "feat(app): ingestion targets the named knowledge base"
```

---

### Task 5: Explore and entity pages query the named knowledge base

The two components that fetch `/api/graphql` directly are easy to miss; both are listed here.

**Files:**
- Modify: `app/app/lib/auth.server.ts` — `getEntity`
- Modify: `app/app/components/graph-explorer.tsx`
- Modify: `app/app/components/graphiql-panel.tsx`
- Modify: `app/app/routes/explore.tsx`
- Modify: `app/app/routes/entity.tsx`

**Interfaces:**
- Consumes: `KbNotFound` (Task 1).
- Produces: `getEntity(request, kbId, id)`; `<GraphExplorer kbId={...} />`; `<GraphiQLPanel kbId={...} />`.

- [ ] **Step 1: Scope the server reader**

In `app/app/lib/auth.server.ts`, replace `getEntity`:

```ts
export async function getEntity(request: Request, kbId: string, id: string): Promise<EntityPage | null> {
  const query =
    "query($id: ID!) { node(id: $id) { id name type summary article " +
    "edges { type target { id name type } } related { id name type } references { label date } } }";
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases/${kbId}/graphql`, {
      method: "POST",
      headers: { ...forwardCookie(request), "content-type": "application/json" },
      body: JSON.stringify({ query, variables: { id } }),
    });
    if (res.status === 404) throw new KbNotFound();
    if (!res.ok) return null;
    const body = await res.json();
    return body?.data?.node ?? null;
  } catch (err) {
    if (err instanceof KbNotFound) throw err;
    return null;
  }
}
```

- [ ] **Step 2: Give the graph explorer its knowledge base**

In `app/app/components/graph-explorer.tsx`, change `fetchGraph` to take the knowledge base and use the scoped path:

```ts
async function fetchGraph(kbId: string, search: string): Promise<GraphData> {
  const res = await fetch(`/api/knowledge-bases/${kbId}/graphql`, {
```

Leave the rest of the function body unchanged. Change the component signature from `export function GraphExplorer() {` to `export function GraphExplorer({ kbId }: { kbId: string }) {`, and pass `kbId` through at every `fetchGraph(...)` call inside it.

- [ ] **Step 3: Give the GraphiQL panel its knowledge base**

In `app/app/components/graphiql-panel.tsx`, change the fetcher URL:

```ts
        url: `/api/knowledge-bases/${kbId}/graphql`,
```

Change the component signature to accept `{ kbId }: { kbId: string }`, and add `kbId` to the dependency array of the `useEffect` that builds the fetcher — otherwise switching knowledge bases leaves the panel querying the previous one.

- [ ] **Step 4: Thread the knowledge base through both pages**

In `app/app/routes/explore.tsx`:

```ts
export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/explore`);
  return { me, kbId: params.kbId };
}
```

and pass `kbId` to both `<GraphExplorer />` and `<GraphiQLPanel />`.

In `app/app/routes/entity.tsx`:

```ts
export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/entity/${params.id}`);
  try {
    const entity = await getEntity(request, params.kbId, params.id);
    if (!entity) throw new Response("Not found", { status: 404 });
    return { me, entity, kbId: params.kbId };
  } catch (err) {
    if (err instanceof KbNotFound) throw redirect("/app");
    throw err;
  }
}
```

Note the ordering: `throw new Response(...)` for a missing entity must stay distinguishable from `KbNotFound`. A missing entity in a knowledge base you *do* own is a genuine 404 page; a knowledge base you do not own is a redirect. The `instanceof` check keeps them apart — do not collapse them.

Also update any link that builds an entity URL to include the knowledge base, e.g. `` `/app/${kbId}/entity/${id}` ``. Check `graph-explorer.tsx`'s node-click handler and `entity.tsx`'s own related-entity links.

- [ ] **Step 5: Typecheck**

```bash
docker compose exec app pnpm typecheck
```

Expected: clean.

- [ ] **Step 6: Verify in a browser — this is the check the whole two-project effort exists for**

Open `/app/<HN Demo id>/explore`. Expected: **the graph renders roughly 360 nodes**, not the empty state. Before this work the page always showed 0.

Then: click a node and confirm it opens `/app/<HN Demo id>/entity/<id>` and the article renders with its relationships and references. Switch to the Query tab and run `{ nodes(limit: 5) { id name type } }` — expected: five real entities. Then open `/app/<a Default* knowledge base id>/explore` and confirm it shows the empty state, proving the two are genuinely isolated.

- [ ] **Step 7: Commit**

```bash
git add app/app/lib/auth.server.ts app/app/components/graph-explorer.tsx \
        app/app/components/graphiql-panel.tsx app/app/routes/explore.tsx app/app/routes/entity.tsx
git commit -m "feat(app): explore and entity pages query the named knowledge base

HN Demo's 360 entities are reachable from the UI for the first time."
```

---

### Task 6: Delete the legacy unscoped routes

Nothing calls them now. They were kept alive only so the UI would keep working while sub-project A shipped.

**Files:**
- Modify: `ingestion/routes_keys.py`, `ingestion/routes_settings.py`, `ingestion/routes_ingest.py`, `ingestion/graph_api.py`, `ingestion/accounts.py`, `ingestion/main.py`
- Modify: the corresponding test files

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This task only removes.

- [ ] **Step 1: Confirm nothing still calls them**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
grep -rn '"/api/keys\|/api/config\|/api/content\|/api/graphql' app/app neonews web 2>/dev/null | grep -v node_modules
```

Expected: **no output**. Any hit means an earlier task missed a call site — fix that before deleting anything. Note that `neonews` calls the Bearer-authenticated `/content` and `/graphql` routes, which have no `/api` prefix and are not being deleted; a hit for those paths without `/api` is fine and expected.

- [ ] **Step 2: Delete the legacy wrappers**

Remove from each module the wrapper that resolves its knowledge base via `home_knowledge_base_id`, keeping the scoped twin and the shared body function:

- `ingestion/routes_keys.py` — `list_keys`, `create_key`, `revoke_key` (the legacy three), and the now-unused `router`
- `ingestion/routes_settings.py` — `get_config`, `put_config` (legacy), and its `router`
- `ingestion/routes_ingest.py` — `ingest_content`, `job_status` (legacy), and its `router`
- `ingestion/graph_api.py` — `get_cookie_context` and `cookie_graphql_router`

Rename each remaining `scoped_router` to `router` so the modules read naturally now that there is only one, and drop the `_scoped` suffix from the handler names for the same reason.

- [ ] **Step 3: Delete `home_knowledge_base_id`**

Remove the function from `ingestion/accounts.py` and every now-dead import of it across the four modules above.

- [ ] **Step 4: Update the router registrations**

In `ingestion/main.py`, remove the `include_router` calls for the deleted legacy routers and the `cookie_graphql_router` mount. Keep the scoped registrations and the Bearer-authenticated `/graphql`, `/content` and `/config` routers, which are a separate surface used by neonews and any API-key client.

- [ ] **Step 5: Delete the legacy tests**

Remove tests that exercise the deleted paths — those calling `/api/keys`, `/api/config`, `/api/content` or `/api/graphql` directly, plus any that assert legacy-and-scoped equivalence, which is meaningless once only one exists. Keep every test of the scoped paths and of `require_membership`. Also remove `test_memberships.py`'s use of `home_knowledge_base_id` if any exists.

List the tests you delete in your report, with a one-line reason each. A deleted test is a lost assertion — say what each one covered and whether a scoped test still covers it.

- [ ] **Step 6: Run the engine suite, lint and types**

```bash
cd ingestion
uv run pytest && uv run ruff check . && uv run mypy .
```

Expected: all green. This is the check proving nothing outside `app/` depended on the deleted routes.

- [ ] **Step 7: Verify the app still works**

```bash
cd /home/steve/Source/sinpi/anything_handwritten
docker compose up -d --build ingestion-api
sleep 20
```

Then in a browser, walk the whole app once more: `/app` lists the knowledge bases; `/app/<HN Demo id>` shows keys; `/config` loads; `/ingest` accepts a submission; `/explore` still renders ~360 nodes. Any page that breaks here means a scoped route was deleted by mistake.

Finally confirm the legacy paths are actually gone:

```bash
C=$(curl -s -i -X POST http://localhost:5173/api/auth/login -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:5173' \
  -d '{"email":"admin@sinpi.software","password":"adminpassword"}' \
  | grep -i '^set-cookie' | sed 's/.*session=\([^;]*\).*/\1/')
for p in /api/keys /api/config; do
  printf '%-16s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:5173$p" -H "Cookie: session=$C")"
done
```

Expected: `404` for both — the routes no longer exist.

- [ ] **Step 8: Commit**

```bash
git add ingestion/
git commit -m "refactor(api): delete the legacy unscoped routes

They existed only to keep the desk UI working while sub-project A shipped.
The UI now names its knowledge base in every call, so resolving one from
'the caller's earliest membership' has no remaining caller — and leaving it
would keep a second, wrong way to reach data."
```

---

## Notes for the implementer

- **Typecheck runs in the container**, never on the host: `docker compose exec app pnpm typecheck`. The dev server runs as root and its generated `.react-router` types are root-owned.
- **Do not delete or modify `HN Demo` or either `Default*` knowledge base.** `HN Demo` holds the operator's 360 real entities and is the subject of the most important verification in this plan. Create your own throwaway knowledge base for any destructive check.
- **Vite hot-reloads route files but not `routes.ts`.** After Task 1's restructure, restart with `docker compose restart app` if routes 404 unexpectedly.
- If a page renders but its data is empty, check whether you passed `kbId` into the client call — an empty result and a wrong knowledge base look identical in the UI. That ambiguity is exactly why each task's browser check compares two different knowledge bases rather than just confirming one loads.

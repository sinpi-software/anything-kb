# Wiki Page View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-entity wiki page at `/app/entity/:id` — the markdown `article`, relationships grouped by type as links to other entity pages, and a references list — plus making Explore graph nodes clickable to reach it.

**Architecture:** A React Router 8 SSR route whose loader queries the cookie GraphQL (`/api/graphql`, session-scoped) for the entity, and whose component renders it. `react-markdown` (SSR-safe) renders the article. The graph explorer gains `onNodeClick` navigation.

**Tech Stack:** React Router 8 (SSR), Tailwind v4, `react-markdown` + `remark-gfm`.

**Design doc:** `docs/superpowers/specs/2026-07-24-wiki-page-view-design.md`.

## Global Constraints

- **Tenancy:** read only through the cookie GraphQL (`/api/graphql`); it resolves the knowledge base from the session. A foreign/unknown id returns `node: null` → the page 404s. Never send a knowledge-base id from the client.
- **Markdown safety:** `react-markdown` in DEFAULT mode — no `rehype-raw`, no `dangerouslySetInnerHTML`. The article is LLM-generated (untrusted); render parsed markdown only.
- **Auth gate:** same SSR redirect-to-login as other `/app/*` routes (`getMe(request)`).
- **Consistency:** reuse `SiteHeader` + `APP_NAV_LINKS`, theme tokens, mobile-first patterns from `config.tsx`/`ingest.tsx`.
- **Verify (no frontend unit tests):** each task ends green on, from `app/`: `export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH"` then `corepack pnpm run typecheck` and (where UI changed) `corepack pnpm run build`.

---

### Task 1: Data layer — deps, `EntityPage` type, `getEntity`

**Files:** `app/package.json`/`pnpm-lock.yaml` (deps), `app/app/lib/types.ts`, `app/app/lib/auth.server.ts`.

**Interfaces — Produces:** `EntityPage` type; `getEntity(request, id): Promise<EntityPage | null>`.

- [ ] **Step 1: Add deps.** From `app/` (Node 22.23.1): `corepack pnpm add react-markdown remark-gfm`.

- [ ] **Step 2: `EntityPage` type** in `types.ts`:
```ts
export interface EntityPage {
  id: string;
  name: string;
  type: string;
  summary: string | null;
  article: string | null;
  edges: { type: string; target: { id: string; name: string; type: string } }[];
  references: { label: string; date: string }[];
}
```

- [ ] **Step 3: `getEntity`** in `auth.server.ts` (mirror `getConfig`'s cookie-forward, but POST GraphQL; return `null` on absence/error):
```ts
export async function getEntity(request: Request, id: string): Promise<import("./types").EntityPage | null> {
  const query =
    "query($id: ID!) { node(id: $id) { id name type summary article " +
    "edges { type target { id name type } } references { label date } } }";
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/graphql`, {
      method: "POST",
      headers: { ...forwardCookie(request), "content-type": "application/json" },
      body: JSON.stringify({ query, variables: { id } }),
    });
    if (!res.ok) return null;
    const body = await res.json();
    return body?.data?.node ?? null;
  } catch {
    return null;
  }
}
```
(Add `EntityPage` to the `import type { ... } from "./types"` line if you prefer a named import over the inline one.)

- [ ] **Step 4: Verify** `corepack pnpm run typecheck` → green.

- [ ] **Step 5: Commit** — `git commit -am "feat(web): entity data layer (react-markdown dep, EntityPage, getEntity)"`.

---

### Task 2: The entity page route + markdown styling

**Files:** `app/app/routes.ts`, `app/app/routes/entity.tsx` (create), `app/app/app.css`.

**Interfaces — Consumes:** Task 1.

- [ ] **Step 1: Register the route** in `routes.ts` (after the other `app/*` routes):
```ts
  route("app/entity/:id", "routes/entity.tsx"),
```

- [ ] **Step 2: Create `routes/entity.tsx`:**
```tsx
import { LogOut } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Link, redirect, useNavigate } from "react-router";
import remarkGfm from "remark-gfm";

import { SiteHeader } from "~/components/site-header";
import { Button } from "~/components/ui/button";
import { logout } from "~/lib/api";
import { getEntity, getMe } from "~/lib/auth.server";
import { APP_NAV_LINKS } from "~/lib/nav";
import type { Route } from "./+types/entity";

export function meta({ data }: Route.MetaArgs) {
  return [{ title: data?.entity ? `${data.entity.name} — anything/kb` : "Entity — anything/kb" }];
}

export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/entity/${params.id}`);
  const entity = await getEntity(request, params.id);
  if (!entity) throw new Response("Not found", { status: 404 });
  return { me, entity };
}

function formatDate(value: string): string {
  if (!value) return "";
  const d = new Date(value);
  return isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(d);
}

export default function Entity({ loaderData }: Route.ComponentProps) {
  const { entity } = loaderData;
  const navigate = useNavigate();

  const groups = new Map<string, typeof entity.edges>();
  for (const e of entity.edges) {
    const arr = groups.get(e.type) ?? [];
    arr.push(e);
    groups.set(e.type, arr);
  }
  const body = entity.article || entity.summary || "";

  async function handleLogout() {
    await logout();
    await navigate("/login");
  }

  return (
    <div className="min-h-svh">
      <SiteHeader
        navLinks={APP_NAV_LINKS}
        actions={
          <Button variant="outline" onClick={handleLogout} className="text-sm">
            <LogOut className="size-3.5" aria-hidden="true" />
            Log out
          </Button>
        }
      />
      <main className="mx-auto max-w-3xl px-5 py-8 sm:px-7 sm:py-12">
        <span className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">
          {entity.type}
        </span>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">{entity.name}</h1>

        {body ? (
          <div className="article-prose mt-6">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
          </div>
        ) : (
          <p className="mt-6 text-muted">No article yet — ingest more about this entity.</p>
        )}

        {entity.edges.length > 0 ? (
          <section className="mt-10">
            <h2 className="font-display text-sm font-semibold tracking-wide text-muted uppercase">Relationships</h2>
            <div className="mt-3 flex flex-col gap-3">
              {[...groups.entries()].map(([type, edges]) => (
                <div key={type} className="flex flex-col gap-1 sm:flex-row sm:gap-3">
                  <span className="font-display text-sm text-muted sm:w-40 sm:flex-none">{type}</span>
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    {edges.map((e) => (
                      <Link key={e.target.id} to={`/app/entity/${e.target.id}`} className="text-accent hover:underline">
                        {e.target.name} <span className="text-muted">· {e.target.type}</span>
                      </Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {entity.references.length > 0 ? (
          <section className="mt-10">
            <h2 className="font-display text-sm font-semibold tracking-wide text-muted uppercase">References</h2>
            <ul className="mt-3 flex flex-col gap-1.5 text-sm text-muted">
              {entity.references.map((r, i) => (
                <li key={i}>
                  {r.label || "source"}
                  {r.date ? ` · ${formatDate(r.date)}` : ""}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Markdown prose styles** — append to `app/app/app.css` (theme-token-driven; readable measure):
```css
.article-prose {
  color: var(--ink);
  max-width: 65ch;
  line-height: 1.7;
}
.article-prose h1,
.article-prose h2,
.article-prose h3 {
  font-family: var(--font-display);
  font-weight: 600;
  margin: 1.6em 0 0.5em;
}
.article-prose h2 { font-size: 1.15rem; }
.article-prose h3 { font-size: 1rem; }
.article-prose p { margin: 0.8em 0; }
.article-prose ul,
.article-prose ol { margin: 0.8em 0; padding-left: 1.4em; }
.article-prose ul { list-style: disc; }
.article-prose ol { list-style: decimal; }
.article-prose li { margin: 0.3em 0; }
.article-prose a { color: var(--accent); text-decoration: underline; }
.article-prose code {
  font-family: var(--font-display);
  font-size: 0.9em;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}
.article-prose blockquote {
  border-left: 3px solid var(--line-strong);
  padding-left: 1em;
  color: var(--muted);
  margin: 0.8em 0;
}
```

- [ ] **Step 4: Verify** `corepack pnpm run typecheck` and `corepack pnpm run build` → both green.

- [ ] **Step 5: Commit** — `git commit -am "feat(web): entity wiki page (article + relationships + references)"`.

---

### Task 3: Clickable graph nodes → entity page

**Files:** `app/app/components/graph-explorer.tsx`.

- [ ] **Step 1: Add navigation.** Import `useNavigate` from `react-router` (add to the existing react-router import if present, else a new import); inside the component, `const navigate = useNavigate();`. Add to the `<ForceGraph … />` element:
```tsx
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            onNodeClick={(node: any) => navigate(`/app/entity/${node.id}`)}
```
(Place it alongside the other ForceGraph props. The node object carries `id`.)

- [ ] **Step 2: Verify** `corepack pnpm run typecheck` and `corepack pnpm run build` → green.

- [ ] **Step 3: Commit** — `git commit -am "feat(web): click a graph node to open its entity page"`.

---

## Final verification (after all tasks)

- [ ] From `app/`: `corepack pnpm run typecheck` && `corepack pnpm run build` → green.
- [ ] Backend unaffected — no backend changes in C; still confirm `cd ingestion && uv run pytest -q` is green (regression guard).
- [ ] Deploy via the pipeline (push to `main`). No migration, no backend change.
- [ ] **Live/browser smoke** (real session, read-only): open Explore, click a node → lands on `/app/entity/<id>`; the page shows the markdown article (headings render), the Relationships section links to other entities (click one → navigates), and References shows labels + dates. Confirm `/app/entity/<bogus-uuid>` 404s. (Read-only — no writes to real data; no throwaway account needed.)

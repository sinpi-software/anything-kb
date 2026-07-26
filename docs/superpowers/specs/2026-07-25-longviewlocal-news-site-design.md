# longviewlocal.news — neonews SSR site

**Goal:** Publish neonews's drafted issues as a public news site at `longviewlocal.news`.

The newsroom pipeline (`neonews/`) already drafts editorial issues as markdown into the
`neonews_issues` table (`Issue.body`). Nothing renders them for readers yet. This design adds
a small React Router 8 SSR app that reads those issues and serves them, published through the
cluster's existing Cloudflare tunnel.

## Prerequisite (not part of this spec)

`draft-issue` must be running in the cluster so `neonews_issues` actually has rows to serve.
That is the pending plan `docs/superpowers/plans/2026-07-26-prefect-and-neonews-in-k3s.md`
(Tasks 1–5). Task 1 — the `Issue.body` column — already landed. This spec assumes that plan
has been executed and the cluster's Postgres holds drafted issues. The site can be built and
deployed before then; it will simply show an empty list until issues exist.

## Architecture

One new service: a React Router 8 SSR app at repo-root **`neonews-site/`**, mirroring the
existing `app/` frontend's stack. Its server loaders query `neonews_issues` in Postgres
**directly** (via `pg`), using `NEONEWS_POSTGRES_URL` — the same database and secret neonews
already uses. There is no intermediate HTTP API: neonews is a small system and the site is a
read-only consumer of its own database.

```
neonews_issues (Postgres, neonews_* tables)
        ↑  SELECT (server-side loaders, pg Pool)
neonews-site (React Router 8 SSR, Node)
        ↑  http://neonews-site.ingestion.svc.cluster.local:80
cloudflared (existing tunnel)  ──public hostname──▶  longviewlocal.news
```

Publishing reuses the cluster's existing `cloudflared` Deployment. cloudflared runs inside the
cluster, so a tunnel public-hostname rule can point straight at the site's ClusterIP Service —
no Traefik Ingress and no second tunnel.

### Why direct Postgres (not an API), why a separate app

- `app/` never touches Postgres — it calls the engine's HTTP API — but issues are neonews's
  own data in `neonews_*` tables, which no HTTP API serves. Rather than grow neonews a web
  framework and an issues API plus a frontend (two new services), the site reads the database
  it already owns (one new service). Read-only; the `.server`-suffixed module keeps `pg` and
  the connection string out of the client bundle.
- A separate app (not a route inside `app/`) because `longviewlocal.news` is a distinct public
  brand from the `desk.sinpi.software` engine/wiki app, and keeps the newsroom's dependencies
  and deploy lifecycle independent.

## Stack

Match `app/` exactly (read those files; do not invent versions):

- React Router 8 (`react-router.config.ts` → `ssr: true`), React 19, Vite 8.
- Tailwind v4 via `@tailwindcss/vite`; a single `app/app.css` `@import "tailwindcss"` entry
  with an `@theme` block (a newsroom palette — light is the base, dark via the same toggle
  mechanism `app/` uses).
- shadcn-style primitives on **Base UI** (`@base-ui-components/react`), with
  `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`. The `cn()` helper is
  copied verbatim from `app/app/lib/utils.ts`.
- `react-markdown` + `remark-gfm` for rendering issue bodies.
- pnpm; Node 24 alpine multi-stage Dockerfile mirroring `app/Dockerfile`
  (`build → prune --prod → runtime`, `react-router-serve ./build/server/index.js`, `PORT`).

## Components

### `app/lib/db.server.ts` — data access (server-only)

A module-level `pg` `Pool` built from `NEONEWS_POSTGRES_URL`. The `.server` suffix guarantees
React Router never bundles it (or the connection string) into client code.

- `listIssues(): Promise<IssueSummary[]>` — `SELECT id, generated_at, covers_since,
  story_count, body FROM neonews_issues ORDER BY generated_at DESC`. Maps each row to a
  summary using the pure helpers below (headline from `body`, slug from `generated_at`); the
  full `body` is not sent to the index client — only the derived headline.
- `getIssue(slug): Promise<Issue | null>` — resolve the slug to a row and return it with
  `body`. Matched by comparing `slugOf(generated_at)` (see below); returns `null` for an
  unknown slug so the route can 404.

Rows where `body IS NULL` (pre-`Issue.body` legacy rows, if any) are skipped in the list and
404 on direct access — there is nothing to render.

### `app/lib/issues.ts` — pure helpers (unit-tested)

No DB, no I/O — the only bug-prone logic, so these carry tests.

- `slugOf(generatedAt: Date): string` → `YYYY-MM-DD-HHMM` (UTC), matching neonews's own issue
  file-stem convention (`neonews/issues/2026-07-26-0313.md`). Two issues in the same minute is
  not expected for a low-volume newsroom; if it ever happens the later one is unreachable by
  slug — acceptable, and noted here rather than silently handled.
- `headlineOf(body: string): string` → the first `## ` heading's text (the lead story's
  headline), or a dated fallback (`"Issue — YYYY-MM-DD"`) if the body has none.

### Routes (`app/routes.ts`)

- `index("routes/home.tsx")` → `/` — the issue list. Loader calls `listIssues()`. Renders a
  masthead + a list of issues (headline, date, coverage window, story count), each linking to
  its page. Empty state when there are no issues yet.
- `route(":slug", "routes/issue.tsx")` → `/2026-07-26-0313` — one issue. Loader calls
  `getIssue(params.slug)`; `throw new Response(null, { status: 404 })` when `null`. Renders the
  body markdown via `react-markdown` + `remark-gfm` inside a readable article column.
- `route("healthz", "routes/healthz.tsx")` → `/healthz` — a resource route whose loader returns
  `new Response("ok")` for the k8s readiness probe.

### Look

Editorial/newspaper feel: serif display headlines, generous measure on the article column,
clear dateline/coverage metadata, light/dark aware via the same theme mechanism as `app/`.
Built from the same Card/Button primitives and an adapted `site-header` masthead and
`theme-toggle`. No client JS beyond what RR8 and the theme toggle require; no auth. Specific
brand/masthead wording, palette, and typography are left to implementation.

## Deployment

### `deploy/deploy.sh`

After the existing web-image build, build + push the site image and register its config
(mirroring the two existing image builds):

```bash
echo ">> build + push localhost:5000/anything-neonews-site:$TAG"
docker build -q -t "localhost:5000/anything-neonews-site:$TAG" "$HERE/../neonews-site" >/dev/null
docker push "localhost:5000/anything-neonews-site:$TAG" >/dev/null
```
```bash
pulumi config set neonewsSiteImage "localhost:5000/anything-neonews-site:$TAG"
```

### `deploy/__main__.py`

After the neonews resources (from the prerequisite plan), following the `web` Deployment/Service
as the template:

- `neonews_site_image = cfg.require("neonewsSiteImage")`.
- `neonews-site` **Deployment**: that image, `imagePullPolicy: Always`, container port 3000,
  `env: [{PORT: "3000"}]`, `envFrom` the existing `neonews-secret` (it already carries
  `NEONEWS_POSTGRES_URL`), readiness probe `httpGet /healthz` on 3000, modest resources like
  `web`. `depends_on` the neonews migration Job (the `neonews_issues` table must exist).
- `neonews-site` **Service**: ClusterIP, `port 80 → targetPort 3000`.

Prefect stays LAN-only and is untouched. The site exposes only public, read-only content.

### Cloudflare (manual, documented in `deploy/README.md`)

`longviewlocal.news`'s zone already exists in the Cloudflare account. On the **existing** tunnel
(the same one serving `desk.sinpi.software`), add a public hostname:

- `longviewlocal.news` → `http://neonews-site.ingestion.svc.cluster.local:80`

This is the same dashboard-side configuration point the engine's hostname uses. No new tunnel,
token, or cluster resource is required.

## Testing & verification

- **vitest** on `app/lib/issues.ts` (`slugOf`, `headlineOf`) — this adds a JS test harness the
  `app/` project does not have, justified by the slug/headline logic being where bugs hide. A
  `test` script (`vitest run`) is added to `package.json`.
- **Typecheck + build** as the primary gates, matching `app/`: `react-router typegen && tsc`
  and `pnpm build`.
- **Live verification against the cluster** (a skipped live check is not a passing check):
  after deploy, `https://longviewlocal.news/` lists issues, an issue page renders its markdown,
  an unknown slug 404s, and `/healthz` returns 200. If there are no issues yet (prerequisite
  plan not run), the index shows its empty state rather than erroring.

## File structure

| File | Responsibility |
| --- | --- |
| `neonews-site/package.json` | Deps mirrored from `app/`, plus `pg` and `vitest`; `test` script |
| `neonews-site/react-router.config.ts` | `ssr: true` |
| `neonews-site/vite.config.ts` | Tailwind v4 + RR8 Vite plugins (from `app/`) |
| `neonews-site/Dockerfile`, `.dockerignore` | Node 24 multi-stage, mirrors `app/` |
| `neonews-site/app/app.css` | Tailwind entry + newsroom `@theme` |
| `neonews-site/app/root.tsx` | Document shell + theme (from `app/`) |
| `neonews-site/app/routes.ts` | index, `:slug`, `healthz` |
| `neonews-site/app/routes/home.tsx` | Issue list |
| `neonews-site/app/routes/issue.tsx` | One issue (markdown render) |
| `neonews-site/app/routes/healthz.tsx` | Readiness resource route |
| `neonews-site/app/lib/db.server.ts` | `pg` Pool; `listIssues`, `getIssue` |
| `neonews-site/app/lib/issues.ts` | `slugOf`, `headlineOf` (pure) |
| `neonews-site/app/lib/issues.test.ts` | vitest for the pure helpers |
| `neonews-site/app/lib/utils.ts` | `cn()` (from `app/`) |
| `neonews-site/app/components/…` | masthead, theme-toggle, Card/Button primitives |
| `deploy/deploy.sh` | Build + push the site image, set `neonewsSiteImage` |
| `deploy/__main__.py` | `neonews-site` Deployment + Service |
| `deploy/README.md` | Cloudflare public-hostname step; `neonewsSiteImage` config |

## Out of scope

- Editing/curation UI, comments, search, RSS output, pagination (add when issue volume needs it).
- Any change to the newsroom pipeline or the engine.
- CI wiring for the new project (follow up separately if desired; the repo's existing CI covers
  `ingestion/` and `neonews/`).

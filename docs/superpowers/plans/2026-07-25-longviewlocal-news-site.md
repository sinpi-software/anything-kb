# longviewlocal.news — neonews SSR site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish neonews's drafted issues as a public React Router 8 SSR site at `longviewlocal.news`.

**Architecture:** A new SSR app at repo-root `neonews-site/`, mirroring the existing `app/` frontend's stack. Its server loaders read the `neonews_issues` table from Postgres directly (via `pg`) using `NEONEWS_POSTGRES_URL` — no intermediate HTTP API. Deployed as one k8s Deployment/Service and published on the cluster's existing cloudflared tunnel via a `longviewlocal.news` public-hostname rule.

**Tech Stack:** React Router 8 (SSR), React 19, Vite 8, Tailwind v4 (`@tailwindcss/vite`), shadcn-on-Base-UI, `react-markdown` + `remark-gfm`, `pg`, vitest, pnpm, Node 24.

**Spec:** `docs/superpowers/specs/2026-07-25-longviewlocal-news-site-design.md`

## Global Constraints

- **Mirror `app/`.** Read the corresponding `app/` file before writing each config/component; copy exact dependency versions for anything shared (React Router 8, React 19, Vite 8, Tailwind v4, Base UI, cva, clsx, tailwind-merge, lucide-react). Do not invent versions for shared deps.
- **Node 24, pnpm** (`packageManager: pnpm@10.33.0`), `"type": "module"`, `ssr: true`.
- **Server-only DB access:** the `pg` module and the connection string live only in a `.server.ts` file so React Router never bundles them into client code. Read-only queries; the site never writes.
- **Same database as neonews:** `NEONEWS_POSTGRES_URL`, table `neonews_issues` (columns: `id uuid`, `created_at`, `generated_at`, `covers_since`, `path`, `story_count int`, `body text NULL`). Rows with `body IS NULL` are skipped in the list and 404 on direct access.
- **Slug convention:** `YYYY-MM-DD-HHMM` (UTC) derived from `generated_at`, matching neonews's issue file-stem convention (`neonews/issues/2026-07-26-0313.md`).
- **Prerequisite (outside this plan):** `docs/superpowers/plans/2026-07-26-prefect-and-neonews-in-k3s.md` (Tasks 1–5) deploys neonews so `draft-issue` writes issues in-cluster. The site builds and deploys without it, showing an empty state until issues exist.
- **`KUBECONFIG=deploy/kubeconfig`** for all `kubectl`; the Pulumi passphrase is read from `deploy/.passphrase` — never print it or any secret.
- All commands below run from repo root `/home/steve/Source/sinpi/anything_handwritten` unless a `cd` is shown.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `neonews-site/package.json` | Deps (mirrored + `pg`, `vitest`), scripts | 1 |
| `neonews-site/react-router.config.ts` | `ssr: true` | 1 |
| `neonews-site/vite.config.ts` | Tailwind v4 + RR8 plugins | 1 |
| `neonews-site/tsconfig.json` | TS config (from `app/`) | 1 |
| `neonews-site/.gitignore`, `.dockerignore`, `.env.sample` | Ignores + env doc | 1 |
| `neonews-site/Dockerfile` | Node 24 multi-stage (from `app/`) | 1 |
| `neonews-site/app/app.css` | Tailwind entry + newsroom `@theme` + `.article-prose` | 1 |
| `neonews-site/app/root.tsx` | Document shell + ErrorBoundary | 1 |
| `neonews-site/app/routes.ts` | Route table | 1, 3, 4 |
| `neonews-site/app/routes/healthz.tsx` | Readiness resource route | 1 |
| `neonews-site/app/lib/utils.ts` | `cn()` (from `app/`) | 1 |
| `neonews-site/app/lib/issues.ts` | `slugOf`, `headlineOf` (pure) | 2 |
| `neonews-site/app/lib/issues.test.ts` | vitest for the pure helpers | 2 |
| `neonews-site/app/lib/db.server.ts` | `pg` Pool; `listIssues`, `getIssue` | 3 |
| `neonews-site/app/components/theme-toggle.tsx` | Theme toggle (from `app/`) | 3 |
| `neonews-site/app/components/site-header.tsx` | Masthead | 3 |
| `neonews-site/app/routes/home.tsx` | Issue list | 3 |
| `neonews-site/app/routes/issue.tsx` | One issue (markdown render) | 4 |
| `deploy/deploy.sh` | Build + push the site image, set `neonewsSiteImage` | 5 |
| `deploy/__main__.py` | `neonews-site` Deployment + Service | 5 |
| `deploy/README.md` | Cloudflare hostname step; `neonewsSiteImage` config | 5 |

---

### Task 1: Scaffold the SSR project (build green + `/healthz`)

**Files:**
- Create: `neonews-site/package.json`, `react-router.config.ts`, `vite.config.ts`, `tsconfig.json`, `.gitignore`, `.dockerignore`, `.env.sample`, `Dockerfile`
- Create: `neonews-site/app/app.css`, `app/root.tsx`, `app/routes.ts`, `app/routes/healthz.tsx`, `app/lib/utils.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: a buildable RR8 SSR app with a `/healthz` route returning `ok`. Later tasks add routes to `app/routes.ts` and modules under `app/lib` and `app/components`.

- [ ] **Step 1: Create `neonews-site/package.json`**

Shared versions are copied from `app/package.json`; `pg`, `@types/pg`, and `vitest` are new. Drop `app/`'s deps the site does not use (graphiql, graphql, react-force-graph, @graphiql/toolkit).

```json
{
  "name": "neonews-site",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "react-router build",
    "dev": "react-router dev",
    "start": "react-router-serve ./build/server/index.js",
    "typecheck": "react-router typegen && tsc",
    "test": "vitest run"
  },
  "dependencies": {
    "@base-ui-components/react": "1.0.0-rc.0",
    "@react-router/node": "^8",
    "@react-router/serve": "^8",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "isbot": "^5.1.36",
    "lucide-react": "^1.26.0",
    "pg": "^8.13.1",
    "react": "^19.2.7",
    "react-dom": "^19.2.7",
    "react-markdown": "^10.1.0",
    "react-router": "^8",
    "remark-gfm": "^4.0.1",
    "tailwind-merge": "^3.6.0"
  },
  "devDependencies": {
    "@react-router/dev": "^8",
    "@tailwindcss/vite": "^4.2.2",
    "@types/node": "^22",
    "@types/pg": "^8.11.10",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "tailwindcss": "^4.2.2",
    "typescript": "^5.9.3",
    "vite": "^8.0.3",
    "vitest": "^3.2.0"
  },
  "packageManager": "pnpm@10.33.0"
}
```

- [ ] **Step 2: Create the build config files**

`neonews-site/react-router.config.ts`:
```ts
import type { Config } from "@react-router/dev/config";

export default {
  ssr: true,
} satisfies Config;
```

`neonews-site/vite.config.ts`:
```ts
import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: {
    tsconfigPaths: true,
  },
});
```

`neonews-site/tsconfig.json` (copied verbatim from `app/tsconfig.json`):
```json
{
  "include": [
    "**/*",
    "**/.server/**/*",
    "**/.client/**/*",
    ".react-router/types/**/*"
  ],
  "compilerOptions": {
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "types": ["node", "vite/client"],
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "rootDirs": [".", "./.react-router/types"],
    "paths": {
      "~/*": ["./app/*"]
    },
    "esModuleInterop": true,
    "verbatimModuleSyntax": true,
    "noEmit": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "strict": true
  }
}
```

- [ ] **Step 3: Create the ignore + env + Docker files**

`neonews-site/.gitignore`:
```
.DS_Store
.env
/node_modules/

# React Router
/.react-router/
/build/
```

`neonews-site/.dockerignore`:
```
.react-router
build
node_modules
README.md
```

`neonews-site/.env.sample`:
```
# Server-only: Postgres URL the RR8 loaders read issues from. Same database
# neonews uses; the site only reads the neonews_issues table.
NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/ingestion
```

`neonews-site/Dockerfile` (mirrors `app/Dockerfile`):
```dockerfile
# React Router 8 SSR app (Node server). Build target: linux/amd64.
FROM node:24-alpine AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build
# Drop dev-only deps in place so the runtime image stays lean (build already ran).
RUN pnpm prune --prod

FROM node:24-alpine
RUN corepack enable
WORKDIR /app
ENV NODE_ENV=production PORT=3000
COPY --from=build /app/package.json /app/pnpm-lock.yaml ./
COPY --from=build /app/node_modules /app/node_modules
COPY --from=build /app/build /app/build
EXPOSE 3000
CMD ["pnpm", "run", "start"]
```

- [ ] **Step 4: Create `neonews-site/app/lib/utils.ts`** (verbatim from `app/`)

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 5: Create `neonews-site/app/app.css`**

A trimmed newsroom theme reusing `app/`'s CSS-variable + `data-theme` mechanism and its `.article-prose` block (needed to render issue markdown). Serif display for a newspaper feel.

```css
@import "tailwindcss";

@theme {
  --font-display: Georgia, "Times New Roman", "Iowan Old Style", serif;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
    Roboto, Helvetica, Arial, sans-serif;

  --color-ground: var(--ground);
  --color-surface: var(--surface);
  --color-ink: var(--ink);
  --color-muted: var(--muted);
  --color-line: var(--line);
  --color-line-strong: var(--line-strong);
  --color-accent: var(--accent);
}

:root {
  --ground: #f4f2ee;
  --surface: #ffffff;
  --ink: #1a1712;
  --muted: #5f584c;
  --line: rgba(26, 23, 18, 0.12);
  --line-strong: rgba(26, 23, 18, 0.22);
  --accent: #8a2f24; /* editorial red for links/masthead accents */
  --maxw: 1040px;
  color-scheme: light;
}

@media (prefers-color-scheme: dark) {
  :root {
    --ground: #14120f;
    --surface: #1b1814;
    --ink: #ece7df;
    --muted: #a29a8b;
    --line: rgba(236, 231, 223, 0.12);
    --line-strong: rgba(236, 231, 223, 0.22);
    --accent: #e0917f;
    color-scheme: dark;
  }
}

:root[data-theme="dark"] {
  --ground: #14120f;
  --surface: #1b1814;
  --ink: #ece7df;
  --muted: #a29a8b;
  --line: rgba(236, 231, 223, 0.12);
  --line-strong: rgba(236, 231, 223, 0.22);
  --accent: #e0917f;
  color-scheme: dark;
}

:root[data-theme="light"] {
  --ground: #f4f2ee;
  --surface: #ffffff;
  --ink: #1a1712;
  --muted: #5f584c;
  --line: rgba(26, 23, 18, 0.12);
  --line-strong: rgba(26, 23, 18, 0.22);
  --accent: #8a2f24;
  color-scheme: light;
}

html,
body {
  background: var(--ground);
  color: var(--ink);
}

body {
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

h1,
h2,
h3 {
  font-family: var(--font-display);
  text-wrap: balance;
}

.article-prose {
  color: var(--ink);
  max-width: 65ch;
  line-height: 1.75;
  font-size: 1.05rem;
}
.article-prose h1,
.article-prose h2,
.article-prose h3 {
  font-family: var(--font-display);
  font-weight: 600;
  margin: 1.6em 0 0.5em;
}
.article-prose h2 {
  font-size: 1.4rem;
}
.article-prose h3 {
  font-size: 1.15rem;
}
.article-prose p {
  margin: 0.9em 0;
}
.article-prose ul,
.article-prose ol {
  margin: 0.8em 0;
  padding-left: 1.4em;
}
.article-prose ul {
  list-style: disc;
}
.article-prose ol {
  list-style: decimal;
}
.article-prose li {
  margin: 0.3em 0;
}
.article-prose a {
  color: var(--accent);
  text-decoration: underline;
}
.article-prose strong {
  font-weight: 700;
}
```

- [ ] **Step 6: Create `neonews-site/app/root.tsx`** (from `app/root.tsx`, unchanged logic)

```tsx
import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";

import type { Route } from "./+types/root";
import "./app.css";

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  let message = "Oops!";
  let details = "An unexpected error occurred.";
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? "The requested page could not be found."
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-semibold">{message}</h1>
      <p className="mt-2 text-muted">{details}</p>
      {stack && (
        <pre className="mt-4 w-full overflow-x-auto p-4">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
```

- [ ] **Step 7: Create the route table and healthz route**

`neonews-site/app/routes.ts` (home + issue routes are added in Tasks 3–4; healthz now):
```ts
import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route(":slug", "routes/issue.tsx"),
  route("healthz", "routes/healthz.tsx"),
] satisfies RouteConfig;
```

`neonews-site/app/routes/healthz.tsx` (resource route — a bare `loader`, no default export):
```tsx
export function loader() {
  return new Response("ok", { headers: { "content-type": "text/plain" } });
}
```

Create minimal placeholder route modules so the build resolves (Tasks 3–4 replace them):

`neonews-site/app/routes/home.tsx`:
```tsx
export default function Home() {
  return <main>longviewlocal.news</main>;
}
```

`neonews-site/app/routes/issue.tsx`:
```tsx
export default function Issue() {
  return <main>issue</main>;
}
```

- [ ] **Step 8: Install, build, typecheck, run**

```bash
cd neonews-site && pnpm install && pnpm run typecheck && pnpm run build
```
Expected: install writes `pnpm-lock.yaml`; typecheck and build both succeed. If Vite 8 and vitest resolve an incompatible pair, let pnpm pick vitest's suggested version and re-run — vitest is only used by Task 2's pure-function tests.

Verify the server serves and `/healthz` answers:
```bash
cd neonews-site && (pnpm run start &) && sleep 3 && \
  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/healthz && \
  curl -s http://localhost:3000/healthz; kill %1 2>/dev/null
```
Expected: `200` then `ok`.

- [ ] **Step 9: Commit**

```bash
git add neonews-site
git commit -m "feat(neonews-site): scaffold RR8 SSR app with healthz route"
```

---

### Task 2: Pure issue helpers (`slugOf`, `headlineOf`)

**Files:**
- Create: `neonews-site/app/lib/issues.ts`
- Test: `neonews-site/app/lib/issues.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `slugOf(generatedAt: Date): string` → `"YYYY-MM-DD-HHMM"` in UTC.
  - `headlineOf(body: string): string` → text of the first `## ` heading, else `"Issue — YYYY-MM-DD"` is **not** built here (no date available); the fallback is the literal `"Untitled issue"`. (Date-based labels are composed in the route from `generated_at`.)

- [ ] **Step 1: Write the failing tests**

`neonews-site/app/lib/issues.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { headlineOf, slugOf } from "./issues";

describe("slugOf", () => {
  it("formats generated_at as YYYY-MM-DD-HHMM in UTC", () => {
    expect(slugOf(new Date("2026-07-26T03:13:00Z"))).toBe("2026-07-26-0313");
  });

  it("zero-pads single-digit month, day, hour, and minute", () => {
    expect(slugOf(new Date("2026-01-05T09:07:00Z"))).toBe("2026-01-05-0907");
  });

  it("uses UTC, not local time", () => {
    // Midnight UTC must not roll back a day regardless of the runner's timezone.
    expect(slugOf(new Date("2026-07-26T00:00:00Z"))).toBe("2026-07-26-0000");
  });
});

describe("headlineOf", () => {
  it("returns the text of the first level-2 heading", () => {
    const body = "# Issue — 2026-07-26\n\n## County approves budget\n\nBody text.";
    expect(headlineOf(body)).toBe("County approves budget");
  });

  it("ignores level-1 and level-3 headings", () => {
    const body = "# Big\n\n### small\n\n## The lead\n\ntext";
    expect(headlineOf(body)).toBe("The lead");
  });

  it("falls back to 'Untitled issue' when there is no level-2 heading", () => {
    expect(headlineOf("# Only an h1\n\njust prose")).toBe("Untitled issue");
  });

  it("trims surrounding whitespace from the heading text", () => {
    expect(headlineOf("##   Spaced out   \n")).toBe("Spaced out");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd neonews-site && pnpm run test
```
Expected: FAIL — cannot resolve `./issues` / `slugOf` is not a function.

- [ ] **Step 3: Implement the helpers**

`neonews-site/app/lib/issues.ts`:
```ts
/** The issue's slug: YYYY-MM-DD-HHMM in UTC, matching neonews's issue file stems. */
export function slugOf(generatedAt: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return (
    `${generatedAt.getUTCFullYear()}-${p(generatedAt.getUTCMonth() + 1)}-` +
    `${p(generatedAt.getUTCDate())}-${p(generatedAt.getUTCHours())}${p(generatedAt.getUTCMinutes())}`
  );
}

/** The lead headline: the first `## ` heading in the issue body, or a fallback. */
export function headlineOf(body: string): string {
  const match = body.match(/^##\s+(.+?)\s*$/m);
  return match ? match[1].trim() : "Untitled issue";
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd neonews-site && pnpm run test
```
Expected: PASS — all 8 tests green.

- [ ] **Step 5: Commit**

```bash
git add neonews-site/app/lib/issues.ts neonews-site/app/lib/issues.test.ts
git commit -m "feat(neonews-site): slug and headline helpers with tests"
```

---

### Task 3: Data layer + issue list (home)

**Files:**
- Create: `neonews-site/app/lib/db.server.ts`
- Create: `neonews-site/app/components/theme-toggle.tsx`, `neonews-site/app/components/site-header.tsx`
- Modify: `neonews-site/app/routes/home.tsx` (replace the placeholder)

**Interfaces:**
- Consumes: `slugOf`, `headlineOf` (Task 2); `cn` (Task 1).
- Produces:
  - Type `IssueSummary = { slug: string; headline: string; generatedAt: string; coversSince: string; storyCount: number }`.
  - Type `IssueDetail = { slug: string; headline: string; generatedAt: string; coversSince: string; storyCount: number; body: string }`.
  - `listIssues(): Promise<IssueSummary[]>` — newest first, skips `body IS NULL` rows.
  - `getIssue(slug: string): Promise<IssueDetail | null>` (used by Task 4).
  - `<SiteHeader />` masthead component.

- [ ] **Step 1: Create the DB access module**

`neonews-site/app/lib/db.server.ts` (the `.server` suffix keeps `pg` and the URL out of the client bundle):
```ts
import { Pool } from "pg";

import { headlineOf, slugOf } from "./issues";

export type IssueSummary = {
  slug: string;
  headline: string;
  generatedAt: string; // ISO
  coversSince: string; // ISO
  storyCount: number;
};

export type IssueDetail = IssueSummary & { body: string };

// One pool per process. NEONEWS_POSTGRES_URL is the same database neonews uses;
// this app only ever reads neonews_issues.
const url = process.env.NEONEWS_POSTGRES_URL;
if (!url) throw new Error("NEONEWS_POSTGRES_URL is not set");
const pool = new Pool({ connectionString: url });

type Row = {
  generated_at: Date;
  covers_since: Date;
  story_count: number;
  body: string;
};

function toSummary(row: Row): IssueSummary {
  return {
    slug: slugOf(row.generated_at),
    headline: headlineOf(row.body),
    generatedAt: row.generated_at.toISOString(),
    coversSince: row.covers_since.toISOString(),
    storyCount: row.story_count,
  };
}

export async function listIssues(): Promise<IssueSummary[]> {
  const { rows } = await pool.query<Row>(
    `SELECT generated_at, covers_since, story_count, body
       FROM neonews_issues
      WHERE body IS NOT NULL
      ORDER BY generated_at DESC`,
  );
  return rows.map(toSummary);
}

export async function getIssue(slug: string): Promise<IssueDetail | null> {
  // Slug is derived from generated_at, so match by re-deriving it in SQL:
  // to_char in UTC produces the same YYYY-MM-DD-HHMM string slugOf builds.
  const { rows } = await pool.query<Row>(
    `SELECT generated_at, covers_since, story_count, body
       FROM neonews_issues
      WHERE body IS NOT NULL
        AND to_char(generated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD-HH24MI') = $1
      ORDER BY generated_at DESC
      LIMIT 1`,
    [slug],
  );
  if (rows.length === 0) return null;
  return { ...toSummary(rows[0]), body: rows[0].body };
}
```

- [ ] **Step 2: Create the theme toggle** (verbatim from `app/components/theme-toggle.tsx`)

`neonews-site/app/components/theme-toggle.tsx`:
```tsx
const THEME_ATTR = "data-theme";

function toggleTheme() {
  const root = document.documentElement;
  const current =
    root.getAttribute(THEME_ATTR) ??
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  root.setAttribute(THEME_ATTR, current === "dark" ? "light" : "dark");
}

export function ThemeToggle() {
  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label="Toggle color theme"
      className="rounded-full border border-line-strong px-3 py-1.5 font-display text-xs text-muted transition-colors hover:border-accent hover:text-ink"
    >
      theme
    </button>
  );
}
```

- [ ] **Step 3: Create the masthead**

`neonews-site/app/components/site-header.tsx`:
```tsx
import { Link } from "react-router";

import { ThemeToggle } from "./theme-toggle";

export function SiteHeader() {
  return (
    <header className="border-b border-line-strong">
      <div className="mx-auto flex max-w-[var(--maxw)] items-center justify-between px-6 py-5">
        <Link to="/" className="font-display text-2xl font-bold tracking-tight text-ink">
          Longview Local
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
```

- [ ] **Step 4: Replace the home route with the issue list**

`neonews-site/app/routes/home.tsx`:
```tsx
import { Link } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { listIssues } from "~/lib/db.server";
import type { Route } from "./+types/home";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Longview Local — the news" },
    { name: "description", content: "Local news for Longview, drafted daily." },
  ];
}

export async function loader() {
  return { issues: await listIssues() };
}

const DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "long",
  day: "numeric",
  year: "numeric",
});

export default function Home({ loaderData }: Route.ComponentProps) {
  const { issues } = loaderData;
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-[var(--maxw)] px-6 py-10">
        {issues.length === 0 ? (
          <p className="text-muted">No issues published yet. Check back soon.</p>
        ) : (
          <ul className="divide-y divide-line">
            {issues.map((issue) => (
              <li key={issue.slug} className="py-6">
                <Link to={`/${issue.slug}`} className="group block">
                  <h2 className="font-display text-2xl font-semibold text-ink group-hover:text-accent">
                    {issue.headline}
                  </h2>
                  <p className="mt-1 text-sm text-muted">
                    {DATE.format(new Date(issue.generatedAt))} · {issue.storyCount}{" "}
                    {issue.storyCount === 1 ? "story" : "stories"}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 5: Typecheck and build**

```bash
cd neonews-site && pnpm run typecheck && pnpm run build
```
Expected: both succeed. `db.server.ts` must not be imported by any client component — only route loaders import it.

- [ ] **Step 6: Verify against a local database**

Point the site at the local `neonews_test` DB (created per `neonews/README.md`) and load the index. An empty DB proves the empty state renders without crashing; a seeded row proves the list renders.

```bash
cd neonews-site && NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test \
  pnpm run start & sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/   # 200
kill %1 2>/dev/null
```
Expected: `200`, and the served HTML contains either the empty-state text or an issue headline. If `neonews_test` is unreachable, say so — a skipped check is not a passing check.

- [ ] **Step 7: Commit**

```bash
git add neonews-site/app/lib/db.server.ts neonews-site/app/components neonews-site/app/routes/home.tsx
git commit -m "feat(neonews-site): read neonews_issues and render the issue list"
```

---

### Task 4: Issue page (markdown render + 404)

**Files:**
- Modify: `neonews-site/app/routes/issue.tsx` (replace the placeholder)

**Interfaces:**
- Consumes: `getIssue` (Task 3); `SiteHeader` (Task 3); `react-markdown`, `remark-gfm`.
- Produces: the `/:slug` page; 404 on an unknown slug.

- [ ] **Step 1: Replace the issue route**

`neonews-site/app/routes/issue.tsx`:
```tsx
import Markdown from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";

import { SiteHeader } from "~/components/site-header";
import { getIssue } from "~/lib/db.server";
import type { Route } from "./+types/issue";

export function meta({ data }: Route.MetaArgs) {
  return [{ title: data ? `${data.headline} — Longview Local` : "Not found" }];
}

export async function loader({ params }: Route.LoaderArgs) {
  const issue = await getIssue(params.slug);
  if (!issue) throw new Response(null, { status: 404, statusText: "Issue not found" });
  return issue;
}

const DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "long",
  day: "numeric",
  year: "numeric",
});

export default function Issue({ loaderData }: Route.ComponentProps) {
  const issue = loaderData;
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-[var(--maxw)] px-6 py-10">
        <Link to="/" className="font-display text-sm text-accent hover:underline">
          ← All issues
        </Link>
        <p className="mt-6 text-sm text-muted">{DATE.format(new Date(issue.generatedAt))}</p>
        <article className="article-prose mt-2">
          <Markdown remarkPlugins={[remarkGfm]}>{issue.body}</Markdown>
        </article>
      </main>
    </>
  );
}
```

Note: the issue body already carries the `## ` headline as its first heading, so the article renders it — no separate title element is needed.

- [ ] **Step 2: Typecheck and build**

```bash
cd neonews-site && pnpm run typecheck && pnpm run build
```
Expected: both succeed.

- [ ] **Step 3: Verify render and 404 against the local database**

```bash
cd neonews-site && NEONEWS_POSTGRES_URL=postgresql://ingestion:ingestion@localhost:5432/neonews_test \
  pnpm run start & sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/2000-01-01-0000   # 404 (no such issue)
kill %1 2>/dev/null
```
Expected: `404` for a slug that does not exist. If `neonews_test` holds a real issue, also fetch its slug (from the index HTML) and confirm it returns `200` with the body rendered as HTML (`<h2>`, `<p>`).

- [ ] **Step 4: Commit**

```bash
git add neonews-site/app/routes/issue.tsx
git commit -m "feat(neonews-site): render an issue page from markdown, 404 unknown slugs"
```

---

### Task 5: Deploy and publish at longviewlocal.news

**Files:**
- Modify: `deploy/deploy.sh`
- Modify: `deploy/__main__.py`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: the `neonews-site` image; the existing `neonews-secret` (carries `NEONEWS_POSTGRES_URL`) and `neonews_migrate` Job — both defined by the prerequisite plan (`2026-07-26-prefect-and-neonews-in-k3s.md`, Task 4). If that plan has not been run, its `neonews-secret`/`neonews-migrate` do not exist yet; deploy the prerequisite first.
- Produces: a `neonews-site` Deployment + Service, published via the existing cloudflared tunnel.

- [ ] **Step 1: Add the image build to `deploy.sh`**

Read `deploy/deploy.sh` first to match its variable names (`$TAG`, `$HERE`) and the existing web-image lines. After the web-image build+push, add:
```bash
echo ">> build + push localhost:5000/anything-neonews-site:$TAG"
docker build -q -t "localhost:5000/anything-neonews-site:$TAG" "$HERE/../neonews-site" >/dev/null
docker push "localhost:5000/anything-neonews-site:$TAG" >/dev/null
```
And in the `pulumi config set` block, alongside the existing image configs:
```bash
pulumi config set neonewsSiteImage "localhost:5000/anything-neonews-site:$TAG"
```

- [ ] **Step 2: Add the Deployment + Service in `deploy/__main__.py`**

After the neonews resources (from the prerequisite plan), following the existing `web` Deployment/Service as the template:
```python
# --- neonews public site (longviewlocal.news) ---
# A React Router 8 SSR app that reads neonews_issues directly and renders the
# drafted issues. Published via the existing cloudflared tunnel (add the public
# hostname in the Cloudflare dashboard — see deploy/README.md).
neonews_site_image = cfg.require("neonewsSiteImage")  # localhost:5000/anything-neonews-site:<tag>
neonews_site_deploy = k8s.apps.v1.Deployment(
    "neonews-site",
    metadata=meta("neonews-site"),
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "neonews-site"}},
        "template": {
            "metadata": {"labels": {"app": "neonews-site"}},
            "spec": {
                "containers": [
                    {
                        "name": "neonews-site",
                        "image": neonews_site_image,
                        "imagePullPolicy": "Always",
                        "ports": [{"containerPort": 3000}],
                        "env": [{"name": "PORT", "value": "3000"}],
                        # neonews-secret carries NEONEWS_POSTGRES_URL (the only var the site reads).
                        "envFrom": [{"secretRef": {"name": "neonews-secret"}}],
                        "readinessProbe": {
                            "httpGet": {"path": "/healthz", "port": 3000},
                            "initialDelaySeconds": 5,
                            "periodSeconds": 10,
                        },
                        "resources": {"requests": {"cpu": "50m", "memory": "96Mi"}, "limits": {"memory": "384Mi"}},
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[neonews_migrate]),
)
neonews_site_svc = k8s.core.v1.Service(
    "neonews-site",
    metadata=meta("neonews-site"),
    spec={"selector": {"app": "neonews-site"}, "ports": [{"port": 80, "targetPort": 3000}]},
    opts=pulumi.ResourceOptions(depends_on=[neonews_site_deploy]),
)
pulumi.export("neonews_site_service", "neonews-site.ingestion.svc.cluster.local:80")
```
`neonews_migrate` is the neonews migration Job from the prerequisite plan; it guarantees `neonews_issues` exists before the site starts. If the symbol name in `__main__.py` differs, match the actual name.

- [ ] **Step 3: Deploy**

```bash
cd deploy && ./deploy.sh home manual-site
```
(If deploying without the image build, `KUBECONFIG=./kubeconfig ./venv/bin/pulumi up --yes --stack home` after setting `neonewsSiteImage`.) The passphrase is read from `deploy/.passphrase` — do not print it.

- [ ] **Step 4: Verify the workload against the real cluster**

```bash
export KUBECONFIG=deploy/kubeconfig
kubectl get pods -n ingestion | grep neonews-site           # Running, 1/1 READY
kubectl logs -n ingestion deploy/neonews-site --tail=20     # react-router-serve listening on :3000
kubectl exec -n ingestion deploy/neonews-site -- wget -qO- http://localhost:3000/healthz   # ok
```
Expected: the pod is READY (its probe gates on `/healthz`) and the health check returns `ok`. A crashloop with "NEONEWS_POSTGRES_URL is not set" means `neonews-secret` is missing — the prerequisite plan's Task 4 has not been applied.

- [ ] **Step 5: Add the Cloudflare public hostname (manual)**

In the Cloudflare dashboard, on the **existing** tunnel (the one serving `desk.sinpi.software`), add a public hostname:
- **Hostname:** `longviewlocal.news`
- **Service:** `http://neonews-site.ingestion.svc.cluster.local:80`

No new tunnel, token, or cluster resource. Then verify from the public internet:
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://longviewlocal.news/healthz   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://longviewlocal.news/          # 200
```
Expected: `200` for both. The index shows the empty state until `draft-issue` has run in-cluster (the prerequisite pipeline); once it has, issues appear and each slug renders.

- [ ] **Step 6: Document in `deploy/README.md`**

Under the config section:
```markdown
- `neonewsSiteImage` (required) — set by `deploy.sh`, e.g.
  `localhost:5000/anything-neonews-site:<tag>`. The React Router 8 SSR site that
  renders neonews's drafted issues.
```
And under the Cloudflare / tunnel section:
```markdown
### longviewlocal.news

The public news site is served by the `neonews-site` Deployment and published on
the **same** cloudflared tunnel as `desk.sinpi.software`. In the Cloudflare
dashboard, add a public hostname on that tunnel:

- `longviewlocal.news` → `http://neonews-site.ingestion.svc.cluster.local:80`

cloudflared runs in-cluster, so it reaches the Service directly — no Traefik
Ingress and no second tunnel.
```

- [ ] **Step 7: Commit**

```bash
git add deploy/deploy.sh deploy/__main__.py deploy/README.md
git commit -m "feat(deploy): serve neonews-site and publish longviewlocal.news"
```

---

## Self-Review notes

- **Spec coverage:** scaffold/stack (Task 1) · pure helpers + tests (Task 2) · `db.server.ts` direct Postgres read (Task 3) · routes `/`, `/:slug`, `/healthz` (Tasks 1, 3, 4) · markdown render (Task 4) · deploy Deployment/Service + `deploy.sh` image + Cloudflare hostname + README (Task 5) · testing = vitest + typecheck/build + live checks (throughout). Look/newsroom theme is in Task 1's `app.css` and Task 3's masthead; specific brand wording ("Longview Local") is a reasonable default chosen at implementation, per the spec.
- **Prerequisite dependency** on the neonews `neonews-secret`/`neonews_migrate` (other plan) is called out in Task 5's Interfaces and verification.
- **Type consistency:** `IssueSummary`/`IssueDetail`, `listIssues`/`getIssue`, `slugOf`/`headlineOf` are named identically across Tasks 2–4.

## Rollback

The site is additive: `git revert` the commits and `pulumi up` removes the `neonews-site` Deployment/Service; remove the `longviewlocal.news` public hostname in the Cloudflare dashboard. Nothing else in the cluster depends on it.
```

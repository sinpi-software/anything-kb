# Wiki Page View — Design

**Goal:** A readable per-entity **wiki page** — the entity's article (rendered markdown), its relationships as clickable links to other entity pages, and its source references — reached by clicking a node in the graph or following a relationship link.

**Context:** Sub-project **C**, the final piece of the self-building-wiki north star. **A** (self-building schema) and **B** (wiki-grade articles) shipped; B already exposes `Node.article`, `Node.edges`, and `Node.references` over the cookie GraphQL, so **C is almost entirely frontend** — it consumes existing read data. See `knowledge-graph-engine-direction` memory.

**Architecture in one line:** a new React Router 8 route `/app/entity/:id` whose SSR loader queries the cookie-authed GraphQL for the entity, and whose component renders the markdown `article` + a relationships section (edges → links) + a references list; the Explore graph's nodes become clickable, navigating to these pages.

**Tech stack (unchanged + one dep):** React Router 8 (SSR), Tailwind v4, the cookie GraphQL at `/api/graphql`; **add `react-markdown` + `remark-gfm`** (off-the-shelf, safe markdown rendering).

## Global Constraints

- **Tenancy:** the page reads through the **cookie GraphQL** (`/api/graphql`), which resolves the knowledge base from the session. An entity id belonging to another knowledge base returns `node: null` → the page 404s. Never accept a knowledge-base id from the client.
- **Markdown safety:** render with `react-markdown` in its **default (no raw HTML)** mode — do NOT enable `rehype-raw`/`dangerouslySetInnerHTML`. The article is LLM-generated; treat it as untrusted and render only parsed markdown elements.
- **Auth gate:** the route uses the same SSR redirect-to-login gate as the other `/app/*` pages (`getMe(request)` → redirect if absent).
- **Consistency:** reuse the existing `SiteHeader` + `APP_NAV_LINKS`, theme tokens, and mobile-first layout patterns from `config.tsx`/`ingest.tsx`.

## 1. Data layer

- `app/app/lib/types.ts`: an `EntityPage` type — `{ id, name, type, summary, article: string | null, edges: { type: string; target: { id: string; name: string; type: string } }[], references: { label: string; date: string }[] }`.
- `app/app/lib/auth.server.ts`: `getEntity(request, id): Promise<EntityPage | null>` — POSTs the GraphQL query below to `INTERNAL_API_URL + "/api/graphql"` forwarding the Cookie header (same pattern as `getMe`/`getKeys`/`getConfig`); returns `data.node` or `null` on any error/absent node.
  ```graphql
  query($id: ID!) {
    node(id: $id) {
      id name type summary article
      edges { type target { id name type } }
      references { label date }
    }
  }
  ```

## 2. The entity route

- Register `route("app/entity/:id", "routes/entity.tsx")` in `app/app/routes.ts`.
- **Loader:** `getMe(request)` → redirect to `/login?next=/app/entity/:id` if absent; then `getEntity(request, params.id)`; if `null`, `throw new Response("Not found", { status: 404 })`. Return `{ me, entity }`.
- **Component** (wiki layout, mobile-first):
  - `SiteHeader` with `APP_NAV_LINKS` + a Log out action (as the other app pages).
  - **Header:** the entity `name` as `<h1>`, with a small type badge (e.g. the type in an accent pill).
  - **Article:** render `entity.article` with `<ReactMarkdown remarkPlugins={[remarkGfm]}>` inside a scoped prose container (§3). If `article` is empty, fall back to `summary`, and if that's empty too, an "No article yet — ingest more about this entity." empty state.
  - **Relationships:** group `edges` by `type`; render each group as a labeled list, each target a React Router `<Link to={"/app/entity/" + target.id}>` showing `target.name` (+ a muted type). Hide the section if there are no edges.
  - **References:** a list of `references` (label + formatted date); a source with an empty label shows the date only. Hide the section if empty.

## 3. Markdown styling

Add a scoped prose style so the rendered article "reads like a wiki page" — a small block in `app/app/app.css` (or a colocated stylesheet) targeting the article container's descendants (`h1`/`h2`/`h3`, `p`, `ul`/`ol`/`li`, `a`, `code`, `blockquote`) using the existing theme tokens (`--ink`, `--muted`, `--accent`, `--line`, `--font-display` for headings/code). Keep spacing generous and line length readable (`max-width` ~65ch on the body column). No external typography plugin — a focused hand-styled set is enough.

## 4. Entry point — clickable graph nodes

- `app/app/components/graph-explorer.tsx`: add `onNodeClick={(node) => navigate("/app/entity/" + node.id)}` to the force graph (via `useNavigate` from react-router). A node already carries its `id`. This makes the Explore graph the primary way into pages; relationship links then move page→page.

## Testing / verification

- No unit-test framework on the frontend — the gate is `pnpm run typecheck` + `pnpm run build` (Node 22.23.1), both green.
- **Live/browser smoke** (after deploy): with a logged-in session, open a real entity page — confirm the markdown article renders with headings, the relationships link to other entity pages (and clicking one navigates), and the references list shows labels + dates; confirm clicking a node in Explore lands on its page; confirm a bad/foreign id 404s.

## Out of scope (explicit)

- **Auto-linked prose** (turning entity names inside the article text into links) — v1 uses the structured Relationships section only.
- **Editing** articles/relationships from the page (read-only view).
- A separate browsable/searchable entity index — the Explore graph already serves that.
- Inline citation markers in the body (page-level references only — a B decision).

## Resolved decisions

1. **Dedicated route** `/app/entity/:id` (shareable wiki URLs), not a side panel in Explore.
2. Links appear in a **structured Relationships section only**; the article prose stays clean (auto-linking deferred).
3. `react-markdown` + `remark-gfm`, **no raw HTML** (safe by default).
4. The **Explore graph** is the browse/search index; node-click is the primary entry point (plus relationship links between pages).
5. Foreign/unknown ids **404** via the tenancy-scoped cookie GraphQL returning `null`.

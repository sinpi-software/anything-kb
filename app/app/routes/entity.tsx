import { LogOut } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Link, redirect, useNavigate } from "react-router";
import remarkGfm from "remark-gfm";

import { SiteHeader } from "~/components/site-header";
import { Button } from "~/components/ui/button";
import { logout } from "~/lib/api";
import { getEntity, getMe } from "~/lib/auth.server";
import { appNavLinks } from "~/lib/nav";
import type { Route } from "./+types/entity";

export function meta({ loaderData }: Route.MetaArgs) {
  return [{ title: loaderData?.entity ? `${loaderData.entity.name} — anything/kb` : "Entity — anything/kb" }];
}

export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/entity/${params.id}`);
  const entity = await getEntity(request, params.id);
  if (!entity) throw new Response("Not found", { status: 404 });
  return { me, entity, kbId: params.kbId };
}

function formatDate(value: string): string {
  if (!value) return "";
  const d = new Date(value);
  return isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(d);
}

export default function Entity({ loaderData }: Route.ComponentProps) {
  const { me, entity, kbId } = loaderData;
  const navigate = useNavigate();

  const groups = new Map<string, typeof entity.edges>();
  for (const e of entity.edges) {
    const arr = groups.get(e.type) ?? [];
    arr.push(e);
    groups.set(e.type, arr);
  }
  const body = entity.article || entity.summary || "";

  const relatedGroups = new Map<string, typeof entity.related>();
  for (const r of entity.related) {
    const arr = relatedGroups.get(r.type) ?? [];
    arr.push(r);
    relatedGroups.set(r.type, arr);
  }

  async function handleLogout() {
    await logout();
    await navigate("/login");
  }

  return (
    <div className="min-h-svh">
      <SiteHeader
        navLinks={appNavLinks(kbId)}
        kbName={me.knowledge_bases.find((kb) => kb.knowledge_base_id === kbId)?.knowledge_base_name}
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

        {entity.related.length > 0 ? (
          <section className="mt-10">
            <h2 className="font-display text-sm font-semibold tracking-wide text-muted uppercase">Related</h2>
            <p className="mt-1 text-xs text-muted">Entities two hops away in the graph.</p>
            <div className="mt-3 flex flex-col gap-3">
              {[...relatedGroups.entries()].map(([type, ents]) => (
                <div key={type} className="flex flex-col gap-1 sm:flex-row sm:gap-3">
                  <span className="font-display text-sm text-muted sm:w-40 sm:flex-none">{type}</span>
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    {ents.map((e) => (
                      <Link key={e.id} to={`/app/entity/${e.id}`} className="text-accent hover:underline">
                        {e.name}
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

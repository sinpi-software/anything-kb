import { useState } from "react";
import { redirect, useNavigate } from "react-router";

import { GraphExplorer } from "~/components/graph-explorer";
import { GraphiQLPanel } from "~/components/graphiql-panel";
import { SiteHeader } from "~/components/site-header";
import { Button } from "~/components/ui/button";
import { logout } from "~/lib/api";
import { getMe } from "~/lib/auth.server";
import { appNavLinks } from "~/lib/nav";
import { cn } from "~/lib/utils";
import type { Route } from "./+types/explore";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Explore — anything/kb" }];
}

export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}/explore`);
  return { me, kbId: params.kbId };
}

type Tab = "graph" | "query";

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "border-b-2 px-1 pb-2 font-display text-sm transition-colors",
        active ? "border-accent-fill text-ink" : "border-transparent text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

export default function Explore({ loaderData }: Route.ComponentProps) {
  const { me, kbId } = loaderData;
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("graph");

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
            Log out
          </Button>
        }
      />
      <main className="mx-auto max-w-(--maxw) px-5 py-8 sm:px-7 sm:py-12">
        <p className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">Explore</p>
        <h1 className="mt-3.5 text-3xl font-semibold tracking-tight">Your graph.</h1>
        <p className="mt-1 text-muted">
          Visualize entities and relationships, or run GraphQL queries against your knowledge base.
        </p>

        <div className="mt-7 flex gap-6 border-b border-line">
          <TabButton active={tab === "graph"} onClick={() => setTab("graph")}>
            Visualize
          </TabButton>
          <TabButton active={tab === "query"} onClick={() => setTab("query")}>
            Query
          </TabButton>
        </div>

        <div className="mt-6">
          {tab === "graph" ? <GraphExplorer kbId={kbId} /> : <GraphiQLPanel kbId={kbId} />}
        </div>
      </main>
    </div>
  );
}

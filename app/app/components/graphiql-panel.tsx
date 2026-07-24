import { type ReactNode, useEffect, useState } from "react";

import "graphiql/style.css";

const DEFAULT_QUERY = `# Your knowledge graph, over GraphQL.
# Requests are authenticated by your session — no API key needed here.
{
  nodes(search: "", limit: 25) {
    id
    type
    name
    summary
    edges {
      type
      target {
        name
        type
      }
    }
  }
}
`;

// GraphiQL + its Monaco editor are browser-only and heavy, so everything is
// dynamically imported on the client after mount (never during SSR).
export function GraphiQLPanel() {
  const [content, setContent] = useState<ReactNode>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      await import("graphiql/setup-workers/vite");
      const [{ GraphiQL }, { createGraphiQLFetcher }] = await Promise.all([
        import("graphiql"),
        import("@graphiql/toolkit"),
      ]);
      if (!alive) return;
      const fetcher = createGraphiQLFetcher({
        url: "/api/graphql",
        // Same-origin, but be explicit so the session cookie always rides along.
        fetch: (input, init) => fetch(input, { ...init, credentials: "include" }),
      });
      setContent(<GraphiQL fetcher={fetcher} defaultQuery={DEFAULT_QUERY} />);
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="graphiql-container h-[72vh] overflow-hidden rounded-xl border border-line">
      {content ?? <div className="p-6 font-display text-sm text-muted">Loading query editor…</div>}
    </div>
  );
}

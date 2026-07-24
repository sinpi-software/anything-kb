import { Loader2, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";

interface GraphNode {
  id: string;
  name: string;
  type: string;
}
interface GraphLink {
  source: string;
  target: string;
  label: string;
}
interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

const GRAPH_QUERY = `query($search: String) {
  nodes(search: $search, limit: 200) {
    id name type
    edges { type target { id name type } }
  }
}`;

// Deterministic color per entity type, from the same jewel palette as the hero graph.
const PALETTE = ["#77BDB4", "#E39BA3", "#9AA7E0", "#AFC88F", "#CDAAD9", "#E8B341", "#8FBCD4", "#D4A88F"];
function colorForType(type: string): string {
  let sum = 0;
  for (const ch of type) sum += ch.charCodeAt(0);
  return PALETTE[sum % PALETTE.length];
}

async function fetchGraph(search: string): Promise<GraphData> {
  const res = await fetch("/api/graphql", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: GRAPH_QUERY, variables: { search: search || null } }),
  });
  const body = await res.json();
  if (body.errors) throw new Error(body.errors[0]?.message ?? "Query failed.");
  const nodes = new Map<string, GraphNode>();
  const links: GraphLink[] = [];
  for (const node of body.data.nodes) {
    nodes.set(node.id, { id: node.id, name: node.name, type: node.type });
    for (const edge of node.edges) {
      const target = edge.target;
      if (!nodes.has(target.id)) nodes.set(target.id, { id: target.id, name: target.name, type: target.type });
      links.push({ source: node.id, target: target.id, label: edge.type });
    }
  }
  return { nodes: [...nodes.values()], links };
}

function readColors() {
  const s = getComputedStyle(document.documentElement);
  return {
    bg: s.getPropertyValue("--panel").trim(),
    label: s.getPropertyValue("--panel-ink").trim(),
    link: s.getPropertyValue("--panel-muted").trim(),
  };
}

export function GraphExplorer() {
  // react-force-graph-2d is browser-only (canvas), so it's dynamically imported on the client.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [ForceGraph, setForceGraph] = useState<any>(null);
  const [data, setData] = useState<GraphData>({ nodes: [], links: [] });
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(800);
  const [colors, setColors] = useState({ bg: "#0e1517", label: "#e9eeec", link: "#8b9c97" });

  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    import("react-force-graph-2d").then((mod) => setForceGraph(() => mod.default));
  }, []);

  // Read the themed console colors once, and refresh when the theme changes.
  useEffect(() => {
    const refresh = () => setColors(readColors());
    refresh();
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", refresh);
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", refresh);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const update = () => setWidth(el.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const load = useCallback(async (term: string) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchGraph(term));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load the graph.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    load(search);
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input
          placeholder="Search entities by name…"
          value={search}
          onValueChange={setSearch}
          className="flex-1"
        />
        <Button type="submit" variant="outline" className="flex-none text-sm">
          <Search className="size-4" aria-hidden="true" />
          Search
        </Button>
        <span className="font-display text-xs text-muted">
          {data.nodes.length} node{data.nodes.length === 1 ? "" : "s"} · {data.links.length} edge
          {data.links.length === 1 ? "" : "s"}
        </span>
      </form>

      {error ? (
        <div className="rounded-lg border border-[#c0392b]/30 bg-[#c0392b]/10 px-4 py-3 text-sm text-[#c0392b] dark:text-[#e39ba3]">
          {error}
        </div>
      ) : null}

      <div
        ref={wrap}
        className="relative h-[62vh] overflow-hidden rounded-xl border border-panel-line bg-panel"
      >
        {loading ? (
          <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 font-display text-sm text-panel-muted">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            Loading graph…
          </div>
        ) : null}
        {!loading && data.nodes.length === 0 && !error ? (
          <div className="absolute inset-0 flex items-center justify-center px-6 text-center font-display text-sm text-panel-muted">
            No entities yet — ingest some content, then explore it here.
          </div>
        ) : null}
        {ForceGraph && data.nodes.length > 0 ? (
          <ForceGraph
            graphData={data}
            width={width}
            height={Math.round(typeof window === "undefined" ? 480 : window.innerHeight * 0.62)}
            backgroundColor={colors.bg}
            nodeRelSize={5}
            nodeLabel={(n: GraphNode) => `${n.name} · ${n.type}`}
            linkColor={() => colors.link}
            linkDirectionalArrowLength={3}
            linkDirectionalArrowRelPos={1}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, scale: number) => {
              ctx.beginPath();
              ctx.arc(node.x, node.y, 5, 0, 2 * Math.PI);
              ctx.fillStyle = colorForType(node.type);
              ctx.fill();
              const fontSize = Math.max(10 / scale, 2);
              ctx.font = `${fontSize}px ui-monospace, monospace`;
              ctx.textAlign = "center";
              ctx.textBaseline = "top";
              ctx.fillStyle = colors.label;
              ctx.fillText(node.name, node.x, node.y + 7);
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
              ctx.beginPath();
              ctx.arc(node.x, node.y, 7, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
            linkCanvasObjectMode={() => "after"}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            linkCanvasObject={(link: any, ctx: CanvasRenderingContext2D, scale: number) => {
              const s = link.source;
              const t = link.target;
              if (!s || !t || typeof s.x !== "number" || typeof t.x !== "number") return;
              const mx = (s.x + t.x) / 2;
              const my = (s.y + t.y) / 2;
              const fontSize = Math.max(8 / scale, 1.5);
              ctx.font = `${fontSize}px ui-monospace, monospace`;
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
              // A background pill so the edge line doesn't cut through the label.
              const textWidth = ctx.measureText(link.label).width;
              const pad = 3 / scale;
              ctx.fillStyle = colors.bg;
              ctx.fillRect(mx - textWidth / 2 - pad, my - fontSize / 2 - pad, textWidth + pad * 2, fontSize + pad * 2);
              ctx.fillStyle = colors.link;
              ctx.fillText(link.label, mx, my);
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

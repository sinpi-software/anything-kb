import { useEffect, useRef } from "react";

type EntityType = "person" | "org" | "product" | "place" | "topic";

interface GraphNode {
  id: string;
  label: string;
  t: EntityType;
  x: number;
  y: number;
  key?: boolean;
}

interface GraphEdge {
  a: string;
  b: string;
  rel: string;
}

const HUES: Record<EntityType, string> = {
  person: "#E39BA3",
  org: "#77BDB4",
  product: "#9AA7E0",
  place: "#AFC88F",
  topic: "#CDAAD9",
};

// normalized layout (0..1), hand-placed for a legible composition
const NODES: GraphNode[] = [
  { id: "altman", label: "Sam Altman", t: "person", x: 0.2, y: 0.24 },
  { id: "openai", label: "OpenAI", t: "org", x: 0.44, y: 0.4, key: true },
  { id: "gpt", label: "GPT-5", t: "product", x: 0.3, y: 0.68 },
  { id: "sf", label: "San Francisco", t: "place", x: 0.64, y: 0.18 },
  { id: "anthropic", label: "Anthropic", t: "org", x: 0.74, y: 0.56 },
  { id: "amodei", label: "Dario Amodei", t: "person", x: 0.9, y: 0.34 },
  { id: "claude", label: "Claude", t: "product", x: 0.82, y: 0.82 },
];

const EDGES: GraphEdge[] = [
  { a: "altman", b: "openai", rel: "LEADS" },
  { a: "openai", b: "gpt", rel: "CREATED" },
  { a: "openai", b: "sf", rel: "BASED_IN" },
  { a: "anthropic", b: "openai", rel: "COMPETES" },
  { a: "amodei", b: "anthropic", rel: "LEADS" },
  { a: "anthropic", b: "claude", rel: "CREATED" },
];

function hexA(hex: string, alpha: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

export function HeroGraphCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const wrap = canvas?.parentElement;
    if (!canvas || !ctx || !wrap) return;

    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const byId: Record<string, GraphNode & { i: number; ph: number }> = {};
    const nodes = NODES.map((n, i) => ({ ...n, i, ph: i * 1.7 }));
    nodes.forEach((n) => (byId[n.id] = n));

    let width = 0;
    let height = 0;
    let animationFrame = 0;

    function resize() {
      const rect = wrap!.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas!.width = Math.round(width * dpr);
      canvas!.height = Math.round(height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function pos(n: GraphNode & { ph: number }, t: number) {
      const padX = 78;
      const padY = 52;
      const driftX = reduce ? 0 : Math.sin(t * 0.00035 + n.ph) * 9;
      const driftY = reduce ? 0 : Math.cos(t * 0.0003 + n.ph * 1.3) * 9;
      return {
        x: padX + n.x * (width - padX * 1.5) + driftX,
        y: padY + n.y * (height - padY * 1.7) + driftY,
      };
    }

    let start: number | null = null;

    function frame(ts: number) {
      if (start === null) start = ts;
      const intro = reduce ? 1 : Math.min(1, (ts - start) / 1500);
      const easeIntro = 1 - Math.pow(1 - intro, 3);

      ctx!.clearRect(0, 0, width, height);
      const positions: Record<string, { x: number; y: number }> = {};
      nodes.forEach((n) => (positions[n.id] = pos(n, ts)));

      ctx!.lineWidth = 1;
      EDGES.forEach((e, k) => {
        const a = positions[e.a];
        const b = positions[e.b];
        const prog = reduce ? 1 : Math.max(0, Math.min(1, easeIntro * EDGES.length - k));
        if (prog <= 0) return;
        const mx = a.x + (b.x - a.x) * prog;
        const my = a.y + (b.y - a.y) * prog;
        ctx!.strokeStyle = "rgba(233,238,236,0.16)";
        ctx!.beginPath();
        ctx!.moveTo(a.x, a.y);
        ctx!.lineTo(mx, my);
        ctx!.stroke();
        if (prog > 0.98) {
          const lx = (a.x + b.x) / 2;
          const ly = (a.y + b.y) / 2;
          ctx!.font = '600 9.5px ui-monospace, "SF Mono", Menlo, monospace';
          ctx!.textAlign = "center";
          ctx!.textBaseline = "middle";
          const tw = ctx!.measureText(e.rel).width;
          ctx!.fillStyle = "rgba(14,21,23,0.82)";
          ctx!.fillRect(lx - tw / 2 - 5, ly - 7, tw + 10, 14);
          ctx!.fillStyle = "rgba(139,156,151,0.95)";
          ctx!.fillText(e.rel, lx, ly + 0.5);
        }
      });

      nodes.forEach((n) => {
        const p = positions[n.id];
        const na = reduce ? 1 : Math.max(0, Math.min(1, easeIntro * nodes.length - n.i));
        if (na <= 0) return;
        const col = HUES[n.t];
        const r = n.key ? 8.5 : 6.5;
        ctx!.beginPath();
        ctx!.fillStyle = hexA(n.key ? "#E8B341" : col, 0.14 * na);
        ctx!.arc(p.x, p.y, r + 9, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.beginPath();
        ctx!.fillStyle = hexA(n.key ? "#E8B341" : col, na);
        ctx!.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.lineWidth = 1.5;
        ctx!.strokeStyle = hexA("#0E1517", 0.55 * na);
        ctx!.stroke();
        ctx!.font = '600 11.5px ui-monospace, "SF Mono", Menlo, monospace';
        ctx!.textAlign = "center";
        ctx!.textBaseline = "top";
        ctx!.fillStyle = hexA("#E9EEEC", 0.92 * na);
        ctx!.fillText(n.label, p.x, p.y + r + 6);
      });

      if (!reduce) animationFrame = requestAnimationFrame(frame);
    }

    function handleResize() {
      resize();
      if (reduce) frame(performance.now());
    }

    resize();
    window.addEventListener("resize", handleResize);
    animationFrame = requestAnimationFrame(frame);

    return () => {
      window.removeEventListener("resize", handleResize);
      cancelAnimationFrame(animationFrame);
    };
  }, []);

  return (
    <div className="relative min-h-[440px] flex-1 self-stretch overflow-hidden rounded-l-2xl border border-r-0 border-panel-line bg-[radial-gradient(120%_100%_at_30%_20%,#16211F_0%,var(--panel)_55%,var(--panel-2)_100%)] max-md:min-h-[360px] max-md:rounded-l-2xl">
      <canvas ref={canvasRef} className="absolute inset-0 block h-full w-full" />
      <span className="absolute bottom-4 left-4.5 font-display text-xs tracking-[0.16em] text-panel-muted uppercase">
        live graph · your entities, your edges
      </span>
    </div>
  );
}

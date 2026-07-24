import { ArrowRight } from "lucide-react";
import { Link } from "react-router";

import { CopyButton } from "~/components/copy-button";
import { HeroGraphCanvas } from "~/components/hero-graph-canvas";
import { Reveal } from "~/components/reveal";
import { SiteHeader } from "~/components/site-header";
import { Button } from "~/components/ui/button";
import type { Route } from "./+types/home";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "A knowledge base for anything" },
    {
      name: "description",
      content:
        "A knowledge base for anything — feed it any text, get a typed, queryable knowledge graph.",
    },
  ];
}

const NAV_LINKS = [
  { href: "#how", label: "How it works" },
  { href: "#query", label: "Query" },
  { href: "#why", label: "Why" },
  { href: "#pricing", label: "Pricing" },
];

const DOMAINS = [
  {
    eyebrow: "Market intel",
    eyebrowColor: "text-t-org",
    title: "Track who's who",
    chips: [
      { label: "Company", color: "bg-t-org" },
      { label: "Founder", color: "bg-t-person" },
      { label: "Product", color: "bg-t-product" },
      { label: "Round", color: "bg-t-topic" },
    ],
    rel: (
      <>
        <b className="text-ink">Founder</b> <span className="text-accent">— LEADS →</span>{" "}
        <b className="text-ink">Company</b> <span className="text-accent">— RAISED →</span>{" "}
        <b className="text-ink">Round</b>
      </>
    ),
  },
  {
    eyebrow: "Research watch",
    eyebrowColor: "text-t-place",
    title: "Follow a field",
    chips: [
      { label: "Researcher", color: "bg-t-person" },
      { label: "Paper", color: "bg-t-product" },
      { label: "Method", color: "bg-t-topic" },
      { label: "Lab", color: "bg-t-org" },
    ],
    rel: (
      <>
        <b className="text-ink">Researcher</b> <span className="text-accent">— AUTHORED →</span>{" "}
        <b className="text-ink">Paper</b> <span className="text-accent">— USES →</span>{" "}
        <b className="text-ink">Method</b>
      </>
    ),
  },
  {
    eyebrow: "Threat intel",
    eyebrowColor: "text-t-person",
    title: "Map an adversary",
    chips: [
      { label: "Actor", color: "bg-t-person" },
      { label: "Malware", color: "bg-t-product" },
      { label: "Target", color: "bg-t-place" },
      { label: "Campaign", color: "bg-t-topic" },
    ],
    rel: (
      <>
        <b className="text-ink">Actor</b> <span className="text-accent">— DEPLOYS →</span>{" "}
        <b className="text-ink">Malware</b> <span className="text-accent">— HITS →</span>{" "}
        <b className="text-ink">Target</b>
      </>
    ),
  },
];

const PIPELINE = [
  {
    num: "01 · Ingest",
    title: "Post any text",
    body: "One authenticated endpoint. Content is queued durably and acknowledged — a slow model never blocks you, and nothing gets dropped.",
  },
  {
    num: "02 · Filter",
    title: "Keep the signal",
    body: "A relevance prompt you write decides what's worth keeping. Off-topic content is set aside — the graph stays free of noise.",
  },
  {
    num: "03 · Extract",
    title: "Pull the entities",
    body: "Entities and relationships — constrained to the types you configured — are pulled out and resolved against what's already there.",
  },
  {
    num: "04 · Merge",
    title: "Grow the graph",
    body: "New facts are deduplicated into your graph, each edge carrying provenance back to the source it came from.",
  },
];

const FEATURES = [
  {
    title: "Relevance, not a firehose",
    body: "A prompt you control gates every item, so the base fills with what matters and skips the rest — deliberately, with a reason recorded.",
  },
  {
    title: "Your schema, your graph",
    body: "Declare the entity and relationship types for your domain. Extraction is held to them, so the structure is yours — not a generic guess.",
  },
  {
    title: "It compounds",
    body: "Every source is resolved and merged into what's already known. The same entity from ten sources stays one node, richer each time.",
  },
  {
    title: "Grounded in the source",
    body: "Every node and edge links back to the content it came from. Nothing in the graph is unattributable.",
  },
  {
    title: "Isolated by tenant",
    body: "Every read and write is scoped by an API key. One key, one graph — you never see another tenant's, and they never see yours.",
  },
  {
    title: "One API, any consumer",
    body: "Push in over REST, read out over GraphQL. Build a newsletter, a monitor, or a research tool on top — the graph is the stable interface.",
  },
];

const TIERS = [
  {
    name: "Free",
    tag: "For your first graph.",
    specs: [
      ["1", "organization"],
      ["1,000", "ingestions / month"],
      ["3", "entity types"],
      ["3", "relationship types"],
    ],
    cta: { label: "Start free", href: "/register" },
    featured: false,
  },
  {
    name: "Pro",
    tag: "For a working knowledge base.",
    specs: [
      ["10", "organizations"],
      ["10,000", "ingestions / month"],
      ["Unlimited", "entity types"],
      ["Unlimited", "relationship types"],
    ],
    cta: { label: "Go Pro", href: "/register" },
    featured: true,
  },
  {
    name: "Enterprise",
    tag: "For scale and isolation on your terms.",
    specs: [
      ["Unlimited", "organizations"],
      ["Custom", "ingestion volume"],
      ["Unlimited", "entity types"],
      ["Unlimited", "relationship types"],
    ],
    cta: { label: "Contact us", href: "mailto:hello@sinpi.software" },
    featured: false,
  },
];

const FOOTER_LEGEND = [
  { label: "Person", color: "bg-t-person" },
  { label: "Organization", color: "bg-t-org" },
  { label: "Product", color: "bg-t-product" },
  { label: "Place", color: "bg-t-place" },
  { label: "Topic", color: "bg-t-topic" },
];

const CURL_SAMPLE =
  'curl -X POST https://your-host/content -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "{\\"text\\":\\"OpenAI, led by Sam Altman, released GPT-5.\\"}"';

function SectionHead({
  eyebrow,
  title,
  body,
}: {
  eyebrow: string;
  title: string;
  body: string;
}) {
  return (
    <Reveal className="mb-11 max-w-[60ch]">
      <p className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">
        {eyebrow}
      </p>
      <h2 className="mt-3.5 text-[clamp(1.65rem,3.4vw,2.35rem)] leading-[1.05] font-semibold tracking-[-0.02em]">
        {title}
      </h2>
      <p className="mt-4 max-w-[52ch] text-lg text-muted">{body}</p>
    </Reveal>
  );
}

export default function Home() {
  return (
    <div>
      <SiteHeader navLinks={NAV_LINKS} />

      <main>
        <section className="mx-auto max-w-(--maxw) px-7">
          <div className="grid min-h-[min(76vh,720px)] grid-cols-1 items-center gap-6 py-10 md:grid-cols-[1.02fr_1fr]">
            <div className="relative z-2 max-w-[33ch]">
              <p className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">
                Knowledge-graph engine
              </p>
              <h1 className="mt-4.5 text-[clamp(2.5rem,6.4vw,4.5rem)] leading-[0.96] font-semibold tracking-[-0.035em]">
                A knowledge base for <span className="text-accent">anything</span>.
              </h1>
              <p className="mt-6.5 max-w-[42ch] text-lg text-muted">
                Point it at any stream of text — articles, email, docs, feeds. Describe what
                you care about in one sentence, and it builds a living, typed knowledge graph
                you can query. The noise never makes it in.
              </p>
              <div className="mt-8.5 flex flex-wrap items-center gap-3.5">
                <Button
                  render={
                    <a href="#query">
                      See it work <ArrowRight className="size-4" aria-hidden="true" />
                    </a>
                  }
                />
                <Button variant="ghost" render={<a href="#how">How it works</a>} />
              </div>
            </div>
            <HeroGraphCanvas />
          </div>
        </section>

        <section id="anything" className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <SectionHead
            eyebrow="One engine, any subject"
            title="You name the pieces. It builds the graph."
            body="You declare the entity and relationship types that matter to your work. The engine reads the content, extracts exactly those, and connects them — the same API whether you're tracking a market, a research field, or a threat."
          />
          <div className="grid grid-cols-1 gap-4.5 md:grid-cols-3">
            {DOMAINS.map((domain) => (
              <Reveal
                key={domain.title}
                as="article"
                className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-6.5 transition-[border-color,transform] hover:-translate-y-0.75 hover:border-line-strong"
              >
                <p className={`font-display text-xs ${domain.eyebrowColor}`}>{domain.eyebrow}</p>
                <h3 className="text-lg tracking-[-0.01em]">{domain.title}</h3>
                <div className="flex flex-wrap gap-1.75">
                  {domain.chips.map((chip) => (
                    <span
                      key={chip.label}
                      className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-2.25 py-1 font-display text-xs"
                    >
                      <span className={`size-1.75 rounded-full ${chip.color}`} />
                      {chip.label}
                    </span>
                  ))}
                </div>
                <p className="font-display text-sm leading-relaxed text-muted">{domain.rel}</p>
              </Reveal>
            ))}
          </div>
        </section>

        <section id="how" className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <SectionHead
            eyebrow="The pipeline"
            title="Content in. Structure out. Nothing lost."
            body="Every item runs the same four steps, off the request path, retried until it lands."
          />
          <Reveal
            as="div"
            className="grid grid-cols-1 overflow-hidden rounded-2xl border border-line sm:grid-cols-2 md:grid-cols-4"
          >
            {PIPELINE.map((step, i) => (
              <div
                key={step.num}
                className="relative border-b border-line bg-surface p-6.5 last:border-b-0 md:border-r md:border-b-0 md:last:border-r-0"
              >
                <span className="font-display text-xs font-semibold tracking-[0.04em] text-accent">
                  {step.num}
                </span>
                <h3 className="mt-3.5 mb-2 text-base tracking-[-0.005em]">{step.title}</h3>
                <p className="text-sm leading-relaxed text-muted">{step.body}</p>
                {i < PIPELINE.length - 1 ? (
                  <span
                    aria-hidden="true"
                    className="absolute top-7 right-[-7px] hidden font-display text-line-strong md:block"
                  >
                    →
                  </span>
                ) : null}
              </div>
            ))}
          </Reveal>
          <p className="mt-5 font-display text-sm text-muted">
            Then read it all back through <b className="text-ink">one GraphQL endpoint</b>, scoped
            to you.
          </p>
        </section>

        <section id="query" className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <SectionHead
            eyebrow="The developer surface"
            title="Ask the graph a question."
            body="A generic schema: entity and relationship types are data, so one query shape works for any graph you build. Here's a real request and its real response."
          />
          <Reveal
            as="div"
            className="grid grid-cols-1 overflow-hidden rounded-2xl border border-panel-line md:grid-cols-2"
          >
            <div className="bg-panel">
              <div className="flex items-center gap-2 border-b border-panel-line px-4.5 py-3 font-display text-xs tracking-[0.14em] text-panel-muted uppercase">
                <span className="text-beacon">POST</span> /graphql · query
              </div>
              <pre className="overflow-x-auto p-5 font-display text-sm leading-relaxed text-panel-ink">
                <span className="text-panel-muted">{"{"}</span>{"\n  "}
                <span className="text-t-org">nodes</span>
                <span className="text-panel-muted">(</span>
                <span className="text-beacon">type</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"Organization"</span>
                <span className="text-panel-muted">) {"{"}</span>{"\n    "}
                <span className="text-t-org">name</span>{"\n    "}
                <span className="text-t-org">edges</span>
                <span className="text-panel-muted"> {"{"}</span>{"\n      "}
                <span className="text-t-org">type</span>{"\n      "}
                <span className="text-t-org">target</span>
                <span className="text-panel-muted"> {"{"} </span>
                <span className="text-t-org">name</span>{" "}
                <span className="text-t-org">type</span>
                <span className="text-panel-muted"> {"}"}</span>
                {"\n    "}
                <span className="text-panel-muted">{"}"}</span>
                {"\n  "}
                <span className="text-panel-muted">{"}"}</span>
                {"\n"}
                <span className="text-panel-muted">{"}"}</span>
              </pre>
            </div>
            <div className="border-t border-panel-line bg-panel-2 md:border-t-0 md:border-l">
              <div className="flex items-center gap-2 border-b border-panel-line px-4.5 py-3 font-display text-xs tracking-[0.14em] text-panel-muted uppercase">
                200 OK · application/json
              </div>
              <pre className="overflow-x-auto p-5 font-display text-sm leading-relaxed text-panel-ink">
                <span className="text-panel-muted">{"{ "}</span>
                <span className="text-t-org">"data"</span>
                <span className="text-panel-muted">: {"{ "}</span>
                <span className="text-t-org">"nodes"</span>
                <span className="text-panel-muted">: [</span>
                {"\n  "}
                <span className="text-panel-muted">{"{ "}</span>
                <span className="text-t-org">"name"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"OpenAI"</span>
                <span className="text-panel-muted">,</span>
                {"\n    "}
                <span className="text-t-org">"edges"</span>
                <span className="text-panel-muted">: [{"{ "}</span>
                <span className="text-t-org">"type"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-beacon">"CREATED"</span>
                <span className="text-panel-muted">,</span>
                {"\n      "}
                <span className="text-t-org">"target"</span>
                <span className="text-panel-muted">: {"{ "}</span>
                <span className="text-t-org">"name"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"GPT-5"</span>
                <span className="text-panel-muted">, </span>
                <span className="text-t-org">"type"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"Product"</span>
                <span className="text-panel-muted"> {"} }]"} {"}"},</span>
                {"\n  "}
                <span className="text-panel-muted">{"{ "}</span>
                <span className="text-t-org">"name"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"Anthropic"</span>
                <span className="text-panel-muted">,</span>
                {"\n    "}
                <span className="text-t-org">"edges"</span>
                <span className="text-panel-muted">: [{"{ "}</span>
                <span className="text-t-org">"type"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-beacon">"CREATED"</span>
                <span className="text-panel-muted">,</span>
                {"\n      "}
                <span className="text-t-org">"target"</span>
                <span className="text-panel-muted">: {"{ "}</span>
                <span className="text-t-org">"name"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"Claude"</span>
                <span className="text-panel-muted">, </span>
                <span className="text-t-org">"type"</span>
                <span className="text-panel-muted">: </span>
                <span className="text-t-place">"Product"</span>
                <span className="text-panel-muted"> {"} }] }"}</span>
                {"\n"}
                <span className="text-panel-muted">{"] } }"}</span>
              </pre>
            </div>
          </Reveal>

          <Reveal
            as="div"
            className="mt-4 flex items-center gap-3.5 overflow-x-auto rounded-lg border border-panel-line bg-panel px-3.5 py-3"
          >
            <code className="font-display text-sm whitespace-nowrap text-panel-ink">
              <span className="font-semibold text-beacon">POST</span> /content{"   "}
              <span className="text-panel-muted">
                {'{ "text": "OpenAI, led by Sam Altman, released GPT-5." }'}
              </span>
            </code>
            <CopyButton text={CURL_SAMPLE} className="ml-auto" />
          </Reveal>
        </section>

        <section id="why" className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <SectionHead
            eyebrow="Why it's built this way"
            title="A graph you can trust to grow."
            body=""
          />
          <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 md:grid-cols-3 md:gap-x-10">
            {FEATURES.map((feature, i) => (
              <Reveal
                key={feature.title}
                className="flex flex-col gap-2 border-t border-line pt-5"
              >
                <span className="font-display text-xs tracking-[0.08em] text-accent">
                  / {String(i + 1).padStart(2, "0")}
                </span>
                <h3 className="text-base tracking-[-0.01em]">{feature.title}</h3>
                <p className="text-[0.98rem] leading-relaxed text-muted">{feature.body}</p>
              </Reveal>
            ))}
          </div>
        </section>

        <section id="pricing" className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <SectionHead
            eyebrow="Pricing"
            title="Start free. Grow into it."
            body="Every tier is the same engine and the same API. What changes is how much you ingest each month and how many entity and relationship types you can define."
          />
          <div className="grid grid-cols-1 items-stretch gap-4.5 md:grid-cols-3">
            {TIERS.map((tier) => (
              <Reveal
                key={tier.name}
                as="article"
                className={`relative flex flex-col gap-4.5 rounded-2xl border bg-surface p-7 ${
                  tier.featured
                    ? "border-accent-fill/60 shadow-[0_26px_60px_-34px_var(--accent-fill)] md:order-first"
                    : "border-line"
                }`}
              >
                {tier.featured ? (
                  <span className="absolute -top-2.75 left-6.5 rounded-full bg-accent-fill px-2.75 py-1 font-display text-[0.64rem] font-semibold tracking-[0.13em] text-accent-ink uppercase">
                    Most popular
                  </span>
                ) : null}
                <p
                  className={`font-display text-2xl font-semibold tracking-[-0.02em] ${tier.featured ? "text-accent" : ""}`}
                >
                  {tier.name}
                </p>
                <p className="-mt-1.5 text-muted">{tier.tag}</p>
                <ul className="flex flex-col gap-3.5 border-t border-line pt-5">
                  {tier.specs.map(([value, label]) => (
                    <li key={label} className="flex items-baseline gap-3">
                      <b className="min-w-[4.4ch] font-display text-xl leading-none font-semibold tracking-[-0.02em] tabular-nums text-ink">
                        {value}
                      </b>
                      <span className="text-sm text-muted">{label}</span>
                    </li>
                  ))}
                </ul>
                {tier.featured ? (
                  <Button
                    className="mt-auto justify-center"
                    render={
                      <Link to={tier.cta.href}>
                        {tier.cta.label} <ArrowRight className="size-4" aria-hidden="true" />
                      </Link>
                    }
                  />
                ) : (
                  <Button
                    variant="outline"
                    className="mt-auto justify-center text-center"
                    render={
                      tier.cta.href.startsWith("mailto:") ? (
                        <a href={tier.cta.href}>{tier.cta.label}</a>
                      ) : (
                        <Link to={tier.cta.href}>{tier.cta.label}</Link>
                      )
                    }
                  />
                )}
              </Reveal>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-(--maxw) border-t border-line px-7 py-16 md:py-24">
          <Reveal
            as="div"
            className="flex flex-wrap items-center justify-between gap-7 rounded-[18px] border border-line bg-[radial-gradient(120%_140%_at_100%_0%,color-mix(in_srgb,var(--accent-fill)_12%,transparent)_0%,transparent_46%)] bg-surface p-9 md:p-16"
          >
            <div>
              <h2 className="max-w-[16ch] text-[clamp(1.7rem,4vw,2.6rem)] leading-[1.02] font-semibold tracking-[-0.025em]">
                Feed it anything. Ask it everything.
              </h2>
              <p className="mt-3 max-w-[40ch] text-muted">
                Bring the content and the handful of types you care about. Get back a graph that
                answers questions.
              </p>
            </div>
            <Button
              render={
                <Link to="/register">
                  Start with a POST <ArrowRight className="size-4" aria-hidden="true" />
                </Link>
              }
            />
          </Reveal>
        </section>
      </main>

      <footer className="mx-auto max-w-(--maxw) border-t border-line px-7 py-9">
        <div className="flex flex-wrap items-center justify-between gap-4 font-display text-sm text-muted">
          <span>anything/kb — a knowledge-graph engine</span>
          <span className="flex flex-wrap gap-3.5">
            {FOOTER_LEGEND.map((item) => (
              <span key={item.label} className="inline-flex items-center gap-1.5">
                <span className={`size-2 rounded-full ${item.color}`} />
                {item.label}
              </span>
            ))}
          </span>
        </div>
      </footer>
    </div>
  );
}

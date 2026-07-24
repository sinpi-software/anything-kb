import { CheckCircle2, Loader2, MinusCircle, Send, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { redirect, useNavigate } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { ApiError, getJob, ingestContent, logout } from "~/lib/api";
import { getMe } from "~/lib/auth.server";
import { APP_NAV_LINKS } from "~/lib/nav";
import { cn } from "~/lib/utils";
import type { Route } from "./+types/ingest";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Ingest — anything/kb" }];
}

export async function loader({ request }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect("/login?next=/app/ingest");
  return { me };
}

const POLL_MS = 1500;
const MAX_POLLS = 60; // ~90s ceiling before we stop polling and tell the user to check back

type Phase =
  | { kind: "idle" }
  | { kind: "working"; label: string }
  | { kind: "done" }
  | { kind: "skipped"; reason: string | null }
  | { kind: "failed"; error: string | null }
  | { kind: "error"; message: string };

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const CARD_BASE = "flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm";
const NEUTRAL = "border-line-strong bg-surface text-muted";
const SUCCESS = "border-t-org/30 bg-t-org/10 text-t-org";
const DANGER = "border-[#c0392b]/30 bg-[#c0392b]/10 text-[#c0392b] dark:text-[#e39ba3]";

function StatusCard({ phase }: { phase: Phase }) {
  if (phase.kind === "idle") return null;
  if (phase.kind === "working") {
    return (
      <div role="status" className={cn(CARD_BASE, NEUTRAL, "items-center")}>
        <Loader2 className="size-4 flex-none animate-spin" aria-hidden="true" />
        <span>{phase.label}</span>
      </div>
    );
  }
  if (phase.kind === "done") {
    return (
      <div role="status" className={cn(CARD_BASE, SUCCESS)}>
        <CheckCircle2 className="mt-0.5 size-4 flex-none" aria-hidden="true" />
        <span>
          <span className="font-semibold">Ingested.</span> Relevant — entities and relationships were
          extracted into your graph.
        </span>
      </div>
    );
  }
  if (phase.kind === "skipped") {
    return (
      <div role="status" className={cn(CARD_BASE, NEUTRAL)}>
        <MinusCircle className="mt-0.5 size-4 flex-none" aria-hidden="true" />
        <span>
          <span className="font-semibold">Skipped</span> — judged not relevant to your knowledge base.
          {phase.reason ? <> “{phase.reason}”</> : null}
        </span>
      </div>
    );
  }
  const message =
    phase.kind === "failed" ? (phase.error ?? "Something went wrong during processing.") : phase.message;
  return (
    <div role="alert" className={cn(CARD_BASE, DANGER)}>
      <XCircle className="mt-0.5 size-4 flex-none" aria-hidden="true" />
      <span>
        {phase.kind === "failed" ? <span className="font-semibold">Failed. </span> : null}
        {message}
      </span>
    </div>
  );
}

export default function Ingest({ loaderData }: Route.ComponentProps) {
  const { me } = loaderData;
  const navigate = useNavigate();
  const knowledgeBase = me.knowledge_bases[0];

  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const mounted = useRef(true);
  useEffect(() => {
    return () => {
      mounted.current = false;
    };
  }, []);

  const working = phase.kind === "working";
  const canSubmit = me.email_verified && text.trim().length > 0 && !working;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setPhase({ kind: "working", label: "Queuing…" });
    try {
      const { job_id } = await ingestContent(text, source.trim() || undefined);
      for (let i = 0; i < MAX_POLLS; i++) {
        await delay(POLL_MS);
        if (!mounted.current) return;
        const job = await getJob(job_id);
        if (job.status === "done") return setPhase({ kind: "done" });
        if (job.status === "skipped") return setPhase({ kind: "skipped", reason: job.relevance_reason });
        if (job.status === "failed") return setPhase({ kind: "failed", error: job.error });
        setPhase({ kind: "working", label: job.status === "processing" ? "Analyzing…" : "Queued — waiting…" });
      }
      setPhase({ kind: "error", message: "Still processing — check your graph again shortly." });
    } catch (err) {
      setPhase({ kind: "error", message: err instanceof ApiError ? err.message : "Couldn't ingest that." });
    }
  }

  function handleReset() {
    setText("");
    setSource("");
    setPhase({ kind: "idle" });
  }

  const finished = phase.kind === "done" || phase.kind === "skipped" || phase.kind === "failed";

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
            Log out
          </Button>
        }
      />
      <main className="mx-auto max-w-(--maxw) px-5 py-8 sm:px-7 sm:py-12">
        <p className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">
          Ingest content
        </p>
        <h1 className="mt-3.5 text-3xl font-semibold tracking-tight">Paste anything.</h1>
        {knowledgeBase ? (
          <p className="mt-1 text-muted">
            Into <span className="text-ink">{knowledgeBase.knowledge_base_name}</span>. Your relevance
            filter keeps what matters; the rest is skipped.
          </p>
        ) : null}

        <div className="mt-8 flex flex-col gap-6">
          {!me.email_verified ? (
            <VerifyEmailBanner message="Verify your email to ingest content." />
          ) : null}

          <Card>
            <CardTitle className="text-lg">New text</CardTitle>
            <CardDescription>
              Paste an article, email, doc, or transcript. It's queued, filtered for relevance, then
              extracted into your graph.
            </CardDescription>

            <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
              <Field>
                <FieldLabel>Text</FieldLabel>
                <Textarea
                  rows={12}
                  placeholder="Paste your text here…"
                  required
                  disabled={!me.email_verified || working}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>Source (optional)</FieldLabel>
                <Input
                  placeholder="e.g. newsletter, meeting-notes"
                  disabled={!me.email_verified || working}
                  value={source}
                  onValueChange={setSource}
                />
              </Field>

              <StatusCard phase={phase} />

              <div className="flex items-center gap-3">
                <Button type="submit" disabled={!canSubmit} className="flex-none">
                  <Send className="size-4" aria-hidden="true" />
                  {working ? "Ingesting…" : "Ingest"}
                </Button>
                {finished ? (
                  <Button type="button" variant="outline" onClick={handleReset} className="flex-none text-sm">
                    Ingest another
                  </Button>
                ) : null}
              </div>
            </form>
          </Card>
        </div>
      </main>
    </div>
  );
}

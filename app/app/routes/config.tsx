import { Check, Save } from "lucide-react";
import { useState } from "react";
import { redirect, useNavigate } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
import { ChipInput } from "~/components/ui/chip-input";
import { Field, FieldLabel } from "~/components/ui/field";
import { Textarea } from "~/components/ui/textarea";
import { ApiError, logout, updateConfig } from "~/lib/api";
import { getConfig, getMe } from "~/lib/auth.server";
import { APP_NAV_LINKS } from "~/lib/nav";
import { cn } from "~/lib/utils";
import type { Route } from "./+types/config";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Configure — anything/kb" }];
}

export async function loader({ request }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect("/login?next=/app/config");
  const config = await getConfig(request);
  return { me, config };
}

type Save = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };

export default function Config({ loaderData }: Route.ComponentProps) {
  const { me, config } = loaderData;
  const navigate = useNavigate();

  const [prompt, setPrompt] = useState(config.relevance_prompt);
  const [entities, setEntities] = useState(config.entity_types);
  const [relationships, setRelationships] = useState(config.relationship_types);
  const [save, setSave] = useState<Save>({ kind: "idle" });

  const disabled = !me.email_verified;
  const saving = save.kind === "saving";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (disabled || saving) return;
    setSave({ kind: "saving" });
    try {
      const next = await updateConfig({
        relevance_prompt: prompt,
        entity_types: entities,
        relationship_types: relationships,
      });
      setPrompt(next.relevance_prompt);
      setEntities(next.entity_types);
      setRelationships(next.relationship_types);
      setSave({ kind: "saved" });
    } catch (err) {
      setSave({ kind: "error", message: err instanceof ApiError ? err.message : "Couldn't save configuration." });
    }
  }

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
          Configure
        </p>
        <h1 className="mt-3.5 text-3xl font-semibold tracking-tight">Tune your knowledge base.</h1>
        <p className="mt-1 text-muted">
          The relevance prompt decides what gets in; the types decide what the graph captures. Changes
          apply to content ingested afterward.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-6">
          {disabled ? <VerifyEmailBanner message="Verify your email to edit configuration." /> : null}

          <Card>
            <CardTitle className="text-lg">Relevance filter</CardTitle>
            <CardDescription>
              A plain-language question. Content that doesn't match is skipped, never stored.
            </CardDescription>
            <Field className="mt-5">
              <FieldLabel>Relevance prompt</FieldLabel>
              <Textarea
                rows={3}
                placeholder="e.g. Is this about technology, science, or business news?"
                disabled={disabled}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
              />
            </Field>
          </Card>

          <Card>
            <CardTitle className="text-lg">Schema</CardTitle>
            <CardDescription>
              The entity and relationship types the extractor is allowed to create. Type a value and
              press Enter.
            </CardDescription>
            <div className="mt-5 flex flex-col gap-5">
              <Field>
                <FieldLabel>Entity types</FieldLabel>
                <ChipInput
                  values={entities}
                  onChange={setEntities}
                  disabled={disabled}
                  placeholder="e.g. Person, Organization, Place…"
                />
              </Field>
              <Field>
                <FieldLabel>Relationship types</FieldLabel>
                <ChipInput
                  values={relationships}
                  onChange={setRelationships}
                  disabled={disabled}
                  placeholder="e.g. WORKS_AT, FOUNDED, LOCATED_IN…"
                />
              </Field>
            </div>
          </Card>

          <div className="flex flex-wrap items-center gap-4">
            <Button type="submit" disabled={disabled || saving} className="flex-none">
              <Save className="size-4" aria-hidden="true" />
              {saving ? "Saving…" : "Save configuration"}
            </Button>
            {save.kind === "saved" ? (
              <span className={cn("inline-flex items-center gap-1.5 font-display text-sm text-t-org")}>
                <Check className="size-4" aria-hidden="true" />
                Saved
              </span>
            ) : null}
            {save.kind === "error" ? (
              <span className="font-display text-sm text-[#c0392b] dark:text-[#e39ba3]">{save.message}</span>
            ) : null}
          </div>
        </form>
      </main>
    </div>
  );
}

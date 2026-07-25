import { Check, Save } from "lucide-react";
import { useState } from "react";
import { redirect, useNavigate } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { TypeListEditor } from "~/components/type-list-editor";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
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

  const [interests, setInterests] = useState(config.interests);
  const [discoverTypes, setDiscoverTypes] = useState(config.discover_types);
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
        interests,
        discover_types: discoverTypes,
        entity_types: entities,
        relationship_types: relationships,
      });
      setInterests(next.interests);
      setDiscoverTypes(next.discover_types);
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
          What you care about decides both what gets in and what the extractor focuses on; the types
          decide what the graph captures. Changes apply to content ingested afterward.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-6">
          {disabled ? <VerifyEmailBanner message="Verify your email to edit configuration." /> : null}

          <Card>
            <CardTitle className="text-lg">Relevance &amp; focus</CardTitle>
            <CardDescription>
              A plain-language description of your interests. Content that doesn't match is skipped,
              never stored — and content that does match is extracted through this lens.
            </CardDescription>
            <Field className="mt-5">
              <FieldLabel>What I care about</FieldLabel>
              <Textarea
                rows={3}
                placeholder="e.g. Is this about technology, science, or business news?"
                disabled={disabled}
                value={interests}
                onChange={(event) => setInterests(event.target.value)}
              />
              <p className="text-xs text-muted">Decides what gets in and what's worth extracting.</p>
            </Field>
          </Card>

          <Card>
            <CardTitle className="text-lg">Schema</CardTitle>
            <CardDescription>
              The entity and relationship types the extractor may create. Give each a description —
              it's fed to the model so it knows what the type means in your domain.
            </CardDescription>
            <label className="mt-5 flex flex-wrap items-center gap-2.5 text-sm text-ink">
              <input
                type="checkbox"
                checked={discoverTypes}
                disabled={disabled}
                onChange={(event) => setDiscoverTypes(event.target.checked)}
                className="size-4 rounded border-line-strong accent-accent-fill disabled:opacity-50"
              />
              Automatically discover new types
            </label>
            <div className="mt-5 flex flex-col gap-6">
              <Field>
                <FieldLabel>Entity types</FieldLabel>
                <TypeListEditor
                  values={entities}
                  onChange={setEntities}
                  disabled={disabled}
                  namePlaceholder="Person"
                  descriptionPlaceholder="A specific, named individual…"
                />
              </Field>
              <Field>
                <FieldLabel>Relationship types</FieldLabel>
                <TypeListEditor
                  values={relationships}
                  onChange={setRelationships}
                  disabled={disabled}
                  namePlaceholder="WORKS_AT"
                  descriptionPlaceholder="A person is employed by an organization…"
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

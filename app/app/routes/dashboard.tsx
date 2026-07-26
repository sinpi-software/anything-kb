import { KeyRound, LogOut, Trash2 } from "lucide-react";
import { useState } from "react";
import { redirect, useNavigate, useRevalidator } from "react-router";

import { CopyButton } from "~/components/copy-button";
import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { ApiError, createKey, logout, revokeKey } from "~/lib/api";
import { appNavLinks } from "~/lib/nav";
import { KbNotFound, getKeys, getMe } from "~/lib/auth.server";
import type { ApiKey, CreatedApiKey } from "~/lib/types";
import { cn } from "~/lib/utils";
import type { Route } from "./+types/dashboard";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Dashboard — anything/kb" }];
}

export async function loader({ request, params }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect(`/login?next=/app/${params.kbId}`);
  try {
    const keys = await getKeys(request, params.kbId);
    return { me, keys, kbId: params.kbId };
  } catch (err) {
    if (err instanceof KbNotFound) throw redirect("/app");
    throw err;
  }
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

function CreatedKeyCallout({ created, onDismiss }: { created: CreatedApiKey; onDismiss: () => void }) {
  return (
    <Alert variant="success" className="flex-col items-stretch gap-3">
      <div>
        <span className="font-semibold">"{created.name}" created.</span> Copy it now — you won't
        see the full key again.
      </div>
      <div className="flex items-center gap-3 rounded-lg border border-panel-line bg-panel px-3.5 py-3">
        <code className="flex-1 overflow-x-auto font-display text-sm whitespace-nowrap text-panel-ink">
          {created.key}
        </code>
        <CopyButton text={created.key} />
      </div>
      <Button variant="ghost" onClick={onDismiss} className="self-start">
        Done
      </Button>
    </Alert>
  );
}

function CreateKeyForm({
  kbId,
  disabled,
  onCreated,
}: {
  kbId: string;
  disabled: boolean;
  onCreated: (key: CreatedApiKey) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createKey(kbId, name);
      setName("");
      onCreated(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create that key.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 sm:flex-row sm:items-end">
      {error ? (
        <Alert variant="error" className="sm:hidden">
          {error}
        </Alert>
      ) : null}
      <Field className="flex-1">
        <FieldLabel>New key name</FieldLabel>
        <Input
          placeholder="e.g. production"
          required
          disabled={disabled}
          value={name}
          onValueChange={setName}
        />
      </Field>
      <Button type="submit" disabled={disabled || submitting} className="flex-none">
        <KeyRound className="size-4" aria-hidden="true" />
        {submitting ? "Creating…" : "Create key"}
      </Button>
      {error ? (
        <Alert variant="error" className="hidden w-full sm:flex">
          {error}
        </Alert>
      ) : null}
    </form>
  );
}

function KeyRow({ apiKey, onRevoke, revoking }: { apiKey: ApiKey; onRevoke: (id: string) => void; revoking: boolean }) {
  const revoked = Boolean(apiKey.revoked_at);
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-4 last:border-b-0">
      <div>
        <p className="font-medium text-ink">{apiKey.name}</p>
        <p className="font-display text-sm text-muted">
          {apiKey.prefix}… · created {formatDate(apiKey.created_at)} · last used{" "}
          {formatDate(apiKey.last_used_at)}
        </p>
      </div>
      {revoked ? (
        <span className="font-display text-xs tracking-wide text-muted uppercase">
          Revoked {formatDate(apiKey.revoked_at)}
        </span>
      ) : (
        <Button
          variant="outline"
          onClick={() => onRevoke(apiKey.id)}
          disabled={revoking}
          className={cn("text-sm")}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          {revoking ? "Revoking…" : "Revoke"}
        </Button>
      )}
    </li>
  );
}

export default function Dashboard({ loaderData }: Route.ComponentProps) {
  const { me, keys, kbId } = loaderData;
  const navigate = useNavigate();
  const revalidator = useRevalidator();

  const [createdKey, setCreatedKey] = useState<CreatedApiKey | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  async function handleLogout() {
    await logout();
    await navigate("/login");
  }

  async function handleRevoke(id: string) {
    setRevokingId(id);
    try {
      await revokeKey(kbId, id);
      await revalidator.revalidate();
    } finally {
      setRevokingId(null);
    }
  }

  function handleCreated(created: CreatedApiKey) {
    setCreatedKey(created);
    revalidator.revalidate();
  }

  const primaryKnowledgeBase = me.knowledge_bases[0];

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
      <main className="mx-auto max-w-(--maxw) px-5 py-8 sm:px-7 sm:py-12">
        <p className="font-display text-xs font-semibold tracking-[0.2em] text-accent uppercase">
          Dashboard
        </p>
        <h1 className="mt-3.5 text-3xl font-semibold tracking-tight">{me.email}</h1>
        {primaryKnowledgeBase ? <p className="mt-1 text-muted">{primaryKnowledgeBase.knowledge_base_name}</p> : null}

        <div className="mt-8 flex flex-col gap-6">
          {!me.email_verified ? (
            <VerifyEmailBanner message="Verify your email to create an API key." />
          ) : null}
          {createdKey ? (
            <CreatedKeyCallout created={createdKey} onDismiss={() => setCreatedKey(null)} />
          ) : null}

          <Card>
            <CardTitle className="text-lg">API keys</CardTitle>
            <CardDescription>
              Keys authenticate requests to <code>/content</code> and <code>/graphql</code>.
            </CardDescription>

            <div className="mt-6">
              <CreateKeyForm kbId={kbId} disabled={!me.email_verified} onCreated={handleCreated} />
            </div>

            {keys.length > 0 ? (
              <ul className="mt-6 border-t border-line">
                {keys.map((apiKey) => (
                  <KeyRow
                    key={apiKey.id}
                    apiKey={apiKey}
                    onRevoke={handleRevoke}
                    revoking={revokingId === apiKey.id}
                  />
                ))}
              </ul>
            ) : (
              <p className="mt-6 text-sm text-muted">No API keys yet.</p>
            )}
          </Card>
        </div>
      </main>
    </div>
  );
}

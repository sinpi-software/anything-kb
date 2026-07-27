import { useState } from "react";
import { redirect, useNavigate, useRevalidator } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { VerifyEmailBanner } from "~/components/verify-email-banner";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  ApiError,
  createKnowledgeBase,
  deleteKnowledgeBase,
  logout,
  renameKnowledgeBase,
} from "~/lib/api";
import { getMe, listKnowledgeBases } from "~/lib/auth.server";
import type { KnowledgeBase } from "~/lib/types";
import type { Route } from "./+types/knowledge-bases";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Knowledge bases — anything/kb" }];
}

export async function loader({ request }: Route.LoaderArgs) {
  const me = await getMe(request);
  if (!me) throw redirect("/login?next=/app");
  const knowledgeBases = await listKnowledgeBases(request);
  return { me, knowledgeBases };
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function CreateForm({ disabled }: { disabled: boolean }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [charter, setCharter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const kb = await createKnowledgeBase(name, charter);
      // Straight into the new knowledge base — creating one and then hunting for it
      // in the list is a wasted step.
      await navigate(`/app/${kb.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Field>
        <FieldLabel>Name</FieldLabel>
        <Input value={name} onValueChange={setName} required placeholder="e.g. Competitor research" />
      </Field>
      <Field>
        <FieldLabel>Charter (optional)</FieldLabel>
        <Input value={charter} onValueChange={setCharter} placeholder="What this knowledge base is for" />
      </Field>
      <Button type="submit" disabled={disabled || busy || !name.trim()} className="self-start">
        {busy ? "Creating…" : "Create knowledge base"}
      </Button>
    </form>
  );
}

function RenameRow({ kb, onDone }: { kb: KnowledgeBase; onDone: () => void }) {
  const [name, setName] = useState(kb.name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await renameKnowledgeBase(kb.id, name);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Input value={name} onValueChange={setName} required />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function DeleteRow({ kb, onDone }: { kb: KnowledgeBase; onDone: () => void }) {
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await deleteKnowledgeBase(kb.id, confirmName);
      onDone();
    } catch (err) {
      // 409 "only knowledge base" and 422 "name mismatch" both arrive as ApiError with
      // the API's own detail text, which says the useful thing already.
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Alert variant="error">
        This deletes the knowledge base and everything in its graph, permanently. Type{" "}
        <strong>{kb.name}</strong> to confirm.
      </Alert>
      {error ? <Alert variant="error">{error}</Alert> : null}
      <Input value={confirmName} onValueChange={setConfirmName} placeholder={kb.name} />
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || confirmName !== kb.name}>
          {busy ? "Deleting…" : "Delete permanently"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

type RowMode = "view" | "rename" | "delete";

function KnowledgeBaseRow({ kb }: { kb: KnowledgeBase }) {
  const revalidator = useRevalidator();
  const [mode, setMode] = useState<RowMode>("view");
  const isOwner = kb.role === "owner";

  function done() {
    setMode("view");
    revalidator.revalidate();
  }

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>
            <a href={`/app/${kb.id}`} className="hover:underline">
              {kb.name}
            </a>
          </CardTitle>
          <CardDescription>
            {kb.role} · created {formatDate(kb.created_at)}
            {kb.charter ? ` · ${kb.charter}` : ""}
          </CardDescription>
        </div>
        {mode === "view" && isOwner ? (
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setMode("rename")}>
              Rename
            </Button>
            <Button variant="ghost" onClick={() => setMode("delete")}>
              Delete
            </Button>
          </div>
        ) : null}
      </div>
      {mode === "rename" ? <RenameRow kb={kb} onDone={done} /> : null}
      {mode === "delete" ? <DeleteRow kb={kb} onDone={done} /> : null}
    </Card>
  );
}

export default function KnowledgeBases({ loaderData }: Route.ComponentProps) {
  const { me, knowledgeBases } = loaderData;
  const navigate = useNavigate();

  async function signOut() {
    await logout();
    await navigate("/login");
  }

  return (
    <>
      <SiteHeader
        actions={
          <Button variant="ghost" onClick={signOut}>
            Log out
          </Button>
        }
      />
      <main className="mx-auto flex max-w-(--maxw) flex-col gap-8 px-5 py-10 sm:px-7">
        {me.email_verified ? null : (
          <VerifyEmailBanner message="Verify your email to create a knowledge base." />
        )}
        <div>
          <h1 className="font-display text-2xl font-semibold text-ink">Knowledge bases</h1>
          <p className="text-muted">Each one is a separate graph, with its own config and API keys.</p>
        </div>
        <div className="flex flex-col gap-3">
          {knowledgeBases.map((kb) => (
            <KnowledgeBaseRow key={kb.id} kb={kb} />
          ))}
        </div>
        <Card>
          <CardTitle>New knowledge base</CardTitle>
          <CreateForm disabled={!me.email_verified} />
        </Card>
      </main>
    </>
  );
}

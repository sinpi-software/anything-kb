import { useEffect, useState } from "react";
import { useFetcher, type SubmitTarget } from "react-router";
import { toast } from "sonner";
import { Trash2, Plus } from "lucide-react";
import type { Route } from "./+types/desk.transformations";
import { listTransformations, type TransformationRow } from "~/services/transformations.server";
import {
  TRANSFORMATION_TYPES,
  parseParams,
  transformationInputSchema,
  type TransformationInput,
} from "~/schemas/transformation";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { ParamsFields, paramsFromRecord, type ParamsFieldsValue } from "~/components/transformations/ParamsFields";

export async function loader({ params }: Route.LoaderArgs) {
  return { orgId: params.org_id, transformations: await listTransformations(params.org_id) };
}

type Draft = {
  type: (typeof TRANSFORMATION_TYPES)[number];
  model: string;
  prompt: string;
  params: ParamsFieldsValue;
};

function toDraft(row: TransformationRow): Draft {
  return {
    type: row.type as Draft["type"],
    model: row.model ?? "",
    prompt: row.prompt,
    params: paramsFromRecord(row.params as Record<string, unknown> | null),
  };
}

function buildPayload(draft: Draft): { ok: true; value: TransformationInput } | { ok: false; error: string } {
  const paramsResult = parseParams(draft.params.extraJson);
  if (!paramsResult.ok) return { ok: false, error: paramsResult.error };
  const known: Record<string, number> = {};
  for (const [k, v] of Object.entries(draft.params.known)) {
    if (v.trim() !== "") {
      const n = Number(v);
      if (Number.isNaN(n)) return { ok: false, error: `${k} must be a number` };
      known[k] = n;
    }
  }
  const params = { ...(paramsResult.value ?? {}), ...known };
  const candidate = {
    type: draft.type,
    model: draft.model.trim() || null,
    prompt: draft.prompt,
    params: Object.keys(params).length ? params : null,
  };
  const parsed = transformationInputSchema.safeParse(candidate);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const message = issue?.path.length ? `${issue.path.join(".")}: ${issue.message}` : (issue?.message ?? "Invalid input");
    return { ok: false, error: message };
  }
  return { ok: true, value: parsed.data };
}

function fetcherFailed(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const { error, errors } = data as { error?: unknown; errors?: unknown };
  return Boolean(error || errors);
}

export default function TransformationsPage({ loaderData }: Route.ComponentProps) {
  const { orgId, transformations } = loaderData;
  const addFetcher = useFetcher();

  useEffect(() => {
    if (addFetcher.state === "idle" && fetcherFailed(addFetcher.data)) {
      toast.error("Couldn't add — try again");
    }
  }, [addFetcher.state, addFetcher.data]);

  function add() {
    addFetcher.submit(
      { org_id: orgId, type: "summarize", prompt: "New transformation" },
      { method: "POST", action: "/api/transformations", encType: "application/json" },
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Transformations</h1>
        <Button onClick={add} disabled={addFetcher.state !== "idle"}><Plus className="mr-1 size-4" /> Add</Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead className="w-40">Type</TableHead>
            <TableHead className="w-56">Model</TableHead>
            <TableHead>Prompt</TableHead>
            <TableHead>Params</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {transformations.map((row) => (
            <EditableRow key={row.id} row={row} />
          ))}
        </TableBody>
      </Table>
    </main>
  );
}

function EditableRow({ row }: { row: TransformationRow }) {
  const fetcher = useFetcher();
  const [draft, setDraft] = useState<Draft>(() => toDraft(row));
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (fetcher.state === "idle" && fetcherFailed(fetcher.data)) {
      toast.error("Couldn't save — try again");
    }
  }, [fetcher.state, fetcher.data]);

  function save(next: Draft) {
    const payload = buildPayload(next);
    if (!payload.ok) {
      toast.error(payload.error);
      return;
    }
    fetcher.submit(payload.value as SubmitTarget, {
      method: "PATCH",
      action: `/api/transformations/${row.id}`,
      encType: "application/json",
    });
  }

  function remove() {
    fetcher.submit(null, { method: "DELETE", action: `/api/transformations/${row.id}`, encType: "application/json" });
  }

  return (
    <TableRow>
      <TableCell>{row.position}</TableCell>
      <TableCell>
        <Select
          value={draft.type}
          onValueChange={(type) => {
            if (!type) return;
            const next = { ...draft, type: type as Draft["type"] };
            setDraft(next);
            save(next);
          }}
        >
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {TRANSFORMATION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Input
          value={draft.model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          onBlur={() => save(draft)}
          placeholder="openai/gpt-4o"
        />
      </TableCell>
      <TableCell>
        <Textarea
          rows={2}
          value={draft.prompt}
          onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
          onBlur={() => save(draft)}
        />
      </TableCell>
      <TableCell>
        <ParamsFields value={draft.params} onChange={(params) => setDraft({ ...draft, params })} />
        <Button variant="ghost" size="sm" className="mt-1" onClick={() => save(draft)}>Save params</Button>
      </TableCell>
      <TableCell>
        {confirming ? (
          <Button variant="destructive" size="sm" onClick={remove} onBlur={() => setConfirming(false)}>Sure?</Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}><Trash2 className="size-4" /></Button>
        )}
      </TableCell>
    </TableRow>
  );
}

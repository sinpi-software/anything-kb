import { useEffect, useMemo, useState } from "react";
import { useFetcher, type SubmitTarget } from "react-router";
import { useForm } from "@tanstack/react-form";
import { toast } from "sonner";
import { Trash2, Plus, GripVertical, ChevronRight, ChevronDown } from "lucide-react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Route } from "./+types/desk.transformations";
import { listTransformations, type TransformationRow } from "~/services/transformations.server";
import {
  TRANSFORMATION_TYPES,
  GATE_OPS,
  parseParams,
  transformationInputSchema,
  type TransformationInput,
} from "~/schemas/transformation";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { ParamsFields, paramsFromRecord } from "~/components/transformations/ParamsFields";
import { PromptEditor } from "~/components/transformations/PromptEditor";

export async function loader({ params }: Route.LoaderArgs) {
  return { orgId: params.org_id, transformations: await listTransformations(params.org_id) };
}

type GateDraft = { source: string; field: string; op: string; value: string };
type Draft = ReturnType<typeof toDraft>;

function gateToDraft(gate: unknown): GateDraft | null {
  if (!gate || typeof gate !== "object") return null;
  const { source, field, op, value } = gate as Record<string, unknown>;
  return { source: String(source ?? ""), field: String(field ?? ""), op: String(op ?? GATE_OPS[0]), value: String(value ?? "") };
}

function toDraft(row: TransformationRow) {
  return {
    name: row.name,
    type: row.type as (typeof TRANSFORMATION_TYPES)[number],
    model: row.model ?? "",
    prompt: row.prompt,
    params: paramsFromRecord(row.params as Record<string, unknown> | null),
    gate: gateToDraft(row.gate),
  };
}

// "5" -> number 5, "5.0" -> number 5, anything else -> the trimmed string as-is.
function coerceGateValue(raw: string): string | number {
  const trimmed = raw.trim();
  return trimmed !== "" && !Number.isNaN(Number(trimmed)) ? Number(trimmed) : trimmed;
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
  const gate = draft.gate
    ? { source: draft.gate.source, field: draft.gate.field, op: draft.gate.op, value: coerceGateValue(draft.gate.value) }
    : null;
  const candidate = {
    type: draft.type,
    name: draft.name,
    model: draft.model.trim() || null,
    prompt: draft.prompt,
    params: Object.keys(params).length ? params : null,
    gate,
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
  const reorderFetcher = useFetcher();
  const [order, setOrder] = useState<TransformationRow[]>(transformations);
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  useEffect(() => setOrder(transformations), [transformations]);

  useEffect(() => {
    if (addFetcher.state === "idle" && fetcherFailed(addFetcher.data)) {
      toast.error("Couldn't add — try again");
    }
  }, [addFetcher.state, addFetcher.data]);

  useEffect(() => {
    if (reorderFetcher.state === "idle" && fetcherFailed(reorderFetcher.data)) {
      setOrder(transformations); // revert to last known-good
      toast.error("Couldn't reorder — try again");
    }
  }, [reorderFetcher.state, reorderFetcher.data, transformations]);

  function add() {
    addFetcher.submit(
      { org_id: orgId, name: `new transformation ${Date.now()}`, type: "summarize", prompt: "New transformation" },
      { method: "POST", action: "/api/transformations", encType: "application/json" },
    );
  }

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = order.findIndex((r) => r.id === active.id);
    const newIndex = order.findIndex((r) => r.id === over.id);
    const next = arrayMove(order, oldIndex, newIndex);
    setOrder(next); // optimistic
    reorderFetcher.submit(
      { org_id: orgId, ids: next.map((r) => r.id) },
      { method: "PATCH", action: "/api/transformations", encType: "application/json" },
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Transformations</h1>
        <Button onClick={add} disabled={addFetcher.state !== "idle"}><Plus className="mr-1 size-4" /> Add</Button>
      </div>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={order.map((r) => r.id)} strategy={verticalListSortingStrategy}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead className="w-12">#</TableHead>
                <TableHead className="w-40">Name</TableHead>
                <TableHead className="w-40">Type</TableHead>
                <TableHead>Model</TableHead>
                <TableHead className="w-8" />
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {order.map((row) => (
                <EditableRow key={row.id} row={row} transformations={order} />
              ))}
            </TableBody>
          </Table>
        </SortableContext>
      </DndContext>
    </main>
  );
}

// Autosave: persist a field ~500ms after the last change. The type select uses
// the immediate variant since a discrete choice has no mid-edit state to debounce.
const DEBOUNCED_AUTOSAVE_MS = 500;

function EditableRow({ row, transformations }: { row: TransformationRow; transformations: TransformationRow[] }) {
  const gateSourceOptions = transformations.filter((t) => t.id !== row.id).map((t) => t.name);
  const fetcher = useFetcher();
  const sortable = useSortable({ id: row.id });
  const style = { transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition };
  const [confirming, setConfirming] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultValues = useMemo(() => toDraft(row), [row]);

  const form = useForm({
    defaultValues,
    onSubmit: ({ value }) => {
      const payload = buildPayload(value);
      if (!payload.ok) {
        setError(payload.error);
        return;
      }
      setError(null);
      fetcher.submit(payload.value as SubmitTarget, {
        method: "PATCH",
        action: `/api/transformations/${row.id}`,
        encType: "application/json",
      });
    },
  });

  useEffect(() => {
    if (fetcher.state === "idle" && fetcherFailed(fetcher.data)) {
      toast.error("Couldn't save — try again");
    }
  }, [fetcher.state, fetcher.data]);

  function remove() {
    fetcher.submit(null, { method: "DELETE", action: `/api/transformations/${row.id}`, encType: "application/json" });
  }

  const debounced = { onChangeDebounceMs: DEBOUNCED_AUTOSAVE_MS, onChange: () => form.handleSubmit() };

  return (
    <>
      <TableRow ref={sortable.setNodeRef} style={style}>
        <TableCell>
          <button
            type="button"
            className="cursor-grab text-muted-foreground"
            {...sortable.attributes}
            {...sortable.listeners}
            aria-label="Drag to reorder"
          >
            <GripVertical className="size-4" />
          </button>
        </TableCell>
        <TableCell>{row.position}</TableCell>
        <TableCell>
          <form.Field name="name" listeners={debounced}>
            {(field) => (
              <Input
                value={field.state.value}
                onChange={(e) => field.handleChange(e.target.value)}
                onBlur={field.handleBlur}
                placeholder="name"
              />
            )}
          </form.Field>
        </TableCell>
        <TableCell>
          <form.Field name="type" listeners={{ onChange: () => form.handleSubmit() }}>
            {(field) => (
              <Select
                value={field.state.value}
                onValueChange={(type) => {
                  if (!type) return;
                  field.handleChange(type as Draft["type"]);
                }}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TRANSFORMATION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </form.Field>
        </TableCell>
        <TableCell>
          <form.Field name="model" listeners={debounced}>
            {(field) => (
              <Input
                value={field.state.value}
                onChange={(e) => field.handleChange(e.target.value)}
                onBlur={field.handleBlur}
                placeholder="openai/gpt-4o"
              />
            )}
          </form.Field>
        </TableCell>
        <TableCell>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse row" : "Expand row"}
          >
            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </Button>
        </TableCell>
        <TableCell>
          {confirming ? (
            <Button variant="destructive" size="sm" onClick={remove} onBlur={() => setConfirming(false)}>Sure?</Button>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}><Trash2 className="size-4" /></Button>
          )}
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={7} className="bg-muted/30">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">Prompt</span>
                <form.Field name="prompt" listeners={debounced}>
                  {(field) => (
                    <PromptEditor
                      value={field.state.value}
                      onChange={field.handleChange}
                      onBlur={field.handleBlur}
                    />
                  )}
                </form.Field>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">Params</span>
                <form.Field name="params" listeners={debounced}>
                  {(field) => <ParamsFields value={field.state.value} onChange={field.handleChange} />}
                </form.Field>
              </div>
              <div className="flex flex-col gap-1 md:col-span-2">
                <span className="text-xs text-muted-foreground">Gate</span>
                <form.Field name="gate" listeners={debounced}>
                  {(field) => (
                    <GateFields value={field.state.value} onChange={field.handleChange} sourceOptions={gateSourceOptions} />
                  )}
                </form.Field>
              </div>
            </div>
            {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function GateFields({
  value,
  onChange,
  sourceOptions,
}: {
  value: GateDraft | null;
  onChange: (next: GateDraft | null) => void;
  sourceOptions: string[];
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={value !== null}
          onChange={(e) =>
            onChange(e.target.checked ? { source: sourceOptions[0] ?? "", field: "", op: GATE_OPS[0], value: "" } : null)
          }
        />
        Add gate
      </label>
      {value && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Select value={value.source} onValueChange={(source) => source && onChange({ ...value, source })}>
            <SelectTrigger><SelectValue placeholder="source" /></SelectTrigger>
            <SelectContent>
              {sourceOptions.map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input value={value.field} onChange={(e) => onChange({ ...value, field: e.target.value })} placeholder="field" />
          <Select value={value.op} onValueChange={(op) => op && onChange({ ...value, op })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {GATE_OPS.map((op) => <SelectItem key={op} value={op}>{op}</SelectItem>)}
            </SelectContent>
          </Select>
          <Input value={value.value} onChange={(e) => onChange({ ...value, value: e.target.value })} placeholder="value" />
        </div>
      )}
    </div>
  );
}

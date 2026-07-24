import { z } from "zod";

export const TRANSFORMATION_TYPES = ["score", "summarize", "classify", "knowledge"] as const;
export type TransformationType = (typeof TRANSFORMATION_TYPES)[number];

// The top-level fields each transform type emits in its output JSON — the values a
// gate can check. Mirrors the ingestion output schemas (LLM*TransformOutput,
// KnowledgeTransformOutput). `summarize` emits markdown, so it has no gateable field.
export const TRANSFORMATION_OUTPUT_FIELDS: Record<TransformationType, readonly string[]> = {
  score: ["score"],
  classify: ["categories"],
  knowledge: ["entities_created", "entities_merged", "relationships_created"],
  summarize: [],
};

export const llmParamsSchema = z
  .object({
    temperature: z.number().min(0).max(2).optional(),
    top_p: z.number().min(0).max(1).optional(),
    top_k: z.number().int().min(0).optional(),
    max_tokens: z.number().int().positive().optional(),
  })
  .passthrough();

export const GATE_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"] as const;
// An outgoing gate: after a step runs, its own output is checked against this
// condition; a closed gate halts the later steps in the chain.
export const gateSchema = z.object({
  field: z.string().min(1),
  op: z.enum(GATE_OPS),
  value: z.union([z.string(), z.number(), z.boolean(), z.array(z.string())]),
});

export const transformationInputSchema = z.object({
  type: z.enum(TRANSFORMATION_TYPES),
  name: z.string().trim().min(1, "Name is required"),
  model: z.string().trim().min(1).nullable().optional(),
  prompt: z.string().trim().min(1, "Prompt is required"),
  params: llmParamsSchema.nullable().optional(),
  gate: gateSchema.nullable().optional(),
});
export type TransformationInput = z.infer<typeof transformationInputSchema>;

export const reorderSchema = z.object({
  ids: z.array(z.string().uuid()).min(1),
});

type ParseParamsResult =
  | { ok: true; value: Record<string, unknown> | null }
  | { ok: false; error: string };

export function parseParams(raw: string): ParseParamsResult {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: null };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: "Params must be a JSON object" };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, error: "Invalid JSON" };
  }
}

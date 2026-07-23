import { z } from "zod";

export const TRANSFORMATION_TYPES = ["score", "summarize", "classify", "knowledge"] as const;
export type TransformationType = (typeof TRANSFORMATION_TYPES)[number];

export const llmParamsSchema = z
  .object({
    temperature: z.number().min(0).max(2).optional(),
    top_p: z.number().min(0).max(1).optional(),
    top_k: z.number().int().min(0).optional(),
    max_tokens: z.number().int().positive().optional(),
  })
  .passthrough();

export const transformationInputSchema = z.object({
  type: z.enum(TRANSFORMATION_TYPES),
  model: z.string().trim().min(1).nullable().optional(),
  prompt: z.string().trim().min(1, "Prompt is required"),
  params: llmParamsSchema.nullable().optional(),
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

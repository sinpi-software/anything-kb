import { describe, expect, it } from "vitest";
import { transformationInputSchema, reorderSchema, parseParams, TRANSFORMATION_TYPES } from "./transformation";

describe("transformationInputSchema", () => {
  const valid = { type: "score", name: "score", model: "openai/gpt-4o", prompt: "Rate it", params: { temperature: 0.2 } };

  it("accepts a valid input", () => {
    expect(transformationInputSchema.safeParse(valid).success).toBe(true);
  });

  it("rejects an unknown type", () => {
    const r = transformationInputSchema.safeParse({ ...valid, type: "nope" });
    expect(r.success).toBe(false);
  });

  it("requires a non-empty prompt", () => {
    const r = transformationInputSchema.safeParse({ ...valid, prompt: "  " });
    expect(r.success).toBe(false);
  });

  it("requires a name", () => {
    const { name, ...rest } = valid;
    const r = transformationInputSchema.safeParse(rest);
    expect(r.success).toBe(false);
  });

  it("allows null model and null params", () => {
    expect(transformationInputSchema.safeParse({ type: "summarize", name: "x", model: null, prompt: "x", params: null }).success).toBe(true);
  });

  it("rejects temperature above range", () => {
    const r = transformationInputSchema.safeParse({ ...valid, params: { temperature: 9 } });
    expect(r.success).toBe(false);
  });

  it("keeps unknown param keys (passthrough)", () => {
    const r = transformationInputSchema.parse({ ...valid, params: { seed: 42 } });
    expect(r.params).toEqual({ seed: 42 });
  });

  it("accepts a valid gate", () => {
    const r = transformationInputSchema.safeParse({
      ...valid,
      gate: { source: "newsworthiness", field: "score", op: "gte", value: 5 },
    });
    expect(r.success).toBe(true);
  });

  it("rejects a bad gate op", () => {
    const r = transformationInputSchema.safeParse({
      ...valid,
      gate: { source: "newsworthiness", field: "score", op: ">=", value: 5 },
    });
    expect(r.success).toBe(false);
  });

  it("allows gate omitted or null", () => {
    expect(transformationInputSchema.safeParse(valid).success).toBe(true);
    expect(transformationInputSchema.safeParse({ ...valid, gate: null }).success).toBe(true);
  });
});

describe("reorderSchema", () => {
  it("accepts a list of uuids", () => {
    expect(reorderSchema.safeParse({ ids: ["550e8400-e29b-41d4-a716-446655440000"] }).success).toBe(true);
  });
  it("rejects an empty list", () => {
    expect(reorderSchema.safeParse({ ids: [] }).success).toBe(false);
  });
});

describe("parseParams", () => {
  it("returns null for empty input", () => {
    expect(parseParams("   ")).toEqual({ ok: true, value: null });
  });
  it("parses a JSON object", () => {
    expect(parseParams('{"top_k":5}')).toEqual({ ok: true, value: { top_k: 5 } });
  });
  it("errors on invalid JSON", () => {
    const r = parseParams("{not json");
    expect(r.ok).toBe(false);
  });
  it("errors on non-object JSON", () => {
    const r = parseParams("42");
    expect(r.ok).toBe(false);
  });
});

it("exposes the transform types", () => {
  expect(TRANSFORMATION_TYPES).toEqual(["score", "summarize", "classify", "knowledge"]);
});

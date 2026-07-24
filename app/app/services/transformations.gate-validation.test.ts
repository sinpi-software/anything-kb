// @vitest-environment node
import { afterAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import { db, closeDb } from "~/db/client.server";
import { orgs, transformations } from "~/db/schema";
import { createTransformation, updateTransformation, TransformationValidationError } from "./transformations.server";
import type { TransformationInput } from "~/schemas/transformation";

const TEST_ORG = "gate-validation-test-org";

async function seedChain(): Promise<{ orgId: string; ids: string[] }> {
  const [org] = await db.insert(orgs).values({ name: TEST_ORG }).returning();
  const rows = await db
    .insert(transformations)
    .values([
      { orgId: org.id, position: 0, type: "summarize", name: "a", prompt: "a" },
      { orgId: org.id, position: 1, type: "score", name: "b", prompt: "b" },
      { orgId: org.id, position: 2, type: "classify", name: "c", prompt: "c" },
    ])
    .returning();
  return { orgId: org.id, ids: rows.map((r) => r.id) };
}

async function cleanup(orgId: string): Promise<void> {
  await db.delete(transformations).where(eq(transformations.orgId, orgId));
  await db.delete(orgs).where(eq(orgs.id, orgId));
}

function input(overrides: Partial<TransformationInput> = {}): TransformationInput {
  return {
    type: "summarize",
    name: "new-one",
    prompt: "a prompt",
    ...overrides,
  };
}

afterAll(async () => {
  await closeDb();
});

describe("gate source validation (integration)", () => {
  it("rejects a create whose gate.source names a nonexistent transform", async () => {
    const { orgId } = await seedChain();
    try {
      const err = await createTransformation(
        orgId,
        input({ gate: { source: "does-not-exist", field: "f", op: "eq", value: "x" } }),
      ).catch((e) => e);
      expect(err).toBeInstanceOf(TransformationValidationError);
      expect(err.field).toBe("gate");
    } finally {
      await cleanup(orgId);
    }
  });

  it("allows a create whose gate.source names an earlier transform", async () => {
    const { orgId } = await seedChain();
    try {
      const row = await createTransformation(
        orgId,
        input({ gate: { source: "a", field: "f", op: "eq", value: "x" } }),
      );
      expect(row.gate).toMatchObject({ source: "a" });
    } finally {
      await cleanup(orgId);
    }
  });

  it("rejects an update whose gate.source names a later transform", async () => {
    const { orgId, ids } = await seedChain();
    try {
      // ids[0] is "a" at position 0; only "a" itself exists earlier (none), so
      // naming "b" (position 1, later than "a" at position 0) must reject.
      const err = await updateTransformation(ids[0], input({ name: "a", gate: { source: "b", field: "f", op: "eq", value: "x" } })).catch(
        (e) => e,
      );
      expect(err).toBeInstanceOf(TransformationValidationError);
      expect(err.field).toBe("gate");
    } finally {
      await cleanup(orgId);
    }
  });

  it("allows an update whose gate.source names an earlier transform", async () => {
    const { orgId, ids } = await seedChain();
    try {
      // ids[2] is "c" at position 2; "a" (position 0) and "b" (position 1) are earlier.
      const row = await updateTransformation(ids[2], input({ name: "c", gate: { source: "b", field: "f", op: "eq", value: "x" } }));
      expect(row?.gate).toMatchObject({ source: "b" });
    } finally {
      await cleanup(orgId);
    }
  });

  it("rejects creating a transform with a name that already exists in the org", async () => {
    const { orgId } = await seedChain();
    try {
      const err = await createTransformation(orgId, input({ name: "a" })).catch((e) => e);
      expect(err).toBeInstanceOf(TransformationValidationError);
      expect(err.field).toBe("name");
    } finally {
      await cleanup(orgId);
    }
  });
});

// @vitest-environment node
import { afterAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import { db, closeDb } from "~/db/client.server";
import { orgs, transformations } from "~/db/schema";
import { createTransformation, TransformationValidationError } from "./transformations.server";
import type { TransformationInput } from "~/schemas/transformation";

const TEST_ORG = "gate-validation-test-org";

async function seedChain(): Promise<{ orgId: string }> {
  const [org] = await db.insert(orgs).values({ name: TEST_ORG }).returning();
  await db
    .insert(transformations)
    .values([{ orgId: org.id, position: 0, type: "summarize", name: "a", prompt: "a" }])
    .returning();
  return { orgId: org.id };
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

describe("transformation service (integration)", () => {
  it("persists an outgoing gate on its own output", async () => {
    const { orgId } = await seedChain();
    try {
      const row = await createTransformation(orgId, input({ name: "b", gate: { field: "score", op: "gte", value: 5 } }));
      expect(row.gate).toMatchObject({ field: "score", op: "gte", value: 5 });
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

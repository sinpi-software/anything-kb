// @vitest-environment node
import { afterAll, describe, expect, it } from "vitest";
import { asc, eq } from "drizzle-orm";
import { db, closeDb } from "~/db/client.server";
import { orgs, transformations } from "~/db/schema";
import { reorderTransformations } from "./transformations.server";

const TEST_ORG = "reorder-test-org";

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

afterAll(async () => {
  await closeDb();
});

describe("reorderTransformations (integration)", () => {
  it("reverses order without tripping the unique constraint", async () => {
    const { orgId, ids } = await seedChain();
    try {
      const reversed = [...ids].reverse();
      await reorderTransformations(orgId, reversed);

      const after = await db
        .select()
        .from(transformations)
        .where(eq(transformations.orgId, orgId))
        .orderBy(asc(transformations.position));

      expect(after.map((r) => r.id)).toEqual(reversed);
      expect(after.map((r) => r.position)).toEqual([0, 1, 2]);
    } finally {
      await cleanup(orgId);
    }
  });
});

import { and, asc, eq, sql } from "drizzle-orm";
import { db } from "~/db/client.server";
import { transformations } from "~/db/schema";
import type { TransformationInput } from "~/schemas/transformation";

export type TransformationRow = typeof transformations.$inferSelect;

export function listTransformations(orgId: string): Promise<TransformationRow[]> {
  return db
    .select()
    .from(transformations)
    .where(eq(transformations.orgId, orgId))
    .orderBy(asc(transformations.position));
}

export async function createTransformation(orgId: string, input: TransformationInput): Promise<TransformationRow> {
  const [{ next }] = await db
    .select({ next: sql<number>`coalesce(max(${transformations.position}) + 1, 0)` })
    .from(transformations)
    .where(eq(transformations.orgId, orgId));

  const [row] = await db
    .insert(transformations)
    .values({
      orgId,
      position: next,
      type: input.type,
      model: input.model ?? null,
      prompt: input.prompt,
      params: (input.params ?? null) as typeof transformations.$inferInsert["params"],
    })
    .returning();
  return row;
}

export async function updateTransformation(id: string, input: TransformationInput): Promise<TransformationRow | null> {
  const [row] = await db
    .update(transformations)
    .set({
      type: input.type,
      model: input.model ?? null,
      prompt: input.prompt,
      params: (input.params ?? null) as typeof transformations.$inferInsert["params"],
      updatedAt: new Date().toISOString(),
    })
    .where(eq(transformations.id, id))
    .returning();
  return row ?? null;
}

export async function deleteTransformation(id: string): Promise<void> {
  await db.delete(transformations).where(eq(transformations.id, id));
}

export async function reorderTransformations(orgId: string, ids: string[]): Promise<void> {
  // The unique(org_id, position) constraint is DEFERRABLE INITIALLY DEFERRED,
  // so we can reassign positions row-by-row inside one transaction; Postgres
  // validates uniqueness once at COMMIT.
  await db.transaction(async (tx) => {
    for (let i = 0; i < ids.length; i++) {
      await tx
        .update(transformations)
        .set({ position: i })
        .where(and(eq(transformations.id, ids[i]), eq(transformations.orgId, orgId)));
    }
  });
}

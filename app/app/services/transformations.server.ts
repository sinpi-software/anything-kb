import { and, asc, eq, lt, sql } from "drizzle-orm";
import { db } from "~/db/client.server";
import { transformations } from "~/db/schema";
import type { TransformationInput } from "~/schemas/transformation";

export type TransformationRow = typeof transformations.$inferSelect;

export class TransformationValidationError extends Error {
  constructor(readonly field: string, message: string) {
    super(message);
    this.name = "TransformationValidationError";
  }
}

async function assertGateSourceEarlier(
  orgId: string,
  position: number,
  gate: TransformationInput["gate"],
): Promise<void> {
  if (!gate) return;
  const earlier = await db
    .select({ name: transformations.name })
    .from(transformations)
    .where(and(eq(transformations.orgId, orgId), lt(transformations.position, position)));
  if (!earlier.some((t) => t.name === gate.source)) {
    throw new TransformationValidationError("gate", `Gate source "${gate.source}" must be an earlier transform`);
  }
}

// Drizzle wraps the underlying postgres.js error in a QueryError, so the
// `code`/`constraint_name` fields postgres.js sets live on `.cause`, not on
// the thrown error itself.
function isUniqueNameViolation(err: unknown): boolean {
  const outer = (err ?? {}) as { code?: string; constraint_name?: string; cause?: unknown };
  const inner = (outer.cause ?? {}) as { code?: string; constraint_name?: string };
  const code = outer.code ?? inner.code;
  const constraintName = outer.constraint_name ?? inner.constraint_name;
  return code === "23505" && Boolean(constraintName?.includes("name"));
}

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

  await assertGateSourceEarlier(orgId, next, input.gate);

  try {
    const [row] = await db
      .insert(transformations)
      .values({
        orgId,
        position: next,
        type: input.type,
        name: input.name,
        model: input.model ?? null,
        prompt: input.prompt,
        params: (input.params ?? null) as typeof transformations.$inferInsert["params"],
        gate: (input.gate ?? null) as typeof transformations.$inferInsert["gate"],
      })
      .returning();
    return row;
  } catch (err) {
    if (isUniqueNameViolation(err)) {
      throw new TransformationValidationError("name", `A transform named "${input.name}" already exists`);
    }
    throw err;
  }
}

export async function updateTransformation(id: string, input: TransformationInput): Promise<TransformationRow | null> {
  const [existing] = await db
    .select({ orgId: transformations.orgId, position: transformations.position })
    .from(transformations)
    .where(eq(transformations.id, id));
  if (!existing) return null;

  await assertGateSourceEarlier(existing.orgId, existing.position, input.gate);

  try {
    const [row] = await db
      .update(transformations)
      .set({
        type: input.type,
        name: input.name,
        model: input.model ?? null,
        prompt: input.prompt,
        params: (input.params ?? null) as typeof transformations.$inferInsert["params"],
        gate: (input.gate ?? null) as typeof transformations.$inferInsert["gate"],
        updatedAt: new Date().toISOString(),
      })
      .where(eq(transformations.id, id))
      .returning();
    return row ?? null;
  } catch (err) {
    if (isUniqueNameViolation(err)) {
      throw new TransformationValidationError("name", `A transform named "${input.name}" already exists`);
    }
    throw err;
  }
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

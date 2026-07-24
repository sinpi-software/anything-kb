import type { Route } from "./+types/api.transformations";
import { reorderSchema, transformationInputSchema } from "~/schemas/transformation";
import {
  createTransformation,
  listTransformations,
  reorderTransformations,
  TransformationValidationError,
} from "~/services/transformations.server";

export async function loader({ request }: Route.LoaderArgs) {
  const orgId = new URL(request.url).searchParams.get("org_id");
  if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
  return Response.json(await listTransformations(orgId));
}

async function readJson(request: Request): Promise<Record<string, unknown> | null> {
  try {
    return (await request.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function action({ request }: Route.ActionArgs) {
  if (request.method === "POST") {
    const body = await readJson(request);
    if (!body) return Response.json({ error: "invalid JSON body" }, { status: 422 });
    const orgId = typeof body.org_id === "string" ? body.org_id : null;
    if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    try {
      return Response.json(await createTransformation(orgId, parsed.data), { status: 201 });
    } catch (err) {
      if (err instanceof TransformationValidationError) {
        return Response.json({ error: err.message, field: err.field }, { status: 422 });
      }
      return Response.json({ error: "request failed" }, { status: 500 });
    }
  }

  if (request.method === "PATCH") {
    const body = await readJson(request);
    if (!body) return Response.json({ error: "invalid JSON body" }, { status: 422 });
    const orgId = typeof body.org_id === "string" ? body.org_id : null;
    if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
    const parsed = reorderSchema.safeParse(body);
    if (!parsed.success) return Response.json({ error: "invalid reorder request" }, { status: 422 });
    try {
      await reorderTransformations(orgId, parsed.data.ids);
      return Response.json({ ok: true });
    } catch {
      return Response.json({ error: "request failed" }, { status: 500 });
    }
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}

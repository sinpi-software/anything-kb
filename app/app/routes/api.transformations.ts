import type { Route } from "./+types/api.transformations";
import { reorderSchema, transformationInputSchema } from "~/schemas/transformation";
import { createTransformation, listTransformations, reorderTransformations } from "~/services/transformations.server";

export async function loader({ request }: Route.LoaderArgs) {
  const orgId = new URL(request.url).searchParams.get("org_id");
  if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
  return Response.json(await listTransformations(orgId));
}

export async function action({ request }: Route.ActionArgs) {
  const body = (await request.json()) as Record<string, unknown>;
  const orgId = typeof body.org_id === "string" ? body.org_id : null;

  if (request.method === "POST") {
    if (!orgId) return Response.json({ error: "org_id is required" }, { status: 400 });
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    return Response.json(await createTransformation(orgId, parsed.data), { status: 201 });
  }

  if (request.method === "PATCH") {
    const parsed = reorderSchema.safeParse(body);
    if (!orgId || !parsed.success) return Response.json({ error: "invalid reorder request" }, { status: 422 });
    await reorderTransformations(orgId, parsed.data.ids);
    return Response.json({ ok: true });
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}

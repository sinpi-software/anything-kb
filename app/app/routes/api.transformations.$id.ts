import type { Route } from "./+types/api.transformations.$id";
import { transformationInputSchema } from "~/schemas/transformation";
import { deleteTransformation, updateTransformation } from "~/services/transformations.server";

export async function action({ request, params }: Route.ActionArgs) {
  const { id } = params;

  if (request.method === "PATCH") {
    const body = (await request.json()) as Record<string, unknown>;
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    const row = await updateTransformation(id, parsed.data);
    if (!row) return Response.json({ error: "not found" }, { status: 404 });
    return Response.json(row);
  }

  if (request.method === "DELETE") {
    await deleteTransformation(id);
    return Response.json({ ok: true });
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}

import type { Route } from "./+types/api.transformations.$id";
import { transformationInputSchema } from "~/schemas/transformation";
import { deleteTransformation, updateTransformation } from "~/services/transformations.server";

export async function action({ request, params }: Route.ActionArgs) {
  const { id } = params;

  if (request.method === "PATCH") {
    let body: Record<string, unknown>;
    try {
      body = (await request.json()) as Record<string, unknown>;
    } catch {
      return Response.json({ error: "invalid JSON body" }, { status: 422 });
    }
    const parsed = transformationInputSchema.safeParse(body);
    if (!parsed.success) return Response.json({ errors: parsed.error.flatten() }, { status: 422 });
    try {
      const row = await updateTransformation(id, parsed.data);
      if (!row) return Response.json({ error: "not found" }, { status: 404 });
      return Response.json(row);
    } catch {
      return Response.json({ error: "request failed" }, { status: 500 });
    }
  }

  if (request.method === "DELETE") {
    try {
      await deleteTransformation(id);
      return Response.json({ ok: true });
    } catch {
      return Response.json({ error: "request failed" }, { status: 500 });
    }
  }

  return Response.json({ error: "method not allowed" }, { status: 405 });
}

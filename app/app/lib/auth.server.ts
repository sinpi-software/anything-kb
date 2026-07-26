// SSR-only helpers: loaders run on the Node server and don't get the
// browser's cookie jar automatically, so we forward the incoming request's
// Cookie header to the in-cluster backend by hand. Never forwards
// Set-Cookie back — mutations are client-side (see lib/api.ts) and set the
// cookie directly from the same-origin browser response.
import type { ApiKey, EntityPage, KbConfig, KnowledgeBase, Me } from "./types";

const EMPTY_CONFIG: KbConfig = {
  interests: "",
  discover_types: true,
  entity_types: [],
  relationship_types: [],
};

const INTERNAL_API_URL =
  process.env.INTERNAL_API_URL ?? "http://ingestion-api.ingestion.svc.cluster.local:80";

function forwardCookie(request: Request): HeadersInit {
  return { cookie: request.headers.get("cookie") ?? "" };
}

export async function getMe(request: Request): Promise<Me | null> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/auth/me`, {
      headers: forwardCookie(request),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    // Backend unreachable — treat like an unauthenticated request rather
    // than crashing the page with a 500.
    return null;
  }
}

export async function getKeys(request: Request, kbId: string): Promise<ApiKey[]> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases/${kbId}/keys`, {
      headers: forwardCookie(request),
    });
    if (res.status === 404) throw new KbNotFound();
    if (!res.ok) return [];
    return await res.json();
  } catch (err) {
    // A 404 means the knowledge base is not the caller's — the loader redirects. Any
    // other failure degrades to an empty list rather than bouncing the user.
    if (err instanceof KbNotFound) throw err;
    return [];
  }
}

export async function getConfig(request: Request, kbId: string): Promise<KbConfig> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases/${kbId}/config`, {
      headers: forwardCookie(request),
    });
    if (res.status === 404) throw new KbNotFound();
    if (!res.ok) return EMPTY_CONFIG;
    return await res.json();
  } catch (err) {
    if (err instanceof KbNotFound) throw err;
    return EMPTY_CONFIG;
  }
}

/**
 * A scoped endpoint answered 404: the knowledge base does not exist, is not the
 * caller's, or their role is too low. The API deliberately does not distinguish those
 * — a 403 would confirm the knowledge base exists to someone who may not see it.
 * Loaders catch this and redirect to /app.
 *
 * Every other failure keeps returning an empty default instead, because an unreachable
 * backend should degrade the page rather than bounce the user somewhere confusing.
 */
export class KbNotFound extends Error {}

export async function listKnowledgeBases(request: Request): Promise<KnowledgeBase[]> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/knowledge-bases`, {
      headers: forwardCookie(request),
    });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function getEntity(request: Request, id: string): Promise<EntityPage | null> {
  const query =
    "query($id: ID!) { node(id: $id) { id name type summary article " +
    "edges { type target { id name type } } related { id name type } references { label date } } }";
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/graphql`, {
      method: "POST",
      headers: { ...forwardCookie(request), "content-type": "application/json" },
      body: JSON.stringify({ query, variables: { id } }),
    });
    if (!res.ok) return null;
    const body = await res.json();
    return body?.data?.node ?? null;
  } catch {
    return null;
  }
}

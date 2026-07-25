// SSR-only helpers: loaders run on the Node server and don't get the
// browser's cookie jar automatically, so we forward the incoming request's
// Cookie header to the in-cluster backend by hand. Never forwards
// Set-Cookie back — mutations are client-side (see lib/api.ts) and set the
// cookie directly from the same-origin browser response.
import type { ApiKey, KbConfig, Me } from "./types";

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

export async function getKeys(request: Request): Promise<ApiKey[]> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/keys`, {
      headers: forwardCookie(request),
    });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function getConfig(request: Request): Promise<KbConfig> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/config`, {
      headers: forwardCookie(request),
    });
    if (!res.ok) return EMPTY_CONFIG;
    return await res.json();
  } catch {
    return EMPTY_CONFIG;
  }
}

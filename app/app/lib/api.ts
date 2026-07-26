// Client-side auth + API-key mutations. Same-origin `fetch` so the browser
// carries the `session` cookie and sends `Origin` automatically — the
// backend relies on both for CSRF protection.
import type { ApiKey, CreatedApiKey, KbConfig, KnowledgeBase, Me } from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "content-type": "application/json" },
    ...init,
  });

  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.detail ?? res.statusText);
  }
  return body as T;
}

export interface RegisterInput {
  email: string;
  password: string;
  name?: string;
  knowledge_base_name?: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export const register = (input: RegisterInput): Promise<Me> =>
  apiFetch<Me>("/auth/register", { method: "POST", body: JSON.stringify(input) });

export const login = (input: LoginInput): Promise<Me> =>
  apiFetch<Me>("/auth/login", { method: "POST", body: JSON.stringify(input) });

export const logout = (): Promise<void> =>
  apiFetch<void>("/auth/logout", { method: "POST" });

export const verifyEmail = (token: string): Promise<Me> =>
  apiFetch<Me>("/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) });

export const resendVerification = (): Promise<void> =>
  apiFetch<void>("/auth/resend-verification", { method: "POST" });

export const forgotPassword = (email: string): Promise<{ ok: boolean }> =>
  apiFetch<{ ok: boolean }>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });

export const resetPassword = (token: string, password: string): Promise<Me> =>
  apiFetch<Me>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });

export const listKeys = (kbId: string): Promise<ApiKey[]> =>
  apiFetch<ApiKey[]>(`/knowledge-bases/${kbId}/keys`);

export const createKey = (kbId: string, name: string): Promise<CreatedApiKey> =>
  apiFetch<CreatedApiKey>(`/knowledge-bases/${kbId}/keys`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const revokeKey = (kbId: string, id: string): Promise<void> =>
  apiFetch<void>(`/knowledge-bases/${kbId}/keys/${id}`, { method: "DELETE" });

export interface JobAccepted {
  job_id: string;
}

export type JobStatusValue = "pending" | "processing" | "done" | "skipped" | "failed";

export interface JobStatus {
  job_id: string;
  status: JobStatusValue;
  relevance_reason: string | null;
  error: string | null;
}

export const ingestContent = (text: string, source?: string): Promise<JobAccepted> =>
  apiFetch<JobAccepted>("/content", {
    method: "POST",
    body: JSON.stringify({ text, metadata: source ? { source } : {} }),
  });

export const getJob = (jobId: string): Promise<JobStatus> => apiFetch<JobStatus>(`/content/${jobId}`);

export const updateConfig = (kbId: string, config: KbConfig): Promise<KbConfig> =>
  apiFetch<KbConfig>(`/knowledge-bases/${kbId}/config`, {
    method: "PUT",
    body: JSON.stringify(config),
  });

export const createKnowledgeBase = (name: string, charter?: string): Promise<KnowledgeBase> =>
  apiFetch<KnowledgeBase>("/knowledge-bases", {
    method: "POST",
    body: JSON.stringify({ name, charter: charter?.trim() || null }),
  });

export const renameKnowledgeBase = (id: string, name: string): Promise<KnowledgeBase> =>
  apiFetch<KnowledgeBase>(`/knowledge-bases/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });

// The API requires confirm_name to equal the current name exactly — the delete is
// permanent and takes the knowledge base's graph with it.
export const deleteKnowledgeBase = (id: string, confirmName: string): Promise<void> =>
  apiFetch<void>(`/knowledge-bases/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ confirm_name: confirmName }),
  });

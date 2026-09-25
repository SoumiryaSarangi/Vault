// Service URLs and small typed fetch helpers. Owner: Anushka.
// Defaults match vault.yaml; override with NEXT_PUBLIC_* in web/.env.local.
export const META_URL = process.env.NEXT_PUBLIC_META_URL ?? "http://127.0.0.1:7000";
export const GATEWAY_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:7080";
export const SUPERVISOR_URL = process.env.NEXT_PUBLIC_SUPERVISOR_URL ?? "http://127.0.0.1:7070";
export const ORACLE_URL = process.env.NEXT_PUBLIC_ORACLE_URL ?? "http://127.0.0.1:7090";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  // Content-Type only with a body: a plain GET then needs no CORS preflight (we poll every second).
  const headers = init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers;
  const r = await fetch(url, { ...init, headers });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new ApiError(r.status, body.error ?? "http_error", body.message ?? r.statusText);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const getJson = <T>(url: string) => call<T>(url);
export const postJson = <T>(url: string, body?: unknown) =>
  call<T>(url, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patchJson = <T>(url: string, body: unknown) => call<T>(url, { method: "PATCH", body: JSON.stringify(body) });

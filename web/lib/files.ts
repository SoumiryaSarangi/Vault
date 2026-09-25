"use client";
// Files page data (DESIGN §5.4, §8.1). Owner: Anushka.
// Reads: buckets + file lists + per-file effective copies from metadata (one /v1/inspect call instead of one
// health call per file); the full piece layout of ONE file on demand for the inspect drawer.
// Writes (upload, delete) and downloads go through the public gateway API only (ARCHITECTURE §7.1).
// While the backend isn't there and the console shows sample data, reads fall back to web/fixtures/files.json.
import filesFx from "@/fixtures/files.json";
import { ApiError, GATEWAY_URL, META_URL, SUPERVISOR_URL, getJson, postJson } from "./api";
import type { BucketList, DeleteResult, InspectObject, ObjectHealth, ObjectList, PutResult, SeedResult } from "./contracts";
import { useVault } from "./store";

type FilesFixture = {
  buckets: BucketList;
  lists: Record<string, ObjectList>;
  inspect: { objects: InspectObject[] };
  health: Record<string, ObjectHealth>;
};
const fx = filesFx as unknown as FilesFixture;
const sample = () => useVault.getState().source !== "live";

async function withSample<T>(live: () => Promise<T>, fallback: () => T): Promise<{ data: T; sample: boolean }> {
  try {
    return { data: await live(), sample: false };
  } catch (e) {
    if (sample()) return { data: fallback(), sample: true };
    throw e;
  }
}

const enc = (key: string) => key.split("/").map(encodeURIComponent).join("/");

export const files = {
  buckets: () => withSample(() => getJson<BucketList>(`${META_URL}/v1/buckets`), () => fx.buckets),

  list: (bucket: string) =>
    withSample(
      () => getJson<ObjectList>(`${GATEWAY_URL}/${encodeURIComponent(bucket)}`).catch(() => getJson<ObjectList>(`${META_URL}/v1/objects/${encodeURIComponent(bucket)}`)),
      () => fx.lists[bucket] ?? { bucket, objects: [] },
    ),

  /** bucket/key → effective copies etc. for every live file, in one call (paged by cursor). */
  inspect: () =>
    withSample(
      async () => {
        const out: InspectObject[] = [];
        let cursor: string | null = "";
        for (let page = 0; page < 20 && cursor !== null; page++) {
          const q: string = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
          const r: { objects: InspectObject[]; next_cursor: string | null } = await getJson(`${META_URL}/v1/inspect/objects?limit=500${q}`);
          out.push(...r.objects);
          cursor = r.next_cursor;
        }
        return out;
      },
      () => fx.inspect.objects,
    ),

  health: (bucket: string, key: string) =>
    withSample(
      () => getJson<ObjectHealth>(`${META_URL}/v1/objects/${encodeURIComponent(bucket)}/${enc(key)}/health`),
      () => fx.health[`${bucket}/${key}`] ?? { ifl: 0, target: 3, chunks: [], shared: [], min_cut: [] },
    ),

  downloadUrl: (bucket: string, key: string) => `${GATEWAY_URL}/${encodeURIComponent(bucket)}/${enc(key)}`,

  remove: (bucket: string, key: string) =>
    fetch(`${GATEWAY_URL}/${encodeURIComponent(bucket)}/${enc(key)}`, { method: "DELETE" }).then(async (r) => {
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new ApiError(r.status, b.error ?? "http_error", b.message ?? r.statusText);
      }
      return (await r.json()) as DeleteResult;
    }),

  seed: (bucket: string, count = 200) => postJson<SeedResult>(`${SUPERVISOR_URL}/demo/seed`, { bucket, count }),

  /** Streaming PUT through the gateway with byte progress. Rejects with an ApiError carrying the §6.3 message. */
  upload: (bucket: string, file: File, onProgress: (sent: number) => void) =>
    new Promise<PutResult>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `${GATEWAY_URL}/${encodeURIComponent(bucket)}/${enc(file.name)}`);
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        let body: Record<string, unknown> = {};
        try {
          body = JSON.parse(xhr.responseText || "{}");
        } catch {
          /* not JSON */
        }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(body as unknown as PutResult);
        reject(new ApiError(xhr.status, String(body.error ?? "http_error"), String(body.message ?? `Upload failed (${xhr.status}).`)));
      };
      xhr.onerror = () => reject(new ApiError(0, "unreachable", "Couldn't reach Vault's gateway. Nothing was saved."));
      xhr.send(file);
    }),
};

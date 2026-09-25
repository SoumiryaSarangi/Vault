"use client";
// Files (DESIGN §5.4): buckets · upload · list · inspect pieces and effective copies. Owner: Anushka (A7).
import InspectDrawer from "@/components/files/InspectDrawer";
import { IflChip, PieceDots, ProtectionBadge } from "@/components/files/FileParts";
import UploadDropzone from "@/components/files/UploadDropzone";
import type { Bucket, InspectObject, ObjectListItem } from "@/lib/contracts";
import { fmtBytes, fmtTime, protectionLabel } from "@/lib/format";
import { files } from "@/lib/files";
import { useNow } from "@/lib/hooks";
import { useVault } from "@/lib/store";
import { useCallback, useEffect, useMemo, useState } from "react";

const overhead = (b: Bucket) => (b.policy.type === "erasure" && b.policy.k ? b.policy.n / b.policy.k : b.policy.n);

export default function FilesPage() {
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  const [bucket, setBucket] = useState("clinic");
  const [items, setItems] = useState<ObjectListItem[] | null>(null);
  const [inspect, setInspect] = useState<Record<string, InspectObject>>({});
  const [open, setOpen] = useState<ObjectListItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const toast = useVault((s) => s.toast);
  const source = useVault((s) => s.source);
  const now = useNow(10000);

  const refresh = useCallback(async () => {
    try {
      const [b, l, ins] = await Promise.all([files.buckets(), files.list(bucket), files.inspect()]);
      setBuckets(b.data.buckets);
      setItems(l.data.objects);
      setInspect(Object.fromEntries(ins.data.map((o) => [`${o.bucket}/${o.key}`, o])));
      setError(null);
    } catch {
      setError("Vault isn't running. Start it with python -m vault up.");
    }
  }, [bucket]);

  // Lists on demand + after uploads; effective copies change during chaos, so refresh every 3 s too.
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh, source]);

  const seed = async () => {
    setSeeding(true);
    try {
      const r = await files.seed(bucket);
      toast(`Seeded ${r.uploaded} demo files into ${bucket}.`, "ok");
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't seed demo files.", "error");
    } finally {
      setSeeding(false);
    }
  };

  const sorted = useMemo(() => [...(items ?? [])].sort((a, b) => b.updated_at - a.updated_at), [items]);
  const closeDrawer = useCallback(() => setOpen(null), []);

  return (
    <div className="grid h-full grid-cols-[220px_minmax(0,1fr)] gap-4 px-5 py-2">
      <nav aria-label="Buckets" className="flex min-h-0 flex-col gap-1.5">
        <h2 className="mb-0.5 text-[12px] leading-4 font-medium uppercase tracking-[0.04em] text-fg-2">Buckets</h2>
        {(buckets.length ? buckets : [{ name: bucket, policy: { name: "rep3", type: "replication", n: 3, w: 2 } } as Bucket]).map((b) => (
          <button
            key={b.name}
            type="button"
            onClick={() => (setBucket(b.name), setOpen(null), setItems(null))}
            aria-current={b.name === bucket ? "true" : undefined}
            className={`rounded-[10px] border px-3 py-2 text-left transition-colors ${
              b.name === bucket ? "border-brand bg-bg-2" : "border-line bg-bg-1/70 hover:bg-bg-2"
            }`}
          >
            <p className="text-[15px] font-medium">{b.name}</p>
            <p className="text-[12px] text-fg-1">
              {protectionLabel(b.policy.name)} · {overhead(b).toFixed(1)}×
            </p>
          </button>
        ))}
      </nav>

      <section aria-label={`Files in ${bucket}`} className="relative flex min-h-0 flex-col gap-2">
        <UploadDropzone bucket={bucket} onUploaded={refresh} />
        {error ? (
          <p className="py-8 text-center text-[14px] text-fg-1">{error}</p>
        ) : items === null ? (
          <div className="h-40 animate-pulse rounded-[10px] bg-white/5" />
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <p className="text-[14px] text-fg-1">No files yet. Drop files here, or seed the demo set.</p>
            <button type="button" disabled={seeding} onClick={seed} className="rounded-[10px] bg-brand-strong px-3.5 py-2 text-[14px] font-medium text-bg-0 disabled:opacity-60">
              {seeding ? "Seeding…" : "Seed demo files"}
            </button>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto rounded-[10px] border border-line">
            <table className="w-full table-fixed text-[14px]">
              <colgroup>
                <col />
                <col className="w-24" />
                <col className="w-28" />
                <col className="w-24" />
                <col className="w-24" />
                <col className="w-24" />
              </colgroup>
              <thead className="sticky top-0 bg-bg-1 text-left text-[12px] whitespace-nowrap uppercase tracking-[0.04em] text-fg-2">
                <tr>
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 text-right font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Protection</th>
                  <th className="px-3 py-2 font-medium">Eff. copies</th>
                  <th className="px-3 py-2 font-medium">Pieces</th>
                  <th className="px-3 py-2 text-right font-medium">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {sorted.map((o) => {
                  const ins = inspect[`${bucket}/${o.key}`];
                  const pieceTarget = ins ? (o.policy === "ec42" ? 6 : ins.target) : undefined;
                  return (
                    <tr
                      key={o.key}
                      onClick={() => setOpen(o)}
                      className={`cursor-pointer transition-colors hover:bg-bg-2 ${open?.key === o.key ? "bg-bg-3" : ""}`}
                    >
                      <td className="truncate px-3 py-1.5">
                        <button type="button" onClick={() => setOpen(o)} className="truncate text-left hover:text-brand">
                          {o.key}
                        </button>
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-[13px] tabular-nums text-fg-1">{fmtBytes(o.size)}</td>
                      <td className="px-3 py-1.5">
                        <ProtectionBadge policy={o.policy} />
                      </td>
                      <td className="px-3 py-1.5">
                        <IflChip ifl={ins?.ifl} target={ins?.target} />
                      </td>
                      <td className="px-3 py-1.5">
                        <PieceDots durable={ins?.durable_min} target={pieceTarget} />
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-[13px] tabular-nums text-fg-2">{fmtTime(o.updated_at, now)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {open && (
          <InspectDrawer
            bucket={bucket}
            item={open}
            onClose={closeDrawer}
            onDeleted={() => {
              setOpen(null);
              refresh();
            }}
          />
        )}
      </section>
    </div>
  );
}

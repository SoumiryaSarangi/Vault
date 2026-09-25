"use client";
// Inspect drawer, 480 px (DESIGN §5.4): identity, piece grid (rows = pieces, columns = chunks, cells = machine
// initials coloured by piece state), the minimum failure set, Download / Delete. Owner: Anushka.
import type { ObjectHealth, ObjectListItem } from "@/lib/contracts";
import { domainName, fmtBytes, fmtTime, protectionLabel } from "@/lib/format";
import { files } from "@/lib/files";
import { useNames, useNow } from "@/lib/hooks";
import { useVault } from "@/lib/store";
import { Download, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { FRAG_COLOR, FRAG_WORD, IflChip } from "./FileParts";

const MAX_CHUNKS = 8;

function initials(name: string) {
  const w = name.replace(/['’]/g, "").split(/\s+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : name.slice(0, 2)).toUpperCase();
}

function FragmentGrid({ health }: { health: ObjectHealth }) {
  const names = useNames();
  const chunks = health.chunks.slice(0, MAX_CHUNKS);
  const rows = Math.max(0, ...health.chunks.flatMap((c) => c.fragments.map((f) => f.frag_idx + 1)));
  if (!chunks.length) return <p className="text-[13px] text-fg-2">No pieces recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="border-separate border-spacing-1 text-[12px]">
        <thead>
          <tr>
            <th className="pr-1 text-left font-medium text-fg-2">Piece</th>
            {chunks.map((c) => (
              <th key={c.idx} className="w-9 text-center font-mono font-normal text-fg-2">
                {c.idx}
              </th>
            ))}
            {health.chunks.length > MAX_CHUNKS && <th className="text-fg-2">+{health.chunks.length - MAX_CHUNKS}</th>}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              <td className="pr-1 font-mono text-fg-2">{r}</td>
              {chunks.map((c) => {
                const holders = c.fragments.filter((f) => f.frag_idx === r);
                return (
                  <td key={c.idx} className="h-7 w-9 rounded-md bg-bg-3 text-center align-middle">
                    <div className="flex flex-col items-center gap-px">
                      {holders.length === 0 ? (
                        <span className="text-dead" title="No copy">—</span>
                      ) : (
                        holders.map((f) => (
                          <span
                            key={f.node_id}
                            className="font-mono font-semibold"
                            style={{ color: FRAG_COLOR[f.state] }}
                            title={`${names(f.node_id)}: ${FRAG_WORD[f.state]}${f.route !== "direct" ? ` (via ${names(f.route.replace("relay:", ""))})` : ""}`}
                          >
                            {initials(names(f.node_id))}
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1.5 flex flex-wrap gap-x-3 text-[12px] text-fg-2">
        {(["ok", "incoming", "corrupt", "lost"] as const).map((s) => (
          <span key={s} className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full" style={{ background: FRAG_COLOR[s] }} />
            {FRAG_WORD[s]}
          </span>
        ))}
      </p>
    </div>
  );
}

export default function InspectDrawer({
  bucket,
  item,
  onClose,
  onDeleted,
}: {
  bucket: string;
  item: ObjectListItem;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [health, setHealth] = useState<ObjectHealth | null>(null);
  const [confirm, setConfirm] = useState(false);
  const names = useNames();
  const toast = useVault((s) => s.toast);
  const now = useNow(5000);

  useEffect(() => {
    let stop = false;
    const load = () =>
      files
        .health(bucket, item.key)
        .then((r) => !stop && setHealth(r.data))
        .catch(() => !stop && setHealth(null));
    load();
    const t = setInterval(load, 2000);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      stop = true;
      clearInterval(t);
      window.removeEventListener("keydown", onKey);
    };
  }, [bucket, item.key, onClose]);

  const remove = async () => {
    try {
      await files.remove(bucket, item.key);
      toast(`Deleted ${item.key}.`, "ok");
      onDeleted();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't delete the file.", "error");
    }
  };

  const cut = health?.min_cut ?? [];
  return (
    <aside role="dialog" aria-label={`Inspect ${item.key}`} className="absolute top-0 right-0 bottom-0 z-20 flex w-[480px] flex-col overflow-hidden rounded-2xl border border-line-strong bg-bg-1 shadow-[0_12px_48px_rgb(0_0_0/0.6)]">
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[16px] font-medium">{item.key}</h2>
          <p className="font-mono text-[12px] text-fg-2">
            {fmtBytes(item.size)} · {protectionLabel(item.policy)} · v{item.seq} · commit #{item.commit_seq} · {fmtTime(item.updated_at, now)}
          </p>
          <p className="font-mono text-[12px] text-fg-2" title={item.etag}>
            ETag {item.etag.slice(0, 12)}…
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 text-fg-2 hover:bg-bg-3 hover:text-fg-0">
          <X size={16} />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Where the pieces are</h3>
            <span className="text-[13px] text-fg-1">
              Effective copies <IflChip ifl={health?.ifl} target={health?.target} />
            </span>
          </div>
          {health ? <FragmentGrid health={health} /> : <div className="h-24 animate-pulse rounded-md bg-white/5" />}
        </section>

        <section>
          <h3 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">To lose this file, these must all fail:</h3>
          {cut.length ? (
            <div className="flex flex-wrap gap-1.5">
              {cut.map((d) => (
                <span key={`${d.key}=${d.value}`} className="rounded-full border border-line-strong bg-bg-2 px-2.5 py-0.5 text-[13px]">
                  {d.key === "node" ? names(d.value) : domainName(d.key, d.value)}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-fg-2">{health ? "Nothing recorded yet." : "Checking…"}</p>
          )}
          {health && health.shared.length > 0 && (
            <p className="mt-2 text-[13px] text-suspect">
              All copies share {health.shared.map((d) => domainName(d.key, d.value)).join(", ")}.
            </p>
          )}
        </section>
      </div>

      <footer className="flex items-center gap-2 border-t border-line px-5 py-3">
        <a
          href={files.downloadUrl(bucket, item.key)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-[10px] bg-brand-strong px-3.5 py-2 text-[14px] font-medium text-bg-0"
        >
          <Download size={15} /> Download
        </a>
        {confirm ? (
          <span className="ml-auto flex items-center gap-2 text-[13px]">
            Delete {item.key}?
            <button type="button" onClick={remove} className="rounded-md bg-dead px-2.5 py-1 font-medium text-bg-0">
              Delete
            </button>
            <button type="button" onClick={() => setConfirm(false)} className="rounded-md px-2.5 py-1 text-fg-1 hover:bg-bg-3">
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirm(true)}
            className="ml-auto inline-flex items-center gap-2 rounded-[10px] border border-line px-3.5 py-2 text-[14px] text-fg-1 hover:border-line-strong"
          >
            <Trash2 size={15} /> Delete
          </button>
        )}
      </footer>
    </aside>
  );
}

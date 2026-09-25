"use client";
// Drag-and-drop or pick files; one progress row per file, then the PutResult ("Saved on 3 machines") or the
// §6.3 error copy (DESIGN §5.4, §9). Owner: Anushka.
import type { PutResult } from "@/lib/contracts";
import { fmtBytes } from "@/lib/format";
import { files } from "@/lib/files";
import { CircleCheck, TriangleAlert, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

type Row = { id: number; name: string; size: number; sent: number; result?: PutResult; error?: string };
let seq = 0;

function describeError(code: string, message: string, status?: number): string {
  if (code === "no_bucket") return "That bucket doesn't exist. Nothing was saved.";
  if (status === 404 || status === 405) return "Vault's gateway isn't accepting uploads yet. Nothing was saved.";
  if (code === "quorum_not_met") return message || "Not enough machines confirmed it. Nothing was saved; try again.";
  if (code === "not_enough_machines") return "Not enough machines are on to store it safely. Nothing was saved.";
  if (code === "metadata_unavailable") return "Vault's index is restarting. Nothing was saved; try again in a moment.";
  return message;
}

export default function UploadDropzone({ bucket, onUploaded }: { bucket: string; onUploaded: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const patch = (id: number, p: Partial<Row>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p } : r)));

  const start = (list: FileList | null) => {
    if (!list) return;
    for (const file of Array.from(list)) {
      const id = ++seq;
      setRows((rs) => [{ id, name: file.name, size: file.size, sent: 0 }, ...rs].slice(0, 6));
      files
        .upload(bucket, file, (sent) => patch(id, { sent }))
        .then((result) => {
          patch(id, { result, sent: file.size });
          onUploaded();
        })
        .catch((e: { code?: string; message?: string; status?: number }) =>
          patch(id, { error: describeError(e.code ?? "", e.message ?? "Upload failed.", e.status) }),
        );
    }
  };

  return (
    <div className="space-y-1.5">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          start(e.dataTransfer.files);
        }}
        className={`flex h-12 w-full items-center justify-center gap-2 rounded-[10px] border border-dashed text-[14px] transition-colors ${
          over ? "border-brand bg-bg-3 text-fg-0" : "border-line-strong text-fg-1 hover:border-brand"
        }`}
      >
        <Upload size={16} aria-hidden /> Drop files to store them in <span className="font-medium text-fg-0">{bucket}</span>, or click to pick
      </button>
      <input ref={input} type="file" multiple hidden onChange={(e) => (start(e.target.files), (e.target.value = ""))} />
      {rows.map((r) => {
        const pct = r.size ? Math.round((r.sent / r.size) * 100) : 100;
        return (
          <div key={r.id} className="flex items-center gap-3 rounded-md bg-bg-1/70 px-3 py-1.5 text-[13px]">
            {r.error ? (
              <TriangleAlert size={14} className="shrink-0 text-dead" />
            ) : r.result ? (
              <CircleCheck size={14} className="shrink-0 text-ok" />
            ) : (
              <span className="w-3.5 shrink-0 font-mono text-[11px] text-fg-2">{pct}%</span>
            )}
            <span className="w-44 shrink-0 truncate">{r.name}</span>
            {r.error ? (
              <span className="min-w-0 flex-1 truncate text-dead" title={r.error}>
                {r.error}
              </span>
            ) : r.result ? (
              <span className="min-w-0 flex-1 truncate text-fg-1">
                Saved on {r.result.stored.fragments_acked} machines · {fmtBytes(r.size)}
                {r.result.stored.relayed ? ` · ${r.result.stored.relayed} via relay` : ""}
              </span>
            ) : (
              <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-bg-3">
                <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
              </div>
            )}
            <button type="button" aria-label="Dismiss" onClick={() => setRows((rs) => rs.filter((x) => x.id !== r.id))} className="text-fg-2 hover:text-fg-0">
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

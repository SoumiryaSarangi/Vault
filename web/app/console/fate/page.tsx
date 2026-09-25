"use client";
// Fate (DESIGN §5.5): what machines share, relabel (move a plug), domain cards, effective-copies histogram,
// at-risk files, advice, cluster-wide risk. Domains are derived from the live snapshot so the matrix always works;
// the auditor's FateReport (/v1/fate, J9) adds histogram, at-risk files and advice when present. Owner: Anushka (A8).
import { patchJson, META_URL } from "@/lib/api";
import type { NodeView } from "@/lib/contracts";
import { domainName } from "@/lib/format";
import { C, iflColor, islandColor } from "@/lib/states";
import { useVault } from "@/lib/store";
import { chaos } from "@/lib/chaos";
import { Zap } from "lucide-react";

const KEYS = [
  { key: "power", label: "Power", values: ["A", "B", "C", "D"] },
  { key: "switch", label: "Switch", values: ["S1", "S2", "S3", "S4"] },
  { key: "disk_batch", label: "Disk", values: ["D1", "D2", "D3", "D4"] },
];

function LabelSelect({ node, k, values }: { node: NodeView; k: string; values: string[] }) {
  const toast = useVault((s) => s.toast);
  const v = node.labels[k] ?? "";
  const opts = values.includes(v) || !v ? values : [v, ...values];
  return (
    <select
      value={v}
      aria-label={`${node.display_name} ${k}`}
      onChange={async (e) => {
        const next = { ...node.labels, [k]: e.target.value };
        try {
          await patchJson(`${META_URL}/v1/nodes/${node.id}/labels`, { labels: next });
          toast(`Moved ${node.display_name} to ${domainName(k, e.target.value)}. Checking your files…`, "ok");
        } catch (err) {
          toast(err instanceof Error ? err.message : "Couldn't change the label.", "error");
        }
      }}
      className="w-full rounded-md border border-line bg-bg-3 px-2 py-1 text-[14px] outline-none focus:border-brand"
      style={k === "power" ? { boxShadow: `inset 3px 0 0 ${islandColor(v)}` } : undefined}
    >
      {opts.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

export default function FatePage() {
  const nodes = useVault((s) => s.snapshot?.nodes);
  const fate = useVault((s) => s.fate);
  const faults = useVault((s) => s.faults);
  if (!nodes) return <div className="m-5 h-40 animate-pulse rounded-2xl bg-white/5" />;

  const live = nodes.filter((n) => n.state !== "RETIRED");
  const strips = [...new Set(live.map((n) => n.labels.power).filter(Boolean))].sort() as string[];
  const atRisk = fate?.files.at_risk ?? [];
  const hist = fate?.files.histogram ?? {};
  const histMax = Math.max(1, ...Object.values(hist));
  const version = live[0]?.labels.version;
  const clusterWide =
    fate?.cluster_wide ??
    (version && live.every((n) => n.labels.version === version)
      ? [{ key: "version", value: version, human: `All machines run the same software version (${version}). One bug could affect all of them.` }]
      : []);
  const cut = new Set(faults.filter((f) => f.kind === "power_cut").map((f) => f.subject));

  return (
    <div className="grid h-full grid-cols-[minmax(0,1fr)_360px] gap-4 overflow-hidden px-5 py-2">
      <section aria-label="What your machines share" className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        <h2 className="text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">What your machines share</h2>
        <table className="w-full text-[14px]">
          <thead className="text-left text-[12px] uppercase tracking-[0.04em] text-fg-2">
            <tr>
              <th className="py-1 pr-3 font-medium">Machine</th>
              {KEYS.map((k) => (
                <th key={k.key} className="w-28 px-1 py-1 font-medium">
                  {k.label}
                </th>
              ))}
              <th className="w-24 px-1 py-1 font-medium">Software</th>
            </tr>
          </thead>
          <tbody>
            {live.map((n) => (
              <tr key={n.id}>
                <td className="py-1 pr-3">
                  <span className="font-medium">{n.display_name}</span> <span className="font-mono text-[12px] text-fg-2">{n.id}</span>
                </td>
                {KEYS.map((k) => (
                  <td key={k.key} className="px-1 py-1">
                    <LabelSelect node={n} k={k.key} values={k.values} />
                  </td>
                ))}
                <td className="px-1 py-1 font-mono text-[13px] text-fg-1">{n.labels.version ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="grid grid-cols-3 gap-2">
          {strips.map((s) => {
            const members = live.filter((n) => n.labels.power === s);
            const risky = atRisk.filter((f) => f.shared.some((d) => d.key === "power" && d.value === s)).length;
            const dark = cut.has("all") || cut.has(`power=${s}`);
            return (
              <div
                key={s}
                className="rounded-[10px] border px-3 py-2"
                style={{ borderColor: risky ? C.dead : `color-mix(in srgb, ${islandColor(s)} 60%, transparent)`, background: dark ? "#000" : `color-mix(in srgb, ${islandColor(s)} 10%, transparent)` }}
              >
                <p className="text-[15px] font-medium" style={{ color: islandColor(s) }}>
                  Power Strip {s} {dark && <span className="text-[12px] text-fg-2">· off</span>}
                </p>
                <p className="text-[13px] text-fg-1">
                  {members.length} machine{members.length === 1 ? "" : "s"} · {risky} file{risky === 1 ? "" : "s"} at risk
                </p>
                <p className="truncate text-[12px] text-fg-2">{members.map((m) => m.display_name).join(", ")}</p>
              </div>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-2">
          {strips.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => chaos.powerCut(`power=${s}`, `Power Strip ${s}`, 8)}
              className="inline-flex items-center gap-1.5 rounded-[10px] border border-line px-3 py-1.5 text-[13px] hover:border-line-strong hover:bg-bg-2"
            >
              <Zap size={14} /> Cut Power Strip {s} (8 s)
            </button>
          ))}
        </div>
      </section>

      <aside className="flex min-h-0 flex-col gap-4 overflow-y-auto">
        <section>
          <h2 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Effective copies</h2>
          {Object.keys(hist).length ? (
            ["3", "2", "1"].map((k) => (
              <div key={k} className="mb-1 flex items-center gap-2 text-[14px]">
                <span className="w-3 font-mono" style={{ color: iflColor(Number(k)) }}>
                  {k}
                </span>
                <div className="h-3 flex-1 rounded-sm bg-bg-3">
                  <div className="h-full rounded-sm transition-[width] duration-500" style={{ width: `${((hist[k] ?? 0) / histMax) * 100}%`, background: iflColor(Number(k)) }} />
                </div>
                <span className="w-10 text-right font-mono tabular-nums text-fg-1">{hist[k] ?? 0}</span>
              </div>
            ))
          ) : (
            <p className="text-[13px] text-fg-2">Waiting for the shared-fate audit.</p>
          )}
        </section>
        <section>
          <h2 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Files at risk ({atRisk.length})</h2>
          {atRisk.length ? (
            <ul className="space-y-1 text-[13px]">
              {atRisk.slice(0, 8).map((f) => (
                <li key={`${f.bucket}/${f.key}`} className="flex justify-between gap-2">
                  <span className="truncate">{f.key}</span>
                  <span className="shrink-0 text-dead">{f.shared.map((d) => domainName(d.key, d.value)).join(", ")}</span>
                </li>
              ))}
              {atRisk.length > 8 && <li className="text-fg-2">+{atRisk.length - 8} more</li>}
            </ul>
          ) : (
            <p className="text-[13px] text-fg-2">No file has all its copies on one shared dependency.</p>
          )}
        </section>
        <section>
          <h2 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Advice</h2>
          {fate?.advice.length ? (
            fate.advice.map((a, i) => (
              <p key={i} className="mb-2 rounded-[10px] border border-line bg-bg-1/70 px-3 py-2 text-[14px]">
                {a.human}
              </p>
            ))
          ) : (
            <p className="text-[13px] text-fg-2">Nothing to do. Your copies are as independent as your machines allow.</p>
          )}
        </section>
        <section>
          <h2 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Cluster-wide risk</h2>
          {clusterWide.map((r) => (
            <p key={`${r.key}=${r.value}`} className="text-[14px] text-fg-1">
              {r.human}
            </p>
          ))}
        </section>
      </aside>
    </div>
  );
}

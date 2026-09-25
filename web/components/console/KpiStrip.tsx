"use client";
// KPI strip, 5 tiles, 88 px (DESIGN §5.3). Every number has a sentence beside it. Owner: Anushka.
import { fmtDuration, fmtPct, fmtTime, protectionLabel } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { C, iflColor } from "@/lib/states";
import { useVault } from "@/lib/store";
import type { ReactNode } from "react";

function KpiTile({ label, value, tone, detail, extra }: { label: string; value: ReactNode; tone: string; detail: string; extra?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col justify-center border-r border-line px-4 last:border-r-0">
      <p className="text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">{label}</p>
      <div className="flex items-end gap-2.5">
        <span className="kpi-value font-mono text-[32px] leading-9 font-medium tabular-nums" style={{ color: tone }}>
          {value}
        </span>
        {extra}
      </div>
      <p className="truncate text-[12px] text-fg-1" title={detail}>
        {detail}
      </p>
    </div>
  );
}

export function MttrBar({ detect, grace, repair }: { detect: number; grace: number; repair: number }) {
  const total = detect + grace + repair || 1;
  const parts: [number, string, string][] = [
    [detect, C.suspect, "detect"],
    [grace, C.fg2, "wait"],
    [repair, C.repair, "rebuild"],
  ];
  return (
    <div className="mb-2 flex h-1.5 w-20 overflow-hidden rounded-full bg-bg-3" aria-hidden>
      {parts.map(([v, color, name]) => (
        <div key={name} style={{ width: `${(v / total) * 100}%`, background: color }} />
      ))}
    </div>
  );
}

function IflBars({ histogram }: { histogram: Record<string, number> }) {
  const keys = ["1", "2", "3"];
  const max = Math.max(1, ...keys.map((k) => histogram[k] ?? 0));
  return (
    <div className="mb-2 flex h-5 items-end gap-0.5" aria-hidden>
      {keys.map((k) => (
        <div
          key={k}
          className="w-2 rounded-sm"
          style={{ height: `${Math.max(8, ((histogram[k] ?? 0) / max) * 100)}%`, background: iflColor(Number(k)), opacity: histogram[k] ? 1 : 0.25 }}
        />
      ))}
    </div>
  );
}

export default function KpiStrip() {
  const summary = useVault((s) => s.snapshot?.summary);
  const incident = useVault((s) => s.snapshot?.incident);
  const metrics = useVault((s) => s.metrics);
  const runs = useVault((s) => s.runs);
  const now = useNow(5000);
  const dash = "—";

  // 1. Effective copies
  const files = summary?.files ?? 0;
  const minIfl = files > 0 ? (metrics?.ifl.min ?? summary?.min_ifl) : undefined;   // no files → nothing to score
  const atRisk = metrics?.ifl.at_risk_files ?? summary?.at_risk_files ?? 0;

  // 2. Recovery time
  const last = metrics?.last_incident;
  const open = incident && incident.recovered_at == null;

  // 3. Availability
  const avail = metrics ? metrics.availability_60s * 100 : null;
  const availTone = avail == null ? C.fg1 : avail >= 99.9 ? C.ok : avail >= 99 ? C.suspect : C.dead;

  // 4. Storage overhead
  const overhead = files > 0 ? (metrics?.overhead.cluster ?? summary?.overhead) : undefined;
  const byPolicy = metrics?.overhead.by_policy ?? {};
  const overheadDetail =
    Object.entries(byPolicy)
      .map(([p, x]) => `${protectionLabel(p)} ${x.toFixed(1)}×`)
      .join(" · ") || (files > 0 ? "Measured across all files" : "No files yet");

  // 5. Durability check: latest Vault-mode run
  const run = runs.find((r) => r.mode === "vault" && r.state === "done");
  const violations = run ? Object.values(run.violations).reduce((a, b) => a + b, 0) : null;

  return (
    <footer className="flex h-[88px] border-t border-line" aria-label="Key numbers">
      <KpiTile
        label="Effective copies"
        value={minIfl ?? dash}
        tone={minIfl == null ? C.fg1 : iflColor(minIfl)}
        detail={files > 0 ? `${files} files · ${atRisk} at risk` : "No files yet"}
        extra={metrics ? <IflBars histogram={metrics.ifl.histogram} /> : undefined}
      />
      <KpiTile
        label="Recovery time"
        value={last?.mttr_s != null ? fmtDuration(last.mttr_s) : dash}
        tone={open ? C.repair : "var(--color-fg-0)"}
        detail={
          open
            ? "Rebuilding now…"
            : last?.mttr_s != null
              ? `detect ${(last.detect_s ?? 0).toFixed(1)} · wait ${(last.grace_s ?? 0).toFixed(1)} · rebuild ${(last.repair_s ?? 0).toFixed(1)}`
              : "No failures yet"
        }
        extra={last?.mttr_s != null ? <MttrBar detect={last.detect_s ?? 0} grace={last.grace_s ?? 0} repair={last.repair_s ?? 0} /> : undefined}
      />
      <KpiTile
        label="Availability"
        value={avail == null ? dash : fmtPct(avail)}
        tone={availTone}
        detail={metrics ? `${fmtPct(metrics.readable_now_pct)} of files readable now` : "Measured over the last 60 s"}
      />
      <KpiTile label="Storage overhead" value={overhead != null ? `${overhead.toFixed(1)}×` : dash} tone="var(--color-fg-0)" detail={overheadDetail} />
      <KpiTile
        label="Durability check"
        value={violations ?? dash}
        tone={violations == null ? C.fg1 : violations === 0 ? C.ok : C.dead}
        detail={run ? `${run.counters.faults_injected} faults · Vault mode · ${fmtTime(run.started_at + run.elapsed_s, now)}` : "No checks yet"}
      />
    </footer>
  );
}

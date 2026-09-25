"use client";
// Oracle column component: shows run status, violation counts, counters.
// Owner: Urooz (U6). DESIGN §5.6.
import { RunStatus, RunViolations, RunCounters, Mode } from "@/lib/contracts";

interface Props {
  mode: Mode;
  run: RunStatus | null;
  loading: boolean;
  onStartRun: () => void;
}

const VAULT_COLOR = "text-brand";
const NAIVE_COLOR = "text-naive";

function ViolationCount({ label, value, mode }: { label: string; value: number; mode: Mode }) {
  const isGood = value === 0;
  const color = isGood ? "text-ok" : mode === "vault" ? "text-dead" : "text-dead";
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-line last:border-0">
      <span className="text-fg-1 text-sm font-mono">{label}</span>
      <span className={`text-sm font-semibold tabular-nums ${color}`}>
        {value}
      </span>
    </div>
  );
}

function StateChip({ state }: { state: string }) {
  const map: Record<string, string> = {
    preparing: "bg-suspect/20 text-suspect",
    running: "bg-ok/20 text-ok",
    settling: "bg-repair/20 text-repair",
    verifying: "bg-brand/20 text-brand",
    done: "bg-ok/20 text-ok",
    failed: "bg-dead/20 text-dead",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${map[state] ?? "bg-bg-3 text-fg-2"}`}>
      {state}
    </span>
  );
}

export function OracleColumn({ mode, run, loading, onStartRun }: Props) {
  const accent = mode === "vault" ? VAULT_COLOR : NAIVE_COLOR;
  const modeLabel = mode === "vault" ? "Vault" : "Naive";

  const totalViolations = run
    ? Object.values(run.violations).reduce((a, b) => a + b, 0)
    : null;

  return (
    <div className="flex flex-col gap-4 p-6 rounded-2xl bg-bg-2 border border-line min-h-[520px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className={`text-xl font-bold ${accent}`}>{modeLabel}</h2>
        {run && <StateChip state={run.state} />}
      </div>

      {/* Big violation number */}
      <div className="flex flex-col items-center justify-center py-8 gap-2">
        {run ? (
          <>
            <span
              className={`text-[72px] font-black tabular-nums leading-none transition-all duration-300 ${
                totalViolations === 0 ? "text-ok" : "text-dead"
              }`}
            >
              {totalViolations}
            </span>
            <span className="text-fg-2 text-sm">violations</span>
          </>
        ) : (
          <span className="text-fg-2 text-sm">No run yet</span>
        )}
      </div>

      {/* Violation breakdown */}
      {run && (
        <div className="rounded-xl bg-bg-3 p-4 flex flex-col gap-0">
          <ViolationCount label="lost" value={run.violations.lost} mode={mode} />
          <ViolationCount label="damaged" value={run.violations.damaged} mode={mode} />
          <ViolationCount label="resurrected" value={run.violations.resurrected} mode={mode} />
          <ViolationCount label="stale_read" value={run.violations.stale_read} mode={mode} />
          <ViolationCount label="phantom_read" value={run.violations.phantom_read} mode={mode} />
          <div className="flex items-center justify-between py-1.5 mt-1 pt-2 border-t border-line-strong">
            <span className="text-fg-2 text-xs">under-protected</span>
            <span className={`text-xs font-semibold tabular-nums ${run.health.under_protected > 0 ? "text-suspect" : "text-fg-2"}`}>
              {run.health.under_protected}
            </span>
          </div>
        </div>
      )}

      {/* Counters */}
      {run && (
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: "ops", value: run.counters.ops },
            { label: "faults", value: run.counters.faults_injected },
            { label: "unknown", value: run.counters.unknown },
            { label: "failed", value: run.counters.failed },
          ].map(({ label, value }) => (
            <div key={label} className="bg-bg-3 rounded-lg p-3 flex flex-col gap-0.5">
              <span className="text-[22px] font-bold tabular-nums text-fg-0">{value.toLocaleString()}</span>
              <span className="text-fg-2 text-xs">{label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Start button (only shown when idle) */}
      {(!run || run.state === "done" || run.state === "failed") && (
        <button
          id={`btn-start-${mode}`}
          onClick={onStartRun}
          disabled={loading}
          className={`mt-auto rounded-xl py-3 text-sm font-semibold transition-all duration-200
            ${accent} border border-current hover:bg-white/5 active:scale-95
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          {loading ? "Starting…" : `Run ${modeLabel} Oracle`}
        </button>
      )}
    </div>
  );
}

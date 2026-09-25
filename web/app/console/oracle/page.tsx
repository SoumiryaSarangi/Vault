"use client";
// Durability check (DESIGN §5.6): Vault vs Naive side by side, big violation number, breakdown, counters,
// chaos timeline with violation ticks, "what went wrong" samples, Run again. Owner: Anushka (handed over by Urooz).
import runsFx from "@/fixtures/oracle_runs.json";
import { ORACLE_URL, getJson, postJson } from "@/lib/api";
import type { Mode, RunCreated, RunList, RunStatus, RunSummary } from "@/lib/contracts";
import { fmtTime } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { C } from "@/lib/states";
import { useVault } from "@/lib/store";
import { useEffect, useState } from "react";

const fx = (runsFx as unknown as { runs: RunStatus[] }).runs;
const KINDS: [keyof RunStatus["violations"], string][] = [
  ["lost", "Lost"],
  ["damaged", "Damaged"],
  ["resurrected", "Resurrected"],
  ["stale_read", "Stale reads"],
  ["phantom_read", "Phantom reads"],
];
const total = (r: { violations: RunStatus["violations"] }) => Object.values(r.violations).reduce((a, b) => a + b, 0);

/** Full status of the newest run per mode: live from the Oracle, else the sample runs. */
function useLatest(mode: Mode): { run: RunStatus | null; sample: boolean } {
  const runs = useVault((s) => s.runs);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [sample, setSample] = useState(false);
  const latest: RunSummary | undefined = runs.filter((r) => r.mode === mode).sort((a, b) => b.started_at - a.started_at)[0];
  const id = latest?.run_id;
  const active = latest && !["done", "failed"].includes(latest.state);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      if (!id) return;
      try {
        const r = await getJson<RunStatus>(`${ORACLE_URL}/runs/${id}`);
        if (!stop) (setRun(r), setSample(false));
      } catch {
        const f = fx.find((x) => x.run_id === id) ?? fx.find((x) => x.mode === mode) ?? null;
        if (!stop) (setRun(f), setSample(!!f));
      }
    };
    load();
    const t = setInterval(load, active ? 1000 : 5000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [id, active, mode]);
  return { run, sample };
}

function Column({ mode }: { mode: Mode }) {
  const { run } = useLatest(mode);
  const now = useNow(5000);
  const toast = useVault((s) => s.toast);
  const [confirm, setConfirm] = useState(false);
  const running = run && !["done", "failed"].includes(run.state);
  const v = run ? total(run) : null;

  const start = async () => {
    setConfirm(false);
    try {
      const r = await postJson<RunCreated>(`${ORACLE_URL}/runs`, { mode, seed: 42, duration_s: 60, clients: 8, keyspace: 200, chaos_script: "standard" });
      toast(`Durability check started (${mode} mode). ${r.run_id}`, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't start a check.", "error");
    }
  };

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border bg-bg-1/70 p-4" style={{ borderColor: mode === "naive" ? "var(--color-naive)" : "var(--color-line-strong)" }}>
      <div className="flex items-center justify-between">
        <h2 className="text-[14px] font-semibold tracking-[0.04em] uppercase">
          {mode === "vault" ? "Vault (safety on)" : "Naive (safety off)"}
        </h2>
        <div className="flex items-center gap-2">
          {run && (
            <span className="rounded-full border border-line-strong px-2.5 py-0.5 text-[12px] text-fg-1">
              {running ? `${run.state} · ${Math.round(run.elapsed_s)} s` : run.state === "done" ? `Done · ${fmtTime(run.started_at + run.elapsed_s, now)}` : run.state}
            </span>
          )}
          {confirm ? (
            <span className="flex items-center gap-1.5 text-[12px] text-fg-1" title="A check takes about 2 minutes and resets the demo cluster.">
              Resets the cluster (~2 min).
              <button type="button" onClick={start} className="rounded-md bg-brand-strong px-2 py-0.5 font-medium text-bg-0">
                Run
              </button>
              <button type="button" onClick={() => setConfirm(false)} className="rounded-md px-2 py-0.5 hover:bg-bg-3">
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" disabled={!!running} onClick={() => setConfirm(true)} className="rounded-md border border-line px-2.5 py-0.5 text-[12px] text-fg-1 hover:border-line-strong disabled:opacity-50">
              {running ? "Running…" : "Run again"}
            </button>
          )}
        </div>
      </div>
      {!run ? (
        <p className="py-8 text-center text-[14px] text-fg-2">No checks yet. A check takes about 2 minutes and resets the demo cluster.</p>
      ) : (
        <>
          <div className="my-1 text-center">
            <p className="font-mono text-[72px] leading-[80px] font-medium tabular-nums" style={{ color: v === 0 ? C.ok : C.dead }}>
              {v}
            </p>
            <p className="text-[14px] text-fg-1">violations</p>
          </div>
          {running && (
            <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-bg-3">
              <div className="h-full rounded-full bg-brand" style={{ width: `${Math.min(100, (run.elapsed_s / 60) * 100)}%` }} />
            </div>
          )}
          <p className="text-center text-[14px]">
            {KINDS.map(([k, label], i) => (
              <span key={k} style={{ color: run.violations[k] ? C.dead : undefined }}>
                {i > 0 && " · "}
                {label} {run.violations[k]}
              </span>
            ))}
          </p>
          <p className="mt-1 text-center font-mono text-[13px] text-fg-2">
            {run.counters.ops.toLocaleString()} ops · {run.counters.unknown} unknown · {run.counters.faults_injected} faults · {run.health.under_protected} under-protected
          </p>
        </>
      )}
    </section>
  );
}

function TimelineStrip({ vault, naive }: { vault: RunStatus | null; naive: RunStatus | null }) {
  const base = naive ?? vault;
  if (!base) return null;
  const dur = Math.max(60, ...[vault, naive].flatMap((r) => r?.timeline.map((m) => m.t) ?? []));
  const x = (t: number) => `${(t / dur) * 100}%`;
  const Row = ({ run, label }: { run: RunStatus | null; label: string }) => (
    <div className="flex items-center gap-3">
      <span className="w-14 text-[12px] text-fg-2">{label}</span>
      <div className="relative h-5 flex-1 border-b border-line">
        {run?.timeline
          .filter((m) => m.kind === "violation")
          .map((m, i) => (
            <span key={i} className="absolute bottom-0 h-4 w-0.5 rounded-full bg-dead" style={{ left: x(m.t) }} title={`${m.label} at ${m.t.toFixed(1)} s`} />
          ))}
      </div>
    </div>
  );
  return (
    <section className="rounded-2xl border border-line bg-bg-1/70 px-4 py-3">
      <div className="mb-1 flex items-center gap-3">
        <span className="w-14 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Chaos</span>
        <div className="relative h-6 flex-1">
          {base.timeline
            .filter((m) => m.kind === "chaos")
            .map((m, i) => (
              <span key={i} className="absolute top-0 -translate-x-1/2 text-center" style={{ left: x(m.t) }} title={`${m.label} at ${m.t} s`}>
                <span className="mx-auto block size-2 rounded-full bg-suspect" />
                <span className="block max-w-20 truncate text-[11px] text-fg-2">{m.label}</span>
              </span>
            ))}
        </div>
      </div>
      <Row run={vault} label="Vault" />
      <Row run={naive} label="Naive" />
      <div className="mt-1 flex justify-between pl-[68px] font-mono text-[11px] text-fg-2">
        <span>0 s</span>
        <span>{Math.round(dur)} s</span>
      </div>
    </section>
  );
}

export default function OraclePage() {
  const vault = useLatest("vault");
  const naive = useLatest("naive");
  const explain = useVault((s) => s.ui.explain);
  const samples = naive.run?.samples ?? [];
  return (
    <div className="grid h-full grid-rows-[minmax(0,1fr)_auto_auto] gap-3 overflow-hidden px-5 py-2">
      <div className="grid min-h-0 grid-cols-2 gap-4">
        <Column mode="vault" />
        <Column mode="naive" />
      </div>
      <TimelineStrip vault={vault.run} naive={naive.run} />
      <section className="max-h-28 overflow-y-auto rounded-2xl border border-line bg-bg-1/70 px-4 py-2">
        <h2 className="mb-1 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">
          What went wrong in Naive mode {(vault.sample || naive.sample) && <span className="normal-case tracking-normal">· sample data</span>}
        </h2>
        {samples.length ? (
          samples.map((s, i) => (
            <p key={i} className="text-[14px]">
              {s.human}
              {explain && <span className="ml-2 font-mono text-[12px] text-fg-2">{s.technical}</span>}
            </p>
          ))
        ) : (
          <p className="text-[14px] text-fg-2">Nothing yet. Run a check in Naive mode to compare.</p>
        )}
      </section>
    </div>
  );
}

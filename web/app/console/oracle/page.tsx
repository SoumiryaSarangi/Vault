"use client";
// Durability Oracle page — Vault vs Naive (DESIGN §5.6). Owner: Urooz (U6).
// Starts on fixture data; switches to live API once U5 is running.
import { useEffect, useRef, useState, useCallback } from "react";
import { RunStatus, RunList, RunSummary } from "@/lib/contracts";
import { ORACLE_URL, getJson, postJson } from "@/lib/api";
import { OracleColumn } from "@/components/oracle/urooz_OracleColumn";
import { ChaosTimelineStrip } from "@/components/oracle/urooz_ChaosTimelineStrip";
import { ViolationSamples } from "@/components/oracle/urooz_ViolationSamples";

// Fixture fallback (used when API is not available)
import fixtureData from "@/fixtures/oracle_runs.json";

function useOracleRuns() {
  const [vaultRun, setVaultRun] = useState<RunStatus | null>(null);
  const [naiveRun, setNaiveRun] = useState<RunStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [useFixtures, setUseFixtures] = useState(false);
  const sseRef = useRef<EventSource | null>(null);

  // Load run list (polls every 2s while active runs exist)
  const fetchRuns = useCallback(async () => {
    try {
      const list = await getJson<RunList>(`${ORACLE_URL}/runs`);
      for (const r of list.runs) {
        if (r.state === "done" || r.state === "failed") {
          const full = await getJson<RunStatus>(`${ORACLE_URL}/runs/${r.run_id}`);
          if (r.mode === "vault") setVaultRun(full);
          else setNaiveRun(full);
        }
      }
      setUseFixtures(false);
    } catch {
      // API not available yet — use fixtures
      if (!useFixtures) {
        setUseFixtures(true);
        const runs = (fixtureData as unknown as RunList).runs;
        for (const r of runs) {
          // Treat RunSummary as RunStatus (fixtures have full fields)
          const full = r as unknown as RunStatus;
          if (r.mode === "vault") setVaultRun(full);
          else setNaiveRun(full);
        }
      }
    }
  }, [useFixtures]);

  useEffect(() => {
    fetchRuns();
    const interval = setInterval(fetchRuns, 2000);
    return () => clearInterval(interval);
  }, [fetchRuns]);

  // SSE subscription for active run
  const subscribeToRun = useCallback((runId: string, mode: "vault" | "naive") => {
    if (sseRef.current) sseRef.current.close();
    const es = new EventSource(`${ORACLE_URL}/runs/${runId}/stream`);
    sseRef.current = es;
    es.addEventListener("status", (e) => {
      try {
        const status: RunStatus = JSON.parse((e as MessageEvent).data);
        if (mode === "vault") setVaultRun(status);
        else setNaiveRun(status);
        if (status.state === "done" || status.state === "failed") {
          es.close();
        }
      } catch {}
    });
  }, []);

  const startRun = useCallback(async (mode: "vault" | "naive") => {
    if (useFixtures) return;
    setLoading(true);
    try {
      const res = await postJson<{ run_id: string }>(`${ORACLE_URL}/runs`, {
        mode, seed: 42, duration_s: 60, clients: 8, keyspace: 200, chaos_script: "standard",
      });
      subscribeToRun(res.run_id, mode);
    } catch (e: any) {
      console.error("Start run failed:", e.message);
    } finally {
      setLoading(false);
    }
  }, [useFixtures, subscribeToRun]);

  return { vaultRun, naiveRun, loading, useFixtures, startRun };
}

export default function OraclePage() {
  const { vaultRun, naiveRun, loading, useFixtures, startRun } = useOracleRuns();

  // Choose the latest completed run to show timeline/samples for
  const activeRun = naiveRun ?? vaultRun;
  const combinedTimeline = [
    ...(vaultRun?.timeline ?? []),
    ...(naiveRun?.timeline ?? []),
  ].sort((a, b) => a.t - b.t)
    .filter((v, i, arr) => i === 0 || v.t !== arr[i - 1].t || v.label !== arr[i - 1].label);

  return (
    <main className="flex flex-col gap-6 p-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg-0">Durability Oracle</h1>
          <p className="text-fg-2 text-sm mt-0.5">
            Independent ledger that checks nothing acknowledged was ever lost, damaged, or served stale.
          </p>
        </div>
        {useFixtures && (
          <span className="text-xs text-suspect bg-suspect/10 px-3 py-1 rounded-full border border-suspect/20">
            fixture data — oracle offline
          </span>
        )}
      </div>

      {/* Two-column: Vault | Naive */}
      <div className="grid grid-cols-2 gap-4">
        <OracleColumn
          mode="vault"
          run={vaultRun}
          loading={loading}
          onStartRun={() => startRun("vault")}
        />
        <OracleColumn
          mode="naive"
          run={naiveRun}
          loading={loading}
          onStartRun={() => startRun("naive")}
        />
      </div>

      {/* Chaos timeline strip */}
      {combinedTimeline.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-fg-1 uppercase tracking-widest">Chaos Timeline</h3>
          <ChaosTimelineStrip timeline={combinedTimeline} duration_s={60} />
        </div>
      )}

      {/* Violation samples (from Naive run — that's where violations appear) */}
      {naiveRun && naiveRun.samples.length > 0 && (
        <ViolationSamples samples={naiveRun.samples} />
      )}
      {vaultRun && vaultRun.samples.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-dead uppercase tracking-widest">⚠ Vault violations — these are bugs</h3>
          <ViolationSamples samples={vaultRun.samples} />
        </div>
      )}

      {/* Run again warning */}
      {(vaultRun?.state === "done" || naiveRun?.state === "done") && (
        <div className="rounded-xl bg-suspect/10 border border-suspect/20 px-4 py-3 text-sm text-suspect">
          ⚠ Running again will reset the cluster and wipe all demo data. Use <code className="font-mono">vault reset</code> first if needed.
        </div>
      )}
    </main>
  );
}

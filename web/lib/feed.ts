"use client";
// Everything the console reads, in one place (DESIGN §8.1). Owner: Anushka.
// Live: one SSE connection + pollers. Sample: if Vault's stream isn't up within SAMPLE_AFTER_MS, the store is
// filled from web/fixtures (validated against models.py) and the top bar shows "Sample data".
import eventsFx from "@/fixtures/events.json";
import fateFx from "@/fixtures/fate.json";
import metricsFx from "@/fixtures/metrics.json";
import runsFx from "@/fixtures/oracle_runs.json";
import snapshotFx from "@/fixtures/snapshot.json";
import { META_URL, ORACLE_URL, SUPERVISOR_URL, getJson } from "./api";
import type { EventList, FaultList, FateReport, Metrics, ProcList, RunList, RunSummary, Snapshot, VaultEvent } from "./contracts";
import { connectStream } from "./sse";
import { useVault } from "./store";

const SAMPLE_AFTER_MS = 2000;

function loadSample() {
  const s = useVault.getState();
  if (s.source === "live") return;
  s.setSource("sample");
  s.setSnapshot(snapshotFx as unknown as Snapshot);
  s.setEvents((eventsFx as unknown as EventList).events as VaultEvent[]);
  s.setMetrics(metricsFx as unknown as Metrics);
  s.setFate(fateFx as unknown as FateReport);
  s.setRuns((runsFx as unknown as RunList).runs as RunSummary[]);
}

/** Poll every `ms`; while a service is down, back off to 5 s so the console isn't flooded with errors. */
function every(ms: number, fn: () => Promise<void>): () => void {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    let delay = ms;
    try {
      await fn();
    } catch {
      delay = Math.max(ms, 5000); // the service may not exist yet; sample data or the last value stays on screen
    }
    if (!stopped) setTimeout(tick, delay);
  };
  tick();
  return () => {
    stopped = true;
  };
}

/** Start the feed. Returns a cleanup function (call it on unmount). */
export function startFeed(): () => void {
  const st = useVault.getState;
  const closeStream = connectStream();
  // First live snapshot flips the source to "live" (replacing sample data if it was showing).
  const unsub = useVault.subscribe((s, prev) => {
    if (s.connected && !prev.connected && s.source !== "live") {
      // Never mix sample and live data: the stream re-sends the last 100 events on connect.
      if (s.source === "sample") useVault.setState({ events: [], metrics: null, fate: null, runs: [], phiHistory: {} });
      s.setSource("live");
    }
  });
  const sampleTimer = setTimeout(() => {
    if (!st().connected) loadSample();
  }, SAMPLE_AFTER_MS);

  const stops = [
    every(1000, async () => st().setProcs((await getJson<ProcList>(`${SUPERVISOR_URL}/procs`)).procs)),
    every(1000, async () => st().setFaults((await getJson<FaultList>(`${SUPERVISOR_URL}/chaos`)).faults)),
    every(2000, async () => {
      if (st().source === "live") st().setMetrics(await getJson<Metrics>(`${META_URL}/v1/metrics`));
    }),
    every(2000, async () => {
      if (st().source === "live") st().setFate(await getJson<FateReport>(`${META_URL}/v1/fate`));
    }),
    every(2000, async () => {
      const runs = (await getJson<RunList>(`${ORACLE_URL}/runs`)).runs;
      if (runs.length || st().source === "live") st().setRuns(runs);
    }),
  ];

  return () => {
    clearTimeout(sampleTimer);
    closeStream();
    unsub();
    stops.forEach((s) => s());
  };
}

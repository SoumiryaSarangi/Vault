// One zustand store fed by SSE (DESIGN §8.1). Owner: Anushka. Components read with selectors, never fetch in loops.
import { create } from "zustand";
import type { Fault, FateReport, Metrics, Proc, RunSummary, Snapshot, VaultEvent } from "./contracts";

const EVENT_BUFFER = 200;
const PHI_HISTORY = 60;

type Ui = { selectedNode: string | null; explain: boolean; presenter: boolean; view2d: boolean };

export type VaultStore = {
  connected: boolean;
  snapshot: Snapshot | null;
  events: VaultEvent[];
  metrics: Metrics | null;
  fate: FateReport | null;
  faults: Fault[];
  procs: Proc[];
  runs: RunSummary[];
  phiHistory: Record<string, number[]>;
  ui: Ui;
  setSnapshot: (s: Snapshot) => void;
  pushEvent: (e: VaultEvent) => void;
  setConnected: (c: boolean) => void;
  setMetrics: (m: Metrics) => void;
  setFate: (f: FateReport) => void;
  setFaults: (f: Fault[]) => void;
  setProcs: (p: Proc[]) => void;
  setRuns: (r: RunSummary[]) => void;
  setUi: (patch: Partial<Ui>) => void;
};

export const useVault = create<VaultStore>()((set) => ({
  connected: false,
  snapshot: null,
  events: [],
  metrics: null,
  fate: null,
  faults: [],
  procs: [],
  runs: [],
  phiHistory: {},
  ui: { selectedNode: null, explain: false, presenter: false, view2d: false },

  setSnapshot: (s) =>
    set((st) => {
      const phiHistory = { ...st.phiHistory };
      for (const n of s.nodes) phiHistory[n.id] = [...(phiHistory[n.id] ?? []), n.phi].slice(-PHI_HISTORY);
      return { snapshot: s, phiHistory };
    }),
  pushEvent: (e) =>
    set((st) => (st.events.some((x) => x.id === e.id) ? st : { events: [e, ...st.events].slice(0, EVENT_BUFFER) })),
  setConnected: (connected) => set({ connected }),
  setMetrics: (metrics) => set({ metrics }),
  setFate: (fate) => set({ fate }),
  setFaults: (faults) => set({ faults }),
  setProcs: (procs) => set({ procs }),
  setRuns: (runs) => set({ runs }),
  setUi: (patch) => set((st) => ({ ui: { ...st.ui, ...patch } })),
}));

// One zustand store fed by SSE (DESIGN §8.1). Owner: Anushka. Components read with selectors, never fetch in loops.
import { create } from "zustand";
import type { Fault, FateReport, Metrics, Proc, RunSummary, Snapshot, VaultEvent } from "./contracts";

const EVENT_BUFFER = 200;
const PHI_HISTORY = 60;
const TOAST_MS = 3500;

type Ui = { selectedNode: string | null; explain: boolean; presenter: boolean; view2d: boolean };
export type Toast = { id: number; tone: "info" | "ok" | "error"; text: string };
/** "live": snapshot from Vault's stream. "sample": web/fixtures while the backend isn't streaming. */
export type DataSource = "connecting" | "live" | "sample";

export type VaultStore = {
  connected: boolean;
  source: DataSource;
  snapshot: Snapshot | null;
  events: VaultEvent[];
  metrics: Metrics | null;
  fate: FateReport | null;
  faults: Fault[];
  procs: Proc[];
  runs: RunSummary[];
  phiHistory: Record<string, number[]>;
  toasts: Toast[];
  ui: Ui;
  setSnapshot: (s: Snapshot) => void;
  pushEvent: (e: VaultEvent) => void;
  setEvents: (e: VaultEvent[]) => void;
  setConnected: (c: boolean) => void;
  setSource: (s: DataSource) => void;
  setMetrics: (m: Metrics) => void;
  setFate: (f: FateReport) => void;
  setFaults: (f: Fault[]) => void;
  setProcs: (p: Proc[]) => void;
  setRuns: (r: RunSummary[]) => void;
  setUi: (patch: Partial<Ui>) => void;
  toast: (text: string, tone?: Toast["tone"]) => void;
  dismissToast: (id: number) => void;
};

let toastSeq = 0;

/** repair.progress updates its incident's line in place instead of adding lines (DESIGN §5.3). */
function withEvent(events: VaultEvent[], e: VaultEvent): VaultEvent[] {
  if (events.some((x) => x.id === e.id)) return events;
  const rest =
    e.type === "repair.progress"
      ? events.filter((x) => !(x.type === "repair.progress" && x.data?.incident === e.data?.incident))
      : events;
  return [e, ...rest].slice(0, EVENT_BUFFER);
}

export const useVault = create<VaultStore>()((set, get) => ({
  connected: false,
  source: "connecting",
  snapshot: null,
  events: [],
  metrics: null,
  fate: null,
  faults: [],
  procs: [],
  runs: [],
  phiHistory: {},
  toasts: [],
  ui: { selectedNode: null, explain: false, presenter: false, view2d: true },

  setSnapshot: (s) =>
    set((st) => {
      const phiHistory = { ...st.phiHistory };
      for (const n of s.nodes) phiHistory[n.id] = [...(phiHistory[n.id] ?? []), n.phi].slice(-PHI_HISTORY);
      return { snapshot: s, phiHistory };
    }),
  pushEvent: (e) => set((st) => ({ events: withEvent(st.events, e) })),
  setEvents: (events) => set({ events: [...events].sort((a, b) => b.id - a.id).slice(0, EVENT_BUFFER) }),
  setConnected: (connected) => set({ connected }),
  setSource: (source) => set({ source }),
  setMetrics: (metrics) => set({ metrics }),
  setFate: (fate) => set({ fate }),
  setFaults: (faults) => set({ faults }),
  setProcs: (procs) => set({ procs }),
  setRuns: (runs) => set({ runs }),
  setUi: (patch) => set((st) => ({ ui: { ...st.ui, ...patch } })),
  toast: (text, tone = "info") => {
    const id = ++toastSeq;
    set((st) => ({ toasts: [...st.toasts, { id, tone, text }].slice(-4) }));
    setTimeout(() => get().dismissToast(id), TOAST_MS);
  },
  dismissToast: (id) => set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),
}));

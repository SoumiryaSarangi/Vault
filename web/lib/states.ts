// Visual vocabulary for states (DESIGN §3.1). Owner: Anushka.
// Color never carries meaning alone: every state has a CSS color var, a lucide icon and a word.
import {
  CircleCheck,
  CircleSlash,
  FileWarning,
  Info,
  Loader,
  Lock,
  OctagonX,
  PowerOff,
  RefreshCw,
  Route,
  ScanEye,
  TriangleAlert,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { NodeView, Severity, SummaryLevel } from "./contracts";

export type StateLook = { color: string; icon: LucideIcon; word: string };

export const C = {
  ok: "var(--color-ok)",
  suspect: "var(--color-suspect)",
  partitioned: "var(--color-partitioned)",
  down: "var(--color-down)",
  dead: "var(--color-dead)",
  repair: "var(--color-repair)",
  rejoin: "var(--color-rejoin)",
  fenced: "var(--color-fenced)",
  corrupt: "var(--color-corrupt)",
  brand: "var(--color-brand)",
  fg1: "var(--color-fg-1)",
  fg2: "var(--color-fg-2)",
} as const;

/** How a machine card / 3D node looks. `peerName` resolves "relay:n2" → "Doctor's Desk". */
export function nodeLook(n: NodeView, peerName: (id: string) => string): StateLook {
  if (n.fenced && (n.state === "ALIVE" || n.state === "PARTITIONED"))
    return { color: C.fenced, icon: Lock, word: "Paused for safety" };
  const relay = n.route.startsWith("relay:") ? peerName(n.route.slice(6)) : null;
  switch (n.state) {
    case "ALIVE":
      return relay ? { color: C.partitioned, icon: Route, word: `Via ${relay}` } : { color: C.ok, icon: CircleCheck, word: "Healthy" };
    case "PARTITIONED":
      return { color: C.partitioned, icon: Route, word: relay ? `Via ${relay}` : "Can't reach directly" };
    case "SUSPECT":
      return { color: C.suspect, icon: ScanEye, word: "Checking…" };
    case "DOWN":
      return { color: C.down, icon: PowerOff, word: "Not responding" };
    case "DEAD":
      return { color: C.dead, icon: OctagonX, word: "Off · rebuilding" };
    case "REJOINING":
      return { color: C.rejoin, icon: RefreshCw, word: "Rejoining" };
    case "DRAINING":
      return { color: C.repair, icon: Wrench, word: "Emptying" };
    case "RETIRED":
      return { color: C.fg2, icon: CircleSlash, word: "Retired" };
    case "JOINING":
    default:
      return { color: C.fg1, icon: Loader, word: "Starting" };
  }
}

/** Health sentence dot (DESIGN §6.2 levels). */
export const LEVEL_COLOR: Record<SummaryLevel, string> = {
  ok: C.ok,
  degraded: C.suspect,
  at_risk: C.down,
  critical: C.dead,
};

export const SEVERITY_LOOK: Record<Severity, { color: string; icon: LucideIcon }> = {
  info: { color: C.fg1, icon: Info },
  success: { color: C.ok, icon: CircleCheck },
  warn: { color: C.suspect, icon: TriangleAlert },
  danger: { color: C.dead, icon: OctagonX },
};

export const CORRUPT_LOOK: StateLook = { color: C.corrupt, icon: FileWarning, word: "Damaged copy" };

/** Effective-copies scale (DESIGN §3.1): ≥ target ok, 2 amber, 1 red. */
export function iflColor(ifl: number, target = 3): string {
  if (ifl >= target) return C.ok;
  if (ifl >= 2) return C.suspect;
  return C.dead;
}

/** Power-strip island tints (DESIGN §3.1): A, B, C, D, E. */
const ISLAND = ["var(--color-island-a)", "var(--color-island-b)", "var(--color-island-c)", "var(--color-island-d)", "var(--color-island-e)"];
export function islandColor(value: string | undefined): string {
  if (!value) return C.fg2;
  const i = value.toUpperCase().charCodeAt(0) - 65;
  return ISLAND[((i % ISLAND.length) + ISLAND.length) % ISLAND.length];
}

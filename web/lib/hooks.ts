"use client";
// Small shared hooks. Owner: Anushka.
import { useEffect, useState } from "react";
import { CONTROL_NAMES } from "./format";
import { useVault } from "./store";

/** Current time in seconds, re-rendering every `ms` (relative times like "12s ago"). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** id → display name for machines and control processes ("meta" → "Vault index"). */
export function useNames(): (id: string) => string {
  const nodes = useVault((s) => s.snapshot?.nodes);
  const procs = useVault((s) => s.procs);
  return (id: string) =>
    CONTROL_NAMES[id] ??
    nodes?.find((n) => n.id === id)?.display_name ??
    procs.find((p) => p.pid === id)?.display_name ??
    id;
}

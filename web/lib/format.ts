// Display formatting (DESIGN §6.4). Owner: Anushka.

/** Base 1000 for display: 2.1 MB */
export function fmtBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(n) >= 1000 && i < units.length - 1) {
    n /= 1000;
    i++;
  }
  return i === 0 ? `${n.toFixed(0)} B` : `${n.toFixed(1)} ${units[i]}`;
}

/** 1.9 s, 14 s, 2 min */
export function fmtDuration(s: number): string {
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

/** Percent without decimals unless < 1% or > 99% */
export function fmtPct(x: number): string {
  return x < 1 || x > 99 ? `${x.toFixed(1)}%` : `${Math.round(x)}%`;
}

/** "12s ago" under a minute, clock time (24 h) after */
export function fmtTime(ts: number, now = Date.now() / 1000): string {
  const d = now - ts;
  if (d < 60) return `${Math.max(0, Math.round(d))}s ago`;
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

const DOMAIN_NAMES: Record<string, string> = {
  power: "Power Strip",
  switch: "Network Switch",
  disk_batch: "Disk Batch",
  version: "Software",
};

/** power=A → "Power Strip A" */
export function domainName(key: string, value: string): string {
  return key === "node" ? value : `${DOMAIN_NAMES[key] ?? key} ${value}`;
}

export const CONTROL_NAMES: Record<string, string> = { meta: "Vault index", gw: "Vault gateway" };

/** rep3 → "3 copies", ec42 → "4+2 code" */
export function protectionLabel(policy: string): string {
  return { rep2: "2 copies", rep3: "3 copies", ec42: "4+2 code" }[policy] ?? policy;
}

"use client";
// Small pieces of the Files page (DESIGN §5.4). Owner: Anushka.
import type { FragState } from "@/lib/contracts";
import { protectionLabel } from "@/lib/format";
import { C, iflColor } from "@/lib/states";

const PROTECTION_TIP: Record<string, string> = {
  rep3: "3 full copies on 3 different machines. Any 1 copy is enough to read the file. Uses 3× space.",
  rep2: "2 full copies on 2 different machines. Any 1 copy is enough to read the file. Uses 2× space.",
  ec42: "Split into 4 pieces plus 2 backup pieces. Any 4 rebuild the file. Uses 1.5× space.",
};

export function ProtectionBadge({ policy }: { policy: string }) {
  return (
    <span className="rounded-full border border-line-strong px-2 py-0.5 text-[12px] whitespace-nowrap text-fg-1" title={PROTECTION_TIP[policy]}>
      {protectionLabel(policy)}
    </span>
  );
}

/** Effective copies: dot + number, coloured against the file's own policy target (MASTER_PLAN D1). */
export function IflChip({ ifl, target }: { ifl: number | undefined; target: number | undefined }) {
  if (ifl == null) return <span className="text-fg-2">—</span>;
  const color = iflColor(ifl, target ?? 3);
  return (
    <span className="inline-flex items-center gap-1.5 font-mono tabular-nums" title={`Effective copies ${ifl} of ${target ?? 3}`}>
      <span className="size-2 rounded-full" style={{ background: color }} />
      <span style={{ color }}>{ifl}</span>
    </span>
  );
}

/** Healthy pieces vs target, one dot each. */
export function PieceDots({ durable, target }: { durable: number | undefined; target: number | undefined }) {
  if (durable == null || target == null) return <span className="text-fg-2">—</span>;
  const n = Math.max(durable, target);
  return (
    <span className="inline-flex gap-0.5" title={`${durable} of ${target} pieces healthy (worst chunk)`}>
      {Array.from({ length: n }, (_, i) => (
        <span key={i} className="size-1.5 rounded-[2px]" style={{ background: i < durable ? C.ok : "var(--color-bg-3)", outline: i < durable ? undefined : `1px solid ${C.dead}` }} />
      ))}
    </span>
  );
}

export const FRAG_COLOR: Record<FragState, string> = {
  ok: C.ok,
  incoming: C.repair,
  pending: C.fg2,
  corrupt: C.corrupt,
  missing: C.dead,
  lost: C.dead,
  trim: C.fg2,
};

export const FRAG_WORD: Record<FragState, string> = {
  ok: "healthy",
  incoming: "being rebuilt",
  pending: "being written",
  corrupt: "damaged",
  missing: "missing",
  lost: "lost (machine off)",
  trim: "extra, being removed",
};

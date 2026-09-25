"use client";
// Machine card, 260 × 76 (DESIGN §5.3). Owner: Anushka.
import type { NodeView } from "@/lib/contracts";
import { nodeLook } from "@/lib/states";
import { HardDrive, Lock, Network, Snail, Zap } from "lucide-react";

export function PhiSparkline({ values, color }: { values: number[]; color: string }) {
  const w = 54;
  const h = 16;
  if (values.length < 2) return <svg width={w} height={h} aria-hidden />;
  const max = Math.max(10, ...values);
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - (Math.min(v, max) / max) * h).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} aria-hidden className="shrink-0">
      <line x1={0} x2={w} y1={h - (8 / max) * h} y2={h - (8 / max) * h} stroke="var(--color-line-strong)" strokeDasharray="2 2" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.25} />
    </svg>
  );
}

export function DiskBar({ used, capacity }: { used: number; capacity: number }) {
  const pct = capacity > 0 ? Math.min(100, (used / capacity) * 100) : 0;
  const color = pct > 90 ? "var(--color-dead)" : pct > 75 ? "var(--color-suspect)" : "var(--color-fg-1)";
  return (
    <div className="flex items-center gap-2" title={`Disk ${pct.toFixed(0)}% full`}>
      <div className="h-1 w-24 overflow-hidden rounded-full bg-bg-3">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="font-mono text-[12px] tabular-nums text-fg-2">{pct.toFixed(0)}%</span>
    </div>
  );
}

export function LabelGlyphs({ labels }: { labels: Record<string, string> }) {
  const items: [typeof Zap, string | undefined, string][] = [
    [Zap, labels.power, "Power Strip"],
    [Network, labels.switch, "Network Switch"],
    [HardDrive, labels.disk_batch, "Disk Batch"],
  ];
  return (
    <div className="flex items-center gap-2 text-[12px] text-fg-2">
      {items.map(([Icon, v, name]) =>
        v ? (
          <span key={name} className="inline-flex items-center gap-0.5" title={`${name} ${v}`}>
            <Icon size={12} aria-hidden />
            {v}
          </span>
        ) : null,
      )}
    </div>
  );
}

export default function NodeCard({
  node,
  phi,
  selected,
  onSelect,
  peerName,
}: {
  node: NodeView;
  phi: number[];
  selected: boolean;
  onSelect: () => void;
  peerName: (id: string) => string;
}) {
  const look = nodeLook(node, peerName);
  const Icon = look.icon;
  const dim = node.state === "DEAD" || node.state === "RETIRED";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${node.display_name}: ${look.word}`}
      className={`flex h-[74px] w-full flex-col justify-between rounded-[10px] border bg-bg-1/70 px-3 py-2 text-left transition-colors hover:bg-bg-2 ${
        selected ? "border-brand" : "border-line"
      } ${dim ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-2">
        <Icon size={15} style={{ color: look.color }} aria-hidden className="shrink-0" />
        <span className="min-w-0 flex-1 truncate text-[15px] leading-5 font-medium">{node.display_name}</span>
        {node.slow && <Snail size={13} className="shrink-0 text-suspect" aria-label="Slow" />}
        {node.fenced && <Lock size={13} className="shrink-0 text-fenced" aria-label="Paused for safety" />}
        <span className="max-w-[110px] shrink-0 truncate text-[12px] font-medium" style={{ color: look.color }}>
          {look.word}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[12px] tabular-nums text-fg-1">
          {node.id} · φ {node.phi.toFixed(1)} · {node.fragments} copies
        </span>
        <PhiSparkline values={phi} color={look.color} />
      </div>
      <div className="flex items-center justify-between">
        <DiskBar used={node.disk_used} capacity={node.capacity} />
        <LabelGlyphs labels={node.labels} />
      </div>
    </button>
  );
}

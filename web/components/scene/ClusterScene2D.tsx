"use client";
// 2D cluster view (DESIGN §7 "2D fallback"): the demo view while 3D is a stretch goal (MASTER_PLAN R7).
// Machines on the hash ring, standing on power-strip islands; the Vault index in the centre; cut cables in red,
// relays in violet, repair/move flows as moving dots. Reads only the snapshot it is given (never fetches).
// Owner: Anushka.
import type { Fault, NodeView, Snapshot } from "@/lib/contracts";
import { domainName } from "@/lib/format";
import { C, islandColor, nodeLook } from "@/lib/states";
import { useEffect, useState } from "react";

const W = 640;
const H = 470;
const CX = W / 2;
const CY = H / 2 - 5;
const R = 160;
const NODE_R = 22;
const GW = { x: CX, y: CY + 62 };

type P = { x: number; y: number };

function angleOf(i: number, n: number) {
  return (i / n) * 2 * Math.PI - Math.PI / 2;
}
function at(angle: number, r = R): P {
  return { x: CX + r * Math.cos(angle), y: CY + r * Math.sin(angle) };
}
function arcPath(a0: number, a1: number, r = R): string {
  const p0 = at(a0, r);
  const p1 = at(a1, r);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${p0.x} ${p0.y} A ${r} ${r} 0 ${large} 1 ${p1.x} ${p1.y}`;
}
/** Raised arc from a to b, bent sideways (perpendicular) so flows never run through the index. */
function flowPath(a: P, b: P): string {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const k = 0.28;
  return `M ${a.x} ${a.y} Q ${mx - dy * k} ${my + dx * k} ${b.x} ${b.y}`;
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => setReduced(window.matchMedia("(prefers-reduced-motion: reduce)").matches), []);
  return reduced;
}

export default function ClusterScene2D({
  snapshot,
  faults = [],
  selectedId,
  onSelect,
}: {
  snapshot: Snapshot | null;
  faults?: Fault[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const reduced = useReducedMotion();
  if (!snapshot) return <div className="h-full w-full animate-pulse rounded-2xl bg-white/5" />;

  const nodes = snapshot.nodes;
  const n = nodes.length;
  const pos: Record<string, P> = { meta: { x: CX, y: CY }, gw: GW };
  const ang: Record<string, number> = {};
  nodes.forEach((node, i) => {
    ang[node.id] = angleOf(i, n);
    pos[node.id] = at(ang[node.id]);
  });
  const name = (id: string) => (id === "meta" ? "Vault index" : id === "gw" ? "Vault gateway" : nodes.find((x) => x.id === id)?.display_name ?? id);
  const cutStrips = new Set(faults.filter((f) => f.kind === "power_cut").map((f) => f.subject));

  // Power-strip islands: a thick rounded arc under ring-neighbours that share a strip, a pad under each machine.
  const islands: { key: string; d?: string; p?: P; color: string; dark: boolean }[] = [];
  const labelsAt: Record<string, P[]> = {};
  nodes.forEach((node, i) => {
    const v = node.labels.power;
    if (!v) return;
    const dark = cutStrips.has("all") || cutStrips.has(`power=${v}`);
    islands.push({ key: `pad-${node.id}`, p: pos[node.id], color: islandColor(v), dark });
    (labelsAt[v] ??= []).push(at(ang[node.id], R + 58));
    const next = nodes[(i + 1) % n];
    if (n > 1 && next.labels.power === v && next.id !== node.id) {
      const a0 = ang[node.id];
      let a1 = ang[next.id];
      if (a1 < a0) a1 += 2 * Math.PI;
      islands.push({ key: `arc-${node.id}`, d: arcPath(a0, a1), color: islandColor(v), dark });
    }
  });

  const summary = `${n} machines. ${nodes.filter((x) => x.state === "ALIVE").length} healthy.`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" role="img" aria-label={summary} preserveAspectRatio="xMidYMid meet">
      {/* the consistent-hash ring */}
      <circle cx={CX} cy={CY} r={R} fill="none" stroke="var(--color-line-strong)" strokeWidth={1} />

      {/* power-strip islands */}
      {islands.map((isl) =>
        isl.d ? (
          <path key={isl.key} d={isl.d} fill="none" stroke={isl.dark ? "#000" : isl.color} strokeOpacity={isl.dark ? 0.7 : 0.14} strokeWidth={64} strokeLinecap="round" />
        ) : (
          <circle key={isl.key} cx={isl.p!.x} cy={isl.p!.y} r={32} fill={isl.dark ? "#000" : isl.color} fillOpacity={isl.dark ? 0.7 : 0.14} />
        ),
      )}
      {Object.entries(labelsAt).map(([v, ps]) => {
        const p = { x: ps.reduce((a, q) => a + q.x, 0) / ps.length, y: ps.reduce((a, q) => a + q.y, 0) / ps.length };
        return (
          <text key={v} x={p.x} y={p.y} textAnchor="middle" fontSize={12} fontWeight={500} fill={islandColor(v)} opacity={0.9}>
            {domainName("power", v)}
          </text>
        );
      })}

      {/* abnormal links: cut in red, relay route in violet */}
      {snapshot.links.map((l) => {
        const a = pos[l.a];
        const b = pos[l.b];
        if (!a || !b) return null;
        const r = l.relay ? pos[l.relay] : undefined;
        return (
          <g key={`${l.a}-${l.b}`}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={C.dead} strokeWidth={2} strokeDasharray="6 5" opacity={0.9} />
            {r && (
              <polyline
                points={`${a.x},${a.y} ${r.x},${r.y} ${b.x},${b.y}`}
                fill="none"
                stroke={C.partitioned}
                strokeWidth={2}
                strokeDasharray="4 6"
                className="dash-flow"
              />
            )}
          </g>
        );
      })}

      {/* repair / move flows */}
      {snapshot.repair.active.map((j) => {
        const a = j.src ? pos[j.src] : undefined;
        const b = j.dst ? pos[j.dst] : undefined;
        if (!a || !b) return null;
        const d = flowPath(a, b);
        const color = j.kind === "repair" ? C.repair : C.brand;
        return (
          <g key={j.job_id}>
            <path d={d} fill="none" stroke={color} strokeOpacity={0.35} strokeWidth={1.5} />
            {!reduced &&
              [0, 0.33, 0.66].map((off) => (
                <circle key={off} r={3} fill={color}>
                  <animateMotion dur="1.4s" repeatCount="indefinite" path={d} begin={`${-off * 1.4}s`} />
                </circle>
              ))}
          </g>
        );
      })}

      {/* control plane: index (centre) and gateway */}
      {(["meta", "gw"] as const).map((id) => {
        const ok = snapshot.control.find((c) => c.id === id)?.ok ?? true;
        const p = pos[id];
        return (
          <g key={id} opacity={ok ? 1 : 0.35}>
            {id === "meta" ? (
              <polygon
                points={Array.from({ length: 6 }, (_, k) => {
                  const a = (k / 6) * 2 * Math.PI - Math.PI / 2;
                  return `${p.x + 22 * Math.cos(a)},${p.y + 22 * Math.sin(a)}`;
                }).join(" ")}
                fill="var(--color-bg-2)"
                stroke={C.brand}
                strokeWidth={1.5}
              />
            ) : (
              <rect x={p.x - 13} y={p.y - 9} width={26} height={18} rx={5} fill="var(--color-bg-2)" stroke={C.fg2} />
            )}
            <text x={p.x} y={p.y + (id === "meta" ? 38 : 24)} textAnchor="middle" fontSize={11} fill={C.fg2}>
              {name(id)}
            </text>
          </g>
        );
      })}

      {/* machines */}
      {nodes.map((node: NodeView) => {
        const p = pos[node.id];
        const look = nodeLook(node, name);
        const sink = node.state === "DEAD" ? 6 : 0;
        const pulse = node.state === "SUSPECT" ? Math.min(1.6, Math.max(0.4, 1.6 - node.phi / 10)) : 0;
        return (
          <g
            key={node.id}
            transform={`translate(${p.x} ${p.y + sink})`}
            opacity={node.state === "DEAD" || node.state === "RETIRED" ? 0.6 : 1}
            onClick={() => onSelect?.(node.id)}
            className={onSelect ? "cursor-pointer" : undefined}
          >
            {selectedId === node.id && <circle r={NODE_R + 8} fill="none" stroke={C.brand} strokeWidth={1.5} />}
            <circle
              r={NODE_R}
              fill="var(--color-bg-2)"
              stroke={look.color}
              strokeWidth={2}
              className={pulse ? "suspect-pulse" : node.state === "DOWN" ? "down-blink" : undefined}
              style={pulse ? ({ "--pulse": `${pulse}s` } as React.CSSProperties) : undefined}
            />
            <circle r={NODE_R - 7} fill={look.color} fillOpacity={0.22} />
            <text y={NODE_R + 16} textAnchor="middle" fontSize={12.5} fontWeight={500} fill="var(--color-fg-0)">
              {node.display_name}
            </text>
            <text y={NODE_R + 30} textAnchor="middle" fontSize={11} fill={look.color}>
              {look.word}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

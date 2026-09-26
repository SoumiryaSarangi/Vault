"use client";
// Machines column (DESIGN §5.3). Also the accessible equivalent of the cluster view (DESIGN §10). Owner: Anushka.
import { useNames } from "@/lib/hooks";
import { useVault } from "@/lib/store";
import NodeCard from "./NodeCard";

export default function NodeList() {
  const nodes = useVault((s) => s.snapshot?.nodes);
  const procs = useVault((s) => s.procs);
  const phi = useVault((s) => s.phiHistory);
  const selected = useVault((s) => s.ui.selectedNode);
  const setUi = useVault((s) => s.setUi);
  const names = useNames();

  return (
    <section aria-label="Machines" className="flex min-h-0 flex-col">
      <h2 className="mb-1.5 text-[12px] leading-4 font-medium uppercase tracking-[0.04em] text-fg-2">
        Machines {nodes && <span className="tabular-nums">({nodes.length})</span>}
      </h2>
      <div className="flex min-h-0 flex-col gap-1.5 overflow-y-auto">
        {nodes
          ? nodes.map((n) => (
              <NodeCard
                key={n.id}
                node={n}
                phi={phi[n.id] ?? []}
                selected={selected === n.id}
                onSelect={() => setUi({ selectedNode: selected === n.id ? null : n.id })}
                peerName={names}
                proc={procs.find((p) => p.pid === n.id)}
              />
            ))
          : Array.from({ length: 6 }, (_, i) => <div key={i} className="h-[74px] animate-pulse rounded-[10px] bg-white/5" />)}
      </div>
    </section>
  );
}

"use client";
// Overview (DESIGN §5.3): machines · cluster view · what's happening. Owner: Anushka (A5).
import EventTimeline from "@/components/console/EventTimeline";
import NodeList from "@/components/console/NodeList";
import ClusterScene2D from "@/components/scene/ClusterScene2D";
import { useVault } from "@/lib/store";

export default function OverviewPage() {
  const snapshot = useVault((s) => s.snapshot);
  const faults = useVault((s) => s.faults);
  const selected = useVault((s) => s.ui.selectedNode);
  const setUi = useVault((s) => s.setUi);
  return (
    <div className="grid h-full grid-cols-[260px_minmax(0,1fr)_340px] gap-4 px-5 py-2 min-[1600px]:grid-cols-[260px_minmax(0,1fr)_400px]">
      <NodeList />
      <section aria-label="Cluster view" className="min-h-0">
        <ClusterScene2D
          snapshot={snapshot}
          faults={faults}
          selectedId={selected}
          onSelect={(id) => setUi({ selectedNode: selected === id ? null : id })}
        />
      </section>
      <EventTimeline />
    </div>
  );
}

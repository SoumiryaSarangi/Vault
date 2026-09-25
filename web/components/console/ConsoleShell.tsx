"use client";
// Console shell (DESIGN §5.3, §8.2): top bar, page, active faults, KPI strip, chaos dock; starts the data feed.
// Fits 1280×720 with no page scroll. Owner: Anushka.
import Toasts from "@/components/ui/Toasts";
import { startFeed } from "@/lib/feed";
import { useVault } from "@/lib/store";
import { useEffect, type ReactNode } from "react";
import ChaosDock, { ActiveFaults } from "./ChaosDock";
import KpiStrip from "./KpiStrip";
import TopBar from "./TopBar";

export default function ConsoleShell({ children }: { children: ReactNode }) {
  const mode = useVault((s) => s.snapshot?.mode ?? "vault");
  const presenter = useVault((s) => s.ui.presenter);
  const setUi = useVault((s) => s.setUi);

  useEffect(() => startFeed(), []);

  // Keyboard: E toggles Explain, P toggles presenter mode (DESIGN §5.10). Ignored while typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const ui = useVault.getState().ui;
      if (e.key === "e" || e.key === "E") setUi({ explain: !ui.explain });
      if (e.key === "p" || e.key === "P") setUi({ presenter: !ui.presenter });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setUi]);

  return (
    <div
      className={`console-bg grid h-dvh min-h-[720px] grid-cols-[minmax(0,1fr)] grid-rows-[56px_minmax(0,1fr)_88px_64px] overflow-hidden ${presenter ? "presenter" : ""}`}
      data-mode={mode}
      style={mode === "naive" ? { boxShadow: "inset 0 4px 0 var(--color-naive)" } : undefined}
    >
      <TopBar />
      <main className="relative min-h-0">
        {children}
        {/* floats over the bottom of the page so adding a fault never shifts the layout (DESIGN §4.3) */}
        <ActiveFaults />
      </main>
      <KpiStrip />
      <ChaosDock />
      <Toasts />
    </div>
  );
}

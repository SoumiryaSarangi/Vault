"use client";
// Power-cut and boot overlays (DESIGN §5.7). Owner: Anushka (A10).
// Cut: the supervisor lists a "power:all" fault → dim the console. Restore: show the recovery lines as events
// arrive, then "N of N files present. X lost." computed from file counts before vs after (never hard-coded).
import VaultMarkLazy from "@/components/brand/VaultMarkLazy";
import { chaos } from "@/lib/chaos";
import { useVault } from "@/lib/store";
import { useEffect, useRef, useState } from "react";

type Boot = { before: number; since: number };

export default function Overlays() {
  const cutAll = useVault((s) => s.faults.some((f) => f.kind === "power_cut" && f.subject === "all"));
  const files = useVault((s) => s.snapshot?.summary.files);
  const unreadable = useVault((s) => s.snapshot?.summary.unreadable_files ?? 0);
  const nodes = useVault((s) => s.snapshot?.nodes);
  const events = useVault((s) => s.events);
  const connected = useVault((s) => s.connected);
  const [boot, setBoot] = useState<Boot | null>(null);
  const before = useRef<number | null>(null);
  const wasCut = useRef(false);

  // Remember the file count right before the cut; when the cut ends, open the boot overlay.
  useEffect(() => {
    if (!cutAll && files != null && !wasCut.current) before.current = files;
    if (cutAll && !wasCut.current) wasCut.current = true;
    if (!cutAll && wasCut.current) {
      wasCut.current = false;
      setBoot({ before: before.current ?? 0, since: Date.now() / 1000 - 2 });
    }
  }, [cutAll, files]);

  const bootEvents = boot ? events.filter((e) => e.ts >= boot.since && ["meta.recovered", "node.startup_discarded"].includes(e.type)).reverse() : [];
  const alive = nodes?.filter((n) => n.state === "ALIVE").length ?? 0;
  const total = nodes?.length ?? 0;
  const recovered = boot && connected && bootEvents.some((e) => e.type === "meta.recovered");
  const done = recovered && alive === total && total > 0;

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setBoot(null), 3000);
    return () => clearTimeout(t);
  }, [done]);

  useEffect(() => {
    if (!boot) return;
    const t = setTimeout(() => setBoot(null), 45000); // never trap the presenter
    return () => clearTimeout(t);
  }, [boot]);

  if (cutAll)
    return (
      <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/80" role="alert">
        <div className="text-center">
          <p className="text-[28px] font-semibold">Power cut. Every machine is off.</p>
          <p className="mt-2 text-fg-1">The dashboard keeps running on its own.</p>
          <button type="button" onClick={() => chaos.powerRestore(null, "everything")} className="mt-5 rounded-[10px] bg-brand-strong px-4 py-2 font-medium text-bg-0">
            Turn the power back on
          </button>
        </div>
      </div>
    );

  if (!boot) return null;
  const present = files ?? 0;
  const lost = Math.max(0, boot.before - present) + unreadable;
  return (
    <button type="button" onClick={() => setBoot(null)} className="fixed inset-0 z-40 flex items-center justify-center bg-black/60" aria-label="Dismiss recovery summary">
      <div className="glass flex w-[560px] flex-col items-center gap-3 px-8 py-7 text-center" aria-live="polite">
        <VaultMarkLazy size={160} />
        <p className="text-[20px] font-medium">Power restored</p>
        {bootEvents.map((e) => (
          <p key={e.id} className="text-[15px] text-fg-1">
            {e.human}
          </p>
        ))}
        {!recovered && <p className="text-[15px] text-fg-2">Vault's index is starting…</p>}
        {recovered && (
          <p className="text-[15px] text-fg-1">
            {alive} of {total} machines are back.
          </p>
        )}
        {done && (
          <p className="text-[20px] font-semibold" style={{ color: lost ? "var(--color-dead)" : "var(--color-ok)" }}>
            {present} of {boot.before} files present. {lost} lost.
          </p>
        )}
      </div>
    </button>
  );
}

"use client";
// "Reconnecting to Vault…" (DESIGN §9): the live stream dropped (not a power cut, which has its own overlay).
// EventSource retries by itself; this only tells the viewer. Owner: Anushka (A6).
import { useVault } from "@/lib/store";
import { useEffect, useState } from "react";

export default function ConnectionBanner() {
  const live = useVault((s) => s.source === "live");
  const connected = useVault((s) => s.connected);
  const powerCut = useVault((s) => s.faults.some((f) => f.kind === "power_cut" && f.subject === "all"));
  const [show, setShow] = useState(false);
  // Wait 1.5 s before showing, so a single dropped heartbeat of the stream doesn't flash a banner.
  useEffect(() => {
    if (!live || connected || powerCut) return setShow(false);
    const t = setTimeout(() => setShow(true), 1500);
    return () => clearTimeout(t);
  }, [live, connected, powerCut]);
  if (!show) return null;
  return (
    <div role="status" className="fixed top-0 left-1/2 z-50 -translate-x-1/2 rounded-b-[10px] px-4 py-1.5 text-[13px] font-medium text-bg-0" style={{ background: "var(--color-suspect)" }}>
      Reconnecting to Vault…
    </div>
  );
}

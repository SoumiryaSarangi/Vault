"use client";
// Settings drawer (DESIGN §5.9): mode (Vault / Naive), Explain, presenter mode, reset demo. Owner: Anushka (A10).
// Repair-speed and grace sliders are P1 and left out.
import { META_URL, SUPERVISOR_URL, postJson } from "@/lib/api";
import type { ModeResult, ResetResult } from "@/lib/contracts";
import { useVault } from "@/lib/store";
import { Settings, X } from "lucide-react";
import { useState } from "react";

export default function SettingsDrawer() {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<null | "naive" | "reset">(null);
  const [busy, setBusy] = useState(false);
  const mode = useVault((s) => s.snapshot?.mode ?? "vault");
  const ui = useVault((s) => s.ui);
  const setUi = useVault((s) => s.setUi);
  const toast = useVault((s) => s.toast);

  const setMode = async (m: "vault" | "naive") => {
    try {
      await postJson<ModeResult>(`${META_URL}/v1/mode`, { mode: m });
      toast(m === "naive" ? "Comparison mode: safety features are off." : "Safety features are back on.", m === "naive" ? "info" : "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't change the mode.", "error");
    }
    setConfirm(null);
  };

  const reset = async () => {
    setBusy(true);
    try {
      const r = await postJson<ResetResult>(`${SUPERVISOR_URL}/cluster/reset`, { seed: true, mode: "vault" });
      toast(`Demo reset in ${r.elapsed_s.toFixed(1)} s.`, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't reset the demo.", "error");
    }
    setBusy(false);
    setConfirm(null);
  };

  const Toggle = ({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) => (
    <label className="flex cursor-pointer items-center justify-between py-1.5 text-[14px]">
      {label}
      <input type="checkbox" role="switch" checked={on} onChange={(e) => set(e.target.checked)} className="size-4 accent-[var(--color-brand)]" />
    </label>
  );

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label="Settings" className="rounded-[10px] p-2 text-fg-1 hover:bg-bg-2 hover:text-fg-0">
        <Settings size={18} />
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/40" onClick={() => setOpen(false)}>
          <aside
            role="dialog"
            aria-label="Settings"
            onClick={(e) => e.stopPropagation()}
            className="absolute top-0 right-0 bottom-0 flex w-[380px] flex-col gap-5 border-l border-line-strong bg-bg-1 p-5"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-[18px] font-semibold">Settings</h2>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="text-fg-2 hover:text-fg-0">
                <X size={18} />
              </button>
            </div>

            <section>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Mode</h3>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setMode("vault")}
                  className={`flex-1 rounded-[10px] border px-3 py-2 text-[14px] ${mode === "vault" ? "border-brand text-brand" : "border-line text-fg-1"}`}
                >
                  Vault (safety on)
                </button>
                <button
                  type="button"
                  onClick={() => setConfirm("naive")}
                  className={`flex-1 rounded-[10px] border px-3 py-2 text-[14px] ${mode === "naive" ? "border-[var(--color-naive)] text-[var(--color-naive)]" : "border-line text-fg-1"}`}
                >
                  Naive (safety off)
                </button>
              </div>
              {confirm === "naive" && (
                <div className="mt-2 rounded-[10px] border border-[var(--color-naive)] p-3 text-[13px]">
                  <p>Naive mode turns off checksums, quorum writes, repair, fencing and relays, so you can compare. Data can be lost.</p>
                  <div className="mt-2 flex gap-2">
                    <button type="button" onClick={() => setMode("naive")} className="rounded-md px-3 py-1 font-medium text-bg-0" style={{ background: "var(--color-naive)" }}>
                      Turn safety off
                    </button>
                    <button type="button" onClick={() => setConfirm(null)} className="rounded-md px-3 py-1 text-fg-1 hover:bg-bg-3">
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </section>

            <section>
              <h3 className="mb-1 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">Display</h3>
              <Toggle label="Explain (technical lines)" on={ui.explain} set={(v) => setUi({ explain: v })} />
              <Toggle label="Presenter mode (bigger text)" on={ui.presenter} set={(v) => setUi({ presenter: v })} />
            </section>

            <section className="mt-auto">
              {confirm === "reset" ? (
                <div className="rounded-[10px] border border-line-strong p-3 text-[13px]">
                  <p>Wipes all data and restarts every machine with fresh demo files (about 20 s).</p>
                  <div className="mt-2 flex gap-2">
                    <button type="button" disabled={busy} onClick={reset} className="rounded-md bg-dead px-3 py-1 font-medium text-bg-0 disabled:opacity-60">
                      {busy ? "Resetting…" : "Reset demo"}
                    </button>
                    <button type="button" onClick={() => setConfirm(null)} className="rounded-md px-3 py-1 text-fg-1 hover:bg-bg-3">
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" onClick={() => setConfirm("reset")} className="w-full rounded-[10px] border border-line px-3 py-2 text-[14px] text-fg-1 hover:border-line-strong">
                  Reset demo…
                </button>
              )}
            </section>
          </aside>
        </div>
      )}
    </>
  );
}

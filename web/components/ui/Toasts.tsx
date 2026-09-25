"use client";
// Toast stack (chaos results, errors). Owner: Anushka.
import { CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { useVault } from "@/lib/store";

const LOOK = {
  ok: { icon: CircleCheck, color: "var(--color-ok)" },
  info: { icon: Info, color: "var(--color-brand)" },
  error: { icon: TriangleAlert, color: "var(--color-dead)" },
};

export default function Toasts() {
  const toasts = useVault((s) => s.toasts);
  const dismiss = useVault((s) => s.dismissToast);
  return (
    <div aria-live="polite" className="pointer-events-none fixed top-[68px] left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2">
      {toasts.map((t) => {
        const { icon: Icon, color } = LOOK[t.tone];
        return (
          <div
            key={t.id}
            className="pointer-events-auto flex max-w-xl items-center gap-2.5 rounded-[10px] border border-line-strong bg-bg-2/95 px-3.5 py-2 text-sm shadow-xl"
          >
            <Icon size={16} style={{ color }} aria-hidden />
            <span>{t.text}</span>
            <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss" className="ml-1 text-fg-2 hover:text-fg-0">
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

"use client";
// Small popover that opens above its trigger; closes on outside click and Esc (DESIGN §10). Owner: Anushka.
import { useEffect, useRef, useState, type ReactNode } from "react";

export default function Popover({
  trigger,
  children,
  align = "left",
  label,
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      {trigger({ open, toggle: () => setOpen((o) => !o) })}
      {open && (
        <div
          role="dialog"
          aria-label={label}
          className={`absolute bottom-[calc(100%+8px)] z-50 min-w-56 rounded-[10px] border border-line-strong bg-bg-2 p-2 shadow-2xl ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/** A row in a popover menu. */
export function MenuItem({
  children,
  onClick,
  disabled,
  dot,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  dot?: string;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-fg-0 hover:bg-bg-3 disabled:cursor-not-allowed disabled:text-fg-2 disabled:hover:bg-transparent"
    >
      {dot && <span className="size-2 shrink-0 rounded-full" style={{ background: dot }} />}
      {children}
    </button>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <p className="px-2.5 pb-1 pt-1.5 text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">{children}</p>;
}

"use client";
// Violation samples list. Owner: Urooz (U6). DESIGN §5.6.
import { ViolationSample, ViolationKind } from "@/lib/contracts";

const KIND_STYLE: Record<ViolationKind, { bg: string; text: string; label: string }> = {
  lost:        { bg: "bg-dead/10", text: "text-dead",    label: "LOST" },
  damaged:     { bg: "bg-corrupt/10", text: "text-corrupt", label: "DAMAGED" },
  resurrected: { bg: "bg-suspect/10", text: "text-suspect", label: "RESURRECTED" },
  stale_read:  { bg: "bg-brand/10", text: "text-brand",   label: "STALE READ" },
  phantom_read:{ bg: "bg-partitioned/10", text: "text-partitioned", label: "PHANTOM" },
};

interface Props {
  samples: ViolationSample[];
}

export function ViolationSamples({ samples }: Props) {
  if (samples.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-fg-1 uppercase tracking-widest">What went wrong</h3>
      <div className="flex flex-col gap-2">
        {samples.map((s, i) => {
          const style = KIND_STYLE[s.kind] ?? { bg: "bg-bg-3", text: "text-fg-1", label: s.kind };
          return (
            <div key={i} className={`rounded-xl p-4 border border-line ${style.bg}`}>
              <div className="flex items-start gap-3">
                <span className={`text-[10px] font-black tracking-wider shrink-0 mt-0.5 ${style.text}`}>
                  {style.label}
                </span>
                <div className="flex flex-col gap-0.5 min-w-0">
                  <span className="text-sm text-fg-0 font-medium">
                    <span className="font-mono text-brand">{s.key}</span>{" — "}
                    {s.human}
                  </span>
                  <span className="text-xs text-fg-2 font-mono truncate">{s.technical}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

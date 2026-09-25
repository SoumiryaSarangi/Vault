"use client";
// Chaos timeline strip with violation ticks. Owner: Urooz (U6). DESIGN §5.6.
import { TimelineMark } from "@/lib/contracts";

interface Props {
  timeline: TimelineMark[];
  duration_s: number;
}

export function ChaosTimelineStrip({ timeline, duration_s }: Props) {
  if (timeline.length === 0) return null;
  const total = Math.max(duration_s, 60);

  return (
    <div className="relative h-10 bg-bg-3 rounded-xl overflow-hidden border border-line">
      {/* Timeline track */}
      <div className="absolute inset-0 flex items-center px-2">
        <div className="w-full h-px bg-line-strong" />
      </div>

      {/* Events */}
      {timeline.map((mark, i) => {
        const pct = Math.min((mark.t / total) * 100, 99);
        const isChaos = mark.kind === "chaos";
        return (
          <div
            key={i}
            className="absolute top-0 h-full flex flex-col items-center justify-center group"
            style={{ left: `${pct}%` }}
          >
            <div
              className={`w-2 h-2 rounded-full ring-2 ring-bg-3 transition-transform group-hover:scale-150 ${
                isChaos ? "bg-suspect" : "bg-dead"
              }`}
            />
            {/* Tooltip */}
            <div className="absolute bottom-full mb-2 px-2 py-1 rounded bg-bg-1 border border-line text-xs text-fg-1 whitespace-nowrap
              opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 shadow-lg">
              <span className="text-fg-2">{mark.t.toFixed(1)}s</span>{" "}
              {mark.label}
            </div>
          </div>
        );
      })}

      {/* Legend */}
      <div className="absolute bottom-1 right-2 flex items-center gap-2 text-[10px] text-fg-2">
        <span className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-suspect inline-block" /> chaos
        </span>
        <span className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-dead inline-block" /> violation
        </span>
      </div>
    </div>
  );
}

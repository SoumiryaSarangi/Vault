"use client";
// Proof band: key stats row. Owner: Urooz (U4). DESIGN §5.2.
const STATS = [
  { value: "0", label: "bytes ever lost", color: "text-ok" },
  { value: "< 3s", label: "fault detection", color: "text-brand" },
  { value: "3×", label: "replicated by default", color: "text-brand-strong" },
  { value: "100%", label: "checksum verified", color: "text-ok" },
];

export function ProofBand() {
  return (
    <div className="w-full max-w-3xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-px bg-line rounded-2xl overflow-hidden border border-line">
      {STATS.map(({ value, label, color }) => (
        <div key={label} className="bg-bg-2 flex flex-col items-center justify-center py-6 px-4 gap-1">
          <span className={`text-3xl font-black tabular-nums ${color}`}>{value}</span>
          <span className="text-fg-2 text-xs text-center leading-tight">{label}</span>
        </div>
      ))}
    </div>
  );
}

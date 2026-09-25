"use client";
// "What's happening" timeline (DESIGN §5.3): newest on top, max 50, Explain adds the technical line.
// Chaos actions (the user's own) use a neutral hand icon so they read differently from Vault's reactions.
// Owner: Anushka.
import type { VaultEvent } from "@/lib/contracts";
import { fmtTime } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { SEVERITY_LOOK } from "@/lib/states";
import { useVault } from "@/lib/store";
import { Hand } from "lucide-react";

const MAX_VISIBLE = 50;

function EventItem({ e, explain, now }: { e: VaultEvent; explain: boolean; now: number }) {
  const chaos = e.type.startsWith("chaos.");
  const { icon: Icon, color } = chaos ? { icon: Hand, color: "var(--color-fg-1)" } : SEVERITY_LOOK[e.severity];
  const pct = e.type === "repair.progress" ? Number(e.data?.pct ?? NaN) : NaN;
  return (
    <li className="flex gap-2.5 py-2 motion-safe:animate-[enter_240ms_var(--ease-out)]">
      <Icon size={15} style={{ color }} className="mt-0.5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] leading-5">{e.human}</p>
        {!Number.isNaN(pct) && (
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-3">
            <div className="h-full rounded-full bg-repair" style={{ width: `${Math.min(100, pct)}%` }} />
          </div>
        )}
        {explain && e.technical && <p className="mt-0.5 break-words font-mono text-[13px] leading-[18px] text-fg-2">{e.technical}</p>}
      </div>
      <time className="shrink-0 font-mono text-[12px] tabular-nums text-fg-2" dateTime={new Date(e.ts * 1000).toISOString()}>
        {fmtTime(e.ts, now)}
      </time>
    </li>
  );
}

export default function EventTimeline() {
  const events = useVault((s) => s.events);
  const explain = useVault((s) => s.ui.explain);
  const presenter = useVault((s) => s.ui.presenter);
  const setUi = useVault((s) => s.setUi);
  const now = useNow(1000);
  const visible = events.slice(0, presenter ? 20 : MAX_VISIBLE);
  const urgent = events[0]?.severity === "danger";

  return (
    <section aria-label="What's happening" className="flex min-h-0 flex-col">
      <div className="mb-1 flex h-4 items-center justify-between">
        <h2 className="text-[12px] font-medium uppercase tracking-[0.04em] text-fg-2">What&apos;s happening</h2>
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-fg-1">
          Explain
          <input
            type="checkbox"
            role="switch"
            checked={explain}
            onChange={(ev) => setUi({ explain: ev.target.checked })}
            className="size-4 accent-[var(--color-brand)]"
          />
        </label>
      </div>
      {visible.length === 0 ? (
        <p className="py-6 text-[14px] text-fg-2">Nothing has happened yet. Try a chaos button below.</p>
      ) : (
        <ol aria-live={urgent ? "assertive" : "polite"} className="min-h-0 divide-y divide-line overflow-y-auto pr-1">
          {visible.map((e) => (
            <EventItem key={e.id} e={e} explain={explain} now={now} />
          ))}
        </ol>
      )}
    </section>
  );
}

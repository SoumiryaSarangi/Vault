"use client";
// Top bar (DESIGN §5.3): mark + wordmark · health sentence · tabs · mode badge · data source. Owner: Anushka.
import Wordmark from "@/components/brand/Wordmark";
import { LEVEL_COLOR } from "@/lib/states";
import { useVault } from "@/lib/store";
import Link from "next/link";
import SettingsDrawer from "./SettingsDrawer";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/console", label: "Overview" },
  { href: "/console/files", label: "Files" },
  { href: "/console/fate", label: "Fate" },
  { href: "/console/oracle", label: "Check" },
];

const NAIVE_PREFIX = "Comparison mode: safety features are off. ";

export function HealthSentence() {
  const summary = useVault((s) => s.snapshot?.summary);
  const mode = useVault((s) => s.snapshot?.mode);
  const explain = useVault((s) => s.ui.explain);
  const source = useVault((s) => s.source);
  if (!summary) {
    return (
      <p className="truncate text-[18px] font-medium text-fg-1" aria-live="polite">
        {source === "connecting" ? "Connecting to Vault…" : "Vault isn't running. Start it with python -m vault up."}
      </p>
    );
  }
  const human = mode === "naive" && !summary.human.startsWith(NAIVE_PREFIX) ? NAIVE_PREFIX + summary.human : summary.human;
  return (
    <div className="flex min-w-0 items-center gap-2.5" aria-live="polite">
      <span className="size-2.5 shrink-0 rounded-full" style={{ background: LEVEL_COLOR[summary.level] }} aria-hidden />
      <div className="min-w-0">
        <p className="truncate text-[18px] leading-[26px] font-medium">{human}</p>
        {explain && summary.technical && <p className="truncate font-mono text-[13px] text-fg-2">{summary.technical}</p>}
      </div>
    </div>
  );
}

export default function TopBar() {
  const path = usePathname();
  const mode = useVault((s) => s.snapshot?.mode);
  const source = useVault((s) => s.source);
  return (
    <header className="flex h-14 items-center gap-6 border-b border-line px-5">
      <Link href="/" aria-label="Vault home">
        <Wordmark />
      </Link>
      <div className="min-w-0 flex-1">
        <HealthSentence />
      </div>
      {source === "sample" && (
        <span
          className="shrink-0 rounded-full border border-line-strong px-2.5 py-0.5 text-[12px] font-medium text-fg-1"
          title="Vault's live stream isn't available yet, so the console is showing sample data from web/fixtures."
        >
          Sample data
        </span>
      )}
      {mode === "naive" && (
        <span className="shrink-0 rounded-full px-2.5 py-0.5 text-[12px] font-semibold text-bg-0" style={{ background: "var(--color-naive)" }}>
          Comparison mode
        </span>
      )}
      <nav className="flex shrink-0 gap-1" aria-label="Console">
        {TABS.map((t) => {
          const active = t.href === "/console" ? path === "/console" : path.startsWith(t.href);
          return (
            <Link
              key={t.href}
              href={t.href}
              aria-current={active ? "page" : undefined}
              className={`rounded-[10px] px-3 py-1.5 text-sm transition-colors ${
                active ? "bg-bg-3 text-brand" : "text-fg-1 hover:bg-bg-2 hover:text-fg-0"
              }`}
            >
              {t.label}
            </Link>
          );
        })}
      </nav>
      <SettingsDrawer />
    </header>
  );
}

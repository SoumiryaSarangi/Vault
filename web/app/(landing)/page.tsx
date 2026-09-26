// Landing page "/" (DESIGN §5.2). Owner: Urooz (task U4). Components go in components/landing/urooz_*.tsx.
// Shared brand pieces (VaultMark) come from components/brand (Anushka).
import type { Metadata } from "next";
import { Hero } from "@/components/landing/urooz_Hero";
import { StoryScroller } from "@/components/landing/urooz_StoryScroller";
import { ProofBand } from "@/components/landing/urooz_ProofBand";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Vault — Storage that heals itself",
  description:
    "S3-grade durability on ordinary computers. No cloud, no sysadmin. Checksums, automatic repair, and the Durability Oracle — all in plain language.",
};

export default function Landing() {
  return (
    <div className="min-h-screen bg-bg-0 text-fg-0 flex flex-col">
      {/* Top bar */}
      <header role="banner" className="fixed top-0 left-0 right-0 z-50 flex items-center justify-between px-6 py-4
        bg-bg-0/80 backdrop-blur-md border-b border-line">
        <span className="text-sm font-bold tracking-[0.25em] text-brand uppercase" aria-label="Vault">VAULT</span>
        <Link
          id="nav-console"
          href="/console"
          className="text-sm text-fg-1 hover:text-fg-0 transition-colors"
        >
          Open console <span aria-hidden="true">→</span>
        </Link>
      </header>

      <main id="main-content" tabIndex={-1} className="flex flex-col outline-none">
      {/* Hero */}
      <Hero />

      {/* Story beats */}
      <section className="py-24 flex flex-col items-center gap-16">
        <StoryScroller />
      </section>

      {/* Proof band */}
      <section className="py-16 flex flex-col items-center gap-8 px-4">
        <h2 className="text-2xl font-bold text-center text-fg-0">By the numbers</h2>
        <ProofBand />
      </section>

      {/* Footer CTA */}
      <section className="py-24 flex flex-col items-center gap-6 text-center px-4">
        <h2 className="text-3xl font-bold text-fg-0">See it heal itself.</h2>
        <p className="text-fg-1 max-w-md">
          Kill a machine from the dashboard, watch the repair queue fill, and see your files come back — MTTR on screen.
        </p>
        <Link
          id="footer-cta"
          href="/console"
          className="px-8 py-4 rounded-2xl border border-brand text-brand font-semibold
            hover:bg-brand/10 transition-colors"
        >
          Open the console <span aria-hidden="true">→</span>
        </Link>
      </section>

      </main>

      {/* Footer */}
      <footer className="border-t border-line py-8 px-6 text-center text-fg-2 text-xs">
        Vault · Built for Reforge · 2026
      </footer>
    </div>
  );
}

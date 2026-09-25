"use client";
// Landing hero — entrance animation, wordmark, tagline, CTA. Owner: Urooz (U4). DESIGN §5.2.
import { useEffect, useState } from "react";
import { HeroBackground } from "./urooz_HeroBackground";
import { CtaButton } from "./urooz_CtaButton";

export function Hero() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 80);
    return () => clearTimeout(t);
  }, []);

  return (
    <section className="relative flex flex-col items-center justify-center min-h-[90vh] text-center px-6 overflow-hidden">
      {/* Animated gradient background */}
      <HeroBackground />

      {/* Noise texture overlay */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.03]"
        style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E\")" }}
        aria-hidden
      />

      {/* Content */}
      <div className={`relative z-10 flex flex-col items-center gap-8 transition-all duration-1000
        ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"}`}>

        {/* Wordmark pill */}
        <div className="flex items-center gap-2 px-4 py-1.5 rounded-full border border-brand/30 bg-brand/10 text-brand text-xs font-bold tracking-[0.3em] uppercase">
          <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse" />
          VAULT
        </div>

        {/* Main headline */}
        <h1 className="text-5xl sm:text-7xl font-black tracking-tight text-fg-0 max-w-3xl leading-[1.05]">
          Storage that{" "}
          <span className="bg-gradient-to-r from-brand-strong to-ok bg-clip-text text-transparent">
            heals itself.
          </span>
        </h1>

        {/* Subline */}
        <p className="text-lg sm:text-xl text-fg-1 max-w-xl leading-relaxed">
          S3-grade durability on a few ordinary computers.{" "}
          <span className="text-fg-0">No cloud. No sysadmin.</span>{" "}
          Checksums, replicas, automatic repair — all in plain language.
        </p>

        {/* CTA */}
        <CtaButton href="/console">Open the console</CtaButton>

        {/* Scroll hint */}
        <div className="flex flex-col items-center gap-1 text-fg-2 text-xs mt-4 animate-bounce">
          <span>scroll to see how it works</span>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>
      </div>
    </section>
  );
}

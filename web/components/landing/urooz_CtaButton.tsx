"use client";
// CTA button with magnetic hover effect. Owner: Urooz (U4). DESIGN §5.2.
import Link from "next/link";
import { useRef, MouseEvent } from "react";

interface Props {
  href: string;
  children: React.ReactNode;
}

export function CtaButton({ href, children }: Props) {
  const btnRef = useRef<HTMLAnchorElement>(null);

  const onMouseMove = (e: MouseEvent<HTMLAnchorElement>) => {
    const btn = btnRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2;
    const y = e.clientY - rect.top - rect.height / 2;
    btn.style.transform = `translate(${x * 0.12}px, ${y * 0.12}px) scale(1.04)`;
  };

  const onMouseLeave = () => {
    const btn = btnRef.current;
    if (btn) btn.style.transform = "";
  };

  return (
    <Link
      id="cta-open-console"
      ref={btnRef}
      href={href}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      className="relative inline-flex items-center gap-2.5 px-8 py-4 rounded-2xl
        bg-gradient-to-br from-brand-strong to-brand text-bg-0 text-base font-bold
        shadow-[0_0_40px_rgba(127,156,255,0.35)]
        hover:shadow-[0_0_60px_rgba(127,156,255,0.5)]
        transition-[box-shadow] duration-300
        select-none"
      style={{ willChange: "transform", transition: "transform 0.2s cubic-bezier(0.16,1,0.3,1), box-shadow 0.3s ease" }}
    >
      {children}
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
        <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    </Link>
  );
}

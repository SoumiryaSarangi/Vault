"use client";
// Liquid-metal Vault logo (TECH_STACK §6.5, DESIGN §2.2). Owner: Anushka.
// WebGL: import it through VaultMarkLazy (next/dynamic, ssr:false), not directly from a server component.
// Pauses (speed 0) when scrolled out of view or with reduced motion (DESIGN §4.2, §3.5).
import { LiquidMetal } from "@paper-design/shaders-react";
import { useEffect, useRef, useState } from "react";

export default function VaultMark({ size = 240, className }: { size?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    setReduced(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div ref={ref} className={className} style={{ width: size, height: size }} aria-label="Vault" role="img">
      <LiquidMetal
        image="/brand/vault-mark.svg"
        colorBack="#00000000"
        colorTint="#ffffff"
        speed={visible && !reduced ? 1 : 0}
        style={{ width: size, height: size }}
      />
    </div>
  );
}

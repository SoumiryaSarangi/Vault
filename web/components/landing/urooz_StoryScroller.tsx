"use client";
// 4-beat scroll story section. Owner: Urooz (U4). DESIGN §5.2.
import { useEffect, useRef, useState } from "react";

const BEATS = [
  {
    icon: "💾",
    title: "Upload once, stored on 3 machines",
    body: "Every file is split into chunks, checksummed, and spread across machines that don't share a power strip. Your data is never in one basket.",
  },
  {
    icon: "🔍",
    title: "Silent corruption? Caught instantly.",
    body: "Every read verifies the checksum. Every scrub pass checks the bytes at rest. A flipped bit never reaches your application.",
  },
  {
    icon: "💀",
    title: "Machine dies. Files stay alive.",
    body: "A dead machine triggers automatic repair in seconds — not minutes, not hours. Vault builds missing copies from surviving ones and tells you exactly how long it took.",
  },
  {
    icon: "📊",
    title: "The Oracle proves it",
    body: "An independent process hammers the system with 8 clients, then checks every write was durable. Vault: 0 violations. Naive: dozens. The numbers speak.",
  },
];

function Beat({ icon, title, body, index }: { icon: string; title: string; body: string; index: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) setVisible(true); },
      { threshold: 0.3 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`flex items-start gap-6 p-8 rounded-2xl border border-line bg-bg-2/60 backdrop-blur
        transition-all duration-700 ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"}`}
      style={{ transitionDelay: `${index * 80}ms` }}
    >
      <span className="text-4xl select-none shrink-0">{icon}</span>
      <div>
        <h3 className="text-lg font-semibold text-fg-0 mb-2">{title}</h3>
        <p className="text-fg-1 text-sm leading-relaxed">{body}</p>
      </div>
    </div>
  );
}

export function StoryScroller() {
  return (
    <section className="w-full max-w-2xl mx-auto flex flex-col gap-4 px-4">
      {BEATS.map((beat, i) => (
        <Beat key={i} {...beat} index={i} />
      ))}
    </section>
  );
}

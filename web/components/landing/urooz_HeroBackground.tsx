"use client";
// Hero background: animated gradient mesh. Owner: Urooz (U4). TECH_STACK §6.5–6.6.
import { useEffect, useRef } from "react";

export function HeroBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf: number;
    let t = 0;

    const resize = () => {
      canvas.width = canvas.offsetWidth * devicePixelRatio;
      canvas.height = canvas.offsetHeight * devicePixelRatio;
    };
    resize();
    window.addEventListener("resize", resize);

    const orbs = [
      { x: 0.2, y: 0.3, r: 0.4, color: "#7f9cff", speed: 0.0003 },
      { x: 0.7, y: 0.6, r: 0.35, color: "#34d399", speed: 0.0004 },
      { x: 0.5, y: 0.1, r: 0.3, color: "#b8ccff", speed: 0.0002 },
    ];

    const draw = () => {
      const w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      orbs.forEach((orb, i) => {
        const px = (orb.x + Math.sin(t * orb.speed * 1000 + i) * 0.15) * w;
        const py = (orb.y + Math.cos(t * orb.speed * 800 + i) * 0.1) * h;
        const grad = ctx.createRadialGradient(px, py, 0, px, py, orb.r * Math.min(w, h));
        grad.addColorStop(0, orb.color + "22");
        grad.addColorStop(1, "transparent");
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, w, h);
      });

      t++;
      raf = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      aria-hidden
    />
  );
}

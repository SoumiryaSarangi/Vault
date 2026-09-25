// Static Vault mark: top bar, favicon-sized uses, fallbacks (DESIGN §2.2). Owner: Anushka.
// Same path as the shader input, with a vertical gradient stroke #FFFFFF → #8A94A8.
import { useId } from "react";

export default function VaultMarkStatic({ size = 22, className }: { size?: number | string; className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 200 200" width={size} height={size} className={className} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FFFFFF" />
          <stop offset="100%" stopColor="#8A94A8" />
        </linearGradient>
      </defs>
      <path
        d="M40 40 L100 160 L160 40"
        fill="none"
        stroke={`url(#${id})`}
        strokeWidth={34}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

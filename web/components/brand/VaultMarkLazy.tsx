"use client";
// Use this to place the liquid-metal logo anywhere (TECH_STACK §6.7). Owner: Anushka.
// Shows the static mark while the shader loads, so there is no empty box (DESIGN §4.3 rule 7).
import dynamic from "next/dynamic";
import VaultMarkStatic from "./VaultMarkStatic";

const VaultMark = dynamic(() => import("./VaultMark"), {
  ssr: false,
  loading: () => <VaultMarkStatic size="100%" />,
});

export default function VaultMarkLazy({ size = 240, className }: { size?: number; className?: string }) {
  return (
    <div className={className} style={{ width: size, height: size }}>
      <VaultMark size={size} />
    </div>
  );
}

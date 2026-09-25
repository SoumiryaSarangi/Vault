// "V A U L T" wordmark, with an optional static mark (DESIGN §2.1, §3.2). Owner: Anushka.
import VaultMarkStatic from "./VaultMarkStatic";

export default function Wordmark({ withMark = true, className = "" }: { withMark?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      {withMark && <VaultMarkStatic size={22} />}
      <span className="text-sm font-semibold tracking-[0.32em] text-fg-0">VAULT</span>
    </span>
  );
}

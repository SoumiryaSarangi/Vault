// Landing page "/" (DESIGN §5.2). Owner: Urooz (task U4). Components go in components/landing/urooz_*.tsx.
// Shared brand pieces (VaultMark) come from components/brand (Anushka).
import Link from "next/link";

export default function Landing() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <p className="text-sm font-semibold tracking-[0.32em]">V A U L T</p>
      <h1 className="text-5xl font-semibold tracking-tight">Storage that heals itself.</h1>
      <p className="max-w-xl text-lg text-fg-1">
        S3-grade durability on a few ordinary machines. No cloud. No sysadmin.
      </p>
      <Link href="/console" className="glass px-6 py-3 text-brand">
        Open the console →
      </Link>
    </main>
  );
}

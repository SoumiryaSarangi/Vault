// Console shell (DESIGN §5.3): top bar, KPI strip and chaos dock on every console page. Owner: Anushka (A5).
import ConsoleShell from "@/components/console/ConsoleShell";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <ConsoleShell>{children}</ConsoleShell>;
}

// Console shell (DESIGN §5.3): top bar, KPI strip and chaos dock on every console page. Owner: Anushka (A6).
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <div className="console-bg min-h-screen">{children}</div>;
}

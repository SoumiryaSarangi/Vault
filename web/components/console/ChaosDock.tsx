"use client";
// Chaos dock (DESIGN §5.3, §5.8): two clicks to break anything, no typing on stage. Active faults show as
// removable chips above the dock. Every action goes to the supervisor (lib/chaos.ts). Owner: Anushka.
import Popover, { MenuItem, MenuLabel } from "@/components/ui/Popover";
import { chaos } from "@/lib/chaos";
import type { ClusterInfo, Fault, NodeView } from "@/lib/contracts";
import { domainName } from "@/lib/format";
import { useNames } from "@/lib/hooks";
import { nodeLook } from "@/lib/states";
import { useVault } from "@/lib/store";
import { Bug, Copy, Eraser, Laptop, Plus, PlugZap, Power, Snail, Snowflake, Unplug, X, Zap, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

function DockButton({ icon: Icon, label, onClick, open }: { icon: LucideIcon; label: string; onClick: () => void; open?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className={`flex h-10 items-center gap-2 rounded-[10px] border px-3.5 text-[14px] font-medium transition-colors ${
        open ? "border-brand bg-bg-3 text-fg-0" : "border-line bg-bg-1/70 text-fg-0 hover:border-line-strong hover:bg-bg-2"
      }`}
    >
      <Icon size={16} aria-hidden />
      {label}
    </button>
  );
}

const NO_NODES: NodeView[] = [];   // stable fallback: a new [] per render would loop zustand forever

function useMachines() {
  const nodes = useVault((s) => s.snapshot?.nodes ?? NO_NODES);
  const procs = useVault((s) => s.procs);
  const names = useNames();
  // Supervisor is the truth for on/off; the snapshot adds state colors and labels.
  const machines = procs.length
    ? procs
        .filter((p) => p.pid.startsWith("n"))
        .map((p) => ({
          id: p.pid,
          name: names(p.pid),                           // the snapshot has renames first
          running: p.state === "running",
          node: nodes.find((n) => n.id === p.pid),
          off: p.note ?? "off",                         // LAN mode: "asleep or off the network"
        }))
    : nodes.map((n) => ({ id: n.id, name: n.display_name, running: n.state !== "DEAD", node: n as NodeView | undefined, off: "off" }));
  const dot = (n?: NodeView) => (n ? nodeLook(n, names).color : "var(--color-fg-2)");
  return { machines, dot, names };
}

/** Power strips in use (from the live labels, A–C by default) plus the next free letter for a new machine. */
function useStrips(): { strips: string[]; next: string } {
  const nodes = useVault((s) => s.snapshot?.nodes ?? NO_NODES);
  const used = [...new Set(["A", "B", "C", ...nodes.map((n) => n.labels.power).filter(Boolean)])].sort();
  const last = used[used.length - 1] ?? "C";
  return { strips: used, next: String.fromCharCode(last.charCodeAt(0) + 1) };
}

function faultLabel(f: Fault, name: (id: string) => string): string {
  const p = f.params as Record<string, string | number>;
  switch (f.kind) {
    case "link":
      return `Cable cut: ${name(String(p.a))} ↔ ${name(String(p.b))}`;
    case "slow":
      return `Slow: ${name(f.subject)} +${p.ms} ms`;
    case "freeze":
      return `Frozen: ${name(f.subject)}`;
    case "disk_full":
      return `Disk full: ${name(f.subject)}`;
    case "power_cut":
      return f.subject === "all" ? "Power cut: everything" : `Power cut: ${domainName(...(f.subject.split("=") as [string, string]))}`;
    default:
      return `${f.kind}: ${f.subject}`;
  }
}

async function removeFault(f: Fault, name: (id: string) => string) {
  const p = f.params as Record<string, string>;
  if (f.kind === "link") return chaos.reconnect(p.a, p.b, name(p.a), name(p.b));
  if (f.kind === "slow") return chaos.slow(f.subject, name(f.subject), 0);
  if (f.kind === "disk_full") return chaos.diskFull(f.subject, name(f.subject), false);
  if (f.kind === "power_cut") {
    const label = f.subject === "all" ? null : f.subject;
    return chaos.powerRestore(label, label ? domainName(...(label.split("=") as [string, string])) : "everything");
  }
}

export function ActiveFaults() {
  const faults = useVault((s) => s.faults);
  const names = useNames();
  if (!faults.length) return null;
  return (
    <div className="absolute bottom-2 left-1/2 z-10 flex w-max max-w-[560px] -translate-x-1/2 flex-wrap justify-center gap-1.5" aria-label="Active faults">
      {faults.map((f) => (
        <span key={f.id} className="inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-bg-2/95 py-0.5 pl-3 pr-1 text-[12px] shadow-lg">
          {faultLabel(f, names)}
          {f.kind !== "freeze" && (
            <button type="button" onClick={() => removeFault(f, names)} aria-label={`Undo: ${faultLabel(f, names)}`} className="rounded-full p-0.5 text-fg-2 hover:bg-bg-3 hover:text-fg-0">
              <X size={12} />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

function CableMenu({ close }: { close: () => void }) {
  const { machines, names } = useMachines();
  const [a, setA] = useState<string | null>(null);
  const [oneWay, setOneWay] = useState(false);
  const ends = [{ id: "meta", name: "Vault index" }, { id: "gw", name: "Vault gateway" }, ...machines.map((m) => ({ id: m.id, name: m.name }))];
  return (
    <div className="w-60">
      <MenuLabel>{a ? `Cut ${names(a)} from…` : "Cut the cable from…"}</MenuLabel>
      {ends
        .filter((e) => e.id !== a)
        .map((e) => (
          <MenuItem
            key={e.id}
            onClick={() => {
              if (!a) return setA(e.id);
              chaos.cutCable(a, e.id, names(a), e.name, oneWay);
              close();
            }}
          >
            {e.name}
          </MenuItem>
        ))}
      <label className="mt-1 flex items-center gap-2 border-t border-line px-2.5 pt-2 text-[13px] text-fg-1">
        <input type="checkbox" checked={oneWay} onChange={(ev) => setOneWay(ev.target.checked)} className="accent-[var(--color-brand)]" />
        One way only
      </label>
    </div>
  );
}

/** Pick a machine, then an option (keeps menus short enough for a 720 px screen). */
function TwoStepMenu({
  close,
  title,
  options,
  onPick,
  filter,
}: {
  close: () => void;
  title: string;
  options: { label: string; value: string }[] | ((m: { id: string; node?: NodeView }) => { label: string; value: string }[]);
  onPick: (id: string, name: string, value: string, node?: NodeView) => void;
  filter?: (m: { running: boolean; node?: NodeView }) => boolean;
}) {
  const { machines, dot } = useMachines();
  const [m, setM] = useState<(typeof machines)[number] | null>(null);
  if (!m)
    return (
      <>
        <MenuLabel>{title}</MenuLabel>
        {machines.map((x) => (
          <MenuItem key={x.id} dot={x.running ? dot(x.node) : "var(--color-fg-2)"} disabled={filter ? !filter(x) : !x.running} onClick={() => setM(x)}>
            {x.name}
            {!x.running && <span className="ml-auto text-[12px] text-fg-2">{x.off}</span>}
          </MenuItem>
        ))}
      </>
    );
  const opts = typeof options === "function" ? options(m) : options;
  return (
    <>
      <MenuLabel>{m.name}</MenuLabel>
      {opts.map((o) => (
        <MenuItem key={o.value} onClick={() => (onPick(m.id, m.name, o.value, m.node), close())}>
          {o.label}
        </MenuItem>
      ))}
    </>
  );
}

function StripPicker({ strips, value, onChange }: { strips: string[]; value: string; onChange: (s: string) => void }) {
  return (
    <div className="flex items-center gap-1" role="radiogroup" aria-label="Power strip">
      <Zap size={13} className="shrink-0 text-fg-2" aria-hidden />
      {strips.map((s) => (
        <button
          type="button"
          key={s}
          role="radio"
          aria-checked={value === s}
          onClick={() => onChange(s)}
          className={`flex-1 rounded-md border px-2 py-1 text-[13px] ${value === s ? "border-brand text-brand" : "border-line text-fg-1"}`}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

const INPUT = "rounded-md border border-line-strong bg-bg-3 px-2.5 py-1.5 text-sm outline-none focus:border-brand";

/** Add → Simulated (a process on this laptop) or Real laptop (the `vault join` command to run on it). */
function AddMachineMenu({ close }: { close: () => void }) {
  const { strips, next } = useStrips();
  const [tab, setTab] = useState<"sim" | "laptop">("sim");
  const [name, setName] = useState("Storeroom PC");
  const [strip, setStrip] = useState(strips[0] ?? "A");
  const [info, setInfo] = useState<ClusterInfo | null | undefined>(undefined);
  useEffect(() => {
    if (tab === "laptop" && info === undefined) chaos.clusterInfo().then(setInfo);
  }, [tab, info]);
  const choices = [...strips, next];
  const cmd = info ? `python -m vault join --hub ${info.hub} --id ${info.next_node_id} --name "${name.trim()}" --strip ${strip}` : "";

  return (
    <div className="flex w-72 flex-col gap-2 p-1">
      <div className="flex gap-1 rounded-md bg-bg-3 p-0.5" role="tablist" aria-label="Kind of machine">
        {(
          [
            ["sim", "Simulated"],
            ["laptop", "Real laptop"],
          ] as const
        ).map(([k, label]) => (
          <button
            type="button"
            role="tab"
            key={k}
            aria-selected={tab === k}
            onClick={() => setTab(k)}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-[13px] font-medium ${tab === k ? "bg-bg-1 text-fg-0" : "text-fg-1"}`}
          >
            {k === "laptop" && <Laptop size={13} aria-hidden />}
            {label}
          </button>
        ))}
      </div>
      <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} aria-label="Machine name" className={INPUT} />
      <StripPicker strips={choices} value={strip} onChange={setStrip} />
      {tab === "sim" ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            chaos.addMachine(name.trim(), { power: strip, switch: "S1", disk_batch: "D1", version: "1.0" });
            close();
          }}
        >
          <p className="mb-2 text-[12px] text-fg-2">Runs on this laptop, like the others. No extra device needed.</p>
          <button type="submit" className="w-full rounded-md bg-brand-strong px-3 py-1.5 text-sm font-medium text-bg-0">
            Add machine
          </button>
        </form>
      ) : info === undefined ? (
        <p className="text-[12px] text-fg-2">Asking the hub…</p>
      ) : info === null ? (
        <p className="text-[12px] text-dead">Couldn&apos;t reach Vault&apos;s supervisor. Is `python -m vault up --lan` running?</p>
      ) : (
        <>
          {!info.lan && (
            <p className="text-[12px] text-suspect">
              This hub isn&apos;t on the network yet. Restart it with <code>python -m vault up --lan</code>.
            </p>
          )}
          <p className="text-[12px] text-fg-1">On the new laptop, in the Vault folder, run:</p>
          <code className="block rounded-md border border-line bg-bg-3 p-2 font-mono text-[12px] break-all text-fg-0">{cmd}</code>
          <button
            type="button"
            onClick={() =>
              navigator.clipboard.writeText(cmd).then(
                () => useVault.getState().toast("Copied. Run it on the new laptop; it shows up here when it joins.", "ok"),
                () => useVault.getState().toast("Couldn't copy. Select the command and copy it by hand.", "error"),
              )
            }
            className="flex items-center justify-center gap-1.5 rounded-md bg-brand-strong px-3 py-1.5 text-sm font-medium text-bg-0"
          >
            <Copy size={14} aria-hidden /> Copy command
          </button>
        </>
      )}
    </div>
  );
}

export default function ChaosDock() {
  const { machines, dot } = useMachines();
  const { strips } = useStrips();
  const mode = useVault((s) => s.snapshot?.mode);
  const [autoRestore, setAutoRestore] = useState(true);
  const pick = (action: (id: string, name: string) => void, close: () => void, onlyRunning = true) =>
    machines.map((m) => (
      <MenuItem
        key={m.id}
        dot={m.running ? dot(m.node) : "var(--color-fg-2)"}
        disabled={onlyRunning && !m.running}
        onClick={() => {
          action(m.id, m.name);
          close();
        }}
      >
        {m.name}
        {!m.running && <span className="ml-auto text-[12px] text-fg-2">{m.off}</span>}
      </MenuItem>
    ));

  return (
    <div
      className="flex h-16 items-center gap-2 border-t px-5"
      style={{ borderColor: mode === "naive" ? "var(--color-naive)" : "var(--color-line)" }}
      role="toolbar"
      aria-label="Break something"
    >
      <Popover label="Turn a machine off or on" trigger={({ open, toggle }) => <DockButton icon={Power} label="Turn off" onClick={toggle} open={open} />}>
        {(close) => (
          <>
            <MenuLabel>Turn off</MenuLabel>
            {pick((id, n) => chaos.turnOff(id, n), close)}
            {machines.some((m) => !m.running) && (
              <>
                <MenuLabel>Turn back on</MenuLabel>
                {machines
                  .filter((m) => !m.running)
                  .map((m) => (
                    <MenuItem key={m.id} onClick={() => (chaos.turnOn(m.id, m.name), close())}>
                      {m.name}
                    </MenuItem>
                  ))}
              </>
            )}
          </>
        )}
      </Popover>
      <Popover label="Damage copies" trigger={({ open, toggle }) => <DockButton icon={Bug} label="Damage" onClick={toggle} open={open} />}>
        {(close) => (
          <>
            <MenuLabel>Damage random copies</MenuLabel>
            {[1, 10, 50].map((n) => (
              <MenuItem key={n} onClick={() => (chaos.damage(n), close())}>
                {n} {n === 1 ? "copy" : "copies"}
              </MenuItem>
            ))}
          </>
        )}
      </Popover>
      <Popover label="Cut a cable" trigger={({ open, toggle }) => <DockButton icon={Unplug} label="Cut cable" onClick={toggle} open={open} />}>
        {(close) => <CableMenu close={close} />}
      </Popover>
      <Popover label="Slow a machine" trigger={({ open, toggle }) => <DockButton icon={Snail} label="Slow" onClick={toggle} open={open} />}>
        {(close) => (
          <TwoStepMenu
            close={close}
            title="Slow down"
            options={[300, 800, 2000].map((ms) => ({ label: `by ${ms} ms`, value: String(ms) }))}
            onPick={(id, n, v) => chaos.slow(id, n, Number(v))}
          />
        )}
      </Popover>
      <Popover label="Freeze a machine" trigger={({ open, toggle }) => <DockButton icon={Snowflake} label="Freeze" onClick={toggle} open={open} />}>
        {(close) => (
          <>
            <MenuLabel>Freeze for 10 s</MenuLabel>
            {pick((id, n) => chaos.freeze(id, n, 10), close)}
          </>
        )}
      </Popover>
      <Popover label="Power cut" trigger={({ open, toggle }) => <DockButton icon={Zap} label="Power cut" onClick={toggle} open={open} />}>
        {(close) => (
          <>
            <MenuLabel>Cut the power</MenuLabel>
            <MenuItem onClick={() => (chaos.powerCut(null, "everything", autoRestore ? 8 : null), close())}>Everything</MenuItem>
            {strips.map((s) => (
              <MenuItem key={s} onClick={() => (chaos.powerCut(`power=${s}`, `Power Strip ${s}`, autoRestore ? 8 : null), close())}>
                Power Strip {s}
              </MenuItem>
            ))}
            <label className="mt-1 flex items-center gap-2 border-t border-line px-2.5 pt-2 text-[13px] text-fg-1">
              <input type="checkbox" checked={autoRestore} onChange={(e) => setAutoRestore(e.target.checked)} className="accent-[var(--color-brand)]" />
              Turn back on after 8 s
            </label>
          </>
        )}
      </Popover>
      <Popover label="Move a plug" trigger={({ open, toggle }) => <DockButton icon={PlugZap} label="Move plug" onClick={toggle} open={open} />}>
        {(close) => (
          <TwoStepMenu
            close={close}
            title="Move whose plug?"
            filter={(m) => !!m.node}
            options={(m) => strips.filter((s) => m.node?.labels.power !== s).map((s) => ({ label: `onto Power Strip ${s}`, value: s }))}
            onPick={(id, n, strip, node) => chaos.movePlug(id, n, node?.labels ?? {}, strip)}
          />
        )}
      </Popover>
      <Popover label="Add a machine" trigger={({ open, toggle }) => <DockButton icon={Plus} label="Add" onClick={toggle} open={open} />}>
        {(close) => <AddMachineMenu close={close} />}
      </Popover>
      <div className="ml-auto">
        <DockButton icon={Eraser} label="Clear" onClick={() => chaos.clearAll()} />
      </div>
    </div>
  );
}

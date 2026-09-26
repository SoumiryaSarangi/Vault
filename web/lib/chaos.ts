// Chaos actions → supervisor (ARCHITECTURE §7.4), with DESIGN §5.8 toasts. Owner: Anushka.
import { ApiError, META_URL, SUPERVISOR_URL, getJson, patchJson, postJson } from "./api";
import type { ClusterInfo, FaultList, PowerCutResult, Proc } from "./contracts";
import { useVault } from "./store";

async function run<T>(call: () => Promise<T>, ok: string): Promise<T | null> {
  const { toast } = useVault.getState();
  try {
    const r = await call();
    toast(ok, "ok");
    return r;
  } catch (e) {
    const msg =
      e instanceof ApiError ? e.message : "Couldn't reach Vault's supervisor. Is `python -m vault up` running?";
    toast(msg, "error");
    return null;
  }
}

const sup = (path: string) => `${SUPERVISOR_URL}${path}`;

export const chaos = {
  turnOff: (pid: string, name: string) => run(() => postJson<Proc>(sup(`/procs/${pid}/kill`)), `You turned off ${name}.`),
  turnOn: (pid: string, name: string) => run(() => postJson<Proc>(sup(`/procs/${pid}/start`)), `You turned ${name} back on.`),
  damage: (count: number) =>
    run(() => postJson(sup("/chaos/corrupt"), { count, mode: "bitflip" }), `You damaged ${count} random ${count === 1 ? "copy" : "copies"}.`),
  cutCable: (a: string, b: string, aName: string, bName: string, oneWay: boolean) =>
    run(
      () => postJson<FaultList>(sup("/chaos/link"), { a, b, cut: true, direction: oneWay ? "a_to_b" : "both" }),
      `You cut the cable between ${aName} and ${bName}${oneWay ? " (one way)" : ""}.`,
    ),
  reconnect: (a: string, b: string, aName: string, bName: string) =>
    run(() => postJson<FaultList>(sup("/chaos/link"), { a, b, cut: false, direction: "both" }), `You reconnected ${aName} and ${bName}.`),
  slow: (pid: string, name: string, ms: number) =>
    run(
      () => postJson<FaultList>(sup(`/chaos/node/${pid}`), { action: "slow", params: { ms } }),
      ms ? `You slowed ${name} by ${ms} ms.` : `${name} is back to normal speed.`,
    ),
  freeze: (pid: string, name: string, seconds: number) =>
    run(() => postJson<FaultList>(sup(`/chaos/node/${pid}`), { action: "freeze", params: { seconds } }), `You froze ${name} for ${seconds} s.`),
  diskFull: (pid: string, name: string, on: boolean) =>
    run(
      () => postJson<FaultList>(sup(`/chaos/node/${pid}`), { action: "disk_full", params: { on } }),
      on ? `You filled up ${name}'s disk.` : `${name}'s disk has space again.`,
    ),
  powerCut: (label: string | null, what: string, restoreAfter: number | null) =>
    run(
      () =>
        postJson<PowerCutResult>(sup("/power/cut"), {
          scope: label ? "label" : "all",
          label,
          restore_after_s: restoreAfter,
        }),
      label ? `You cut ${what}.` : "You cut the power to everything.",
    ),
  powerRestore: (label: string | null, what: string) =>
    run(() => postJson(sup("/power/restore"), { scope: label ? "label" : "all", label }), `You turned the power back on for ${what}.`),
  movePlug: (nodeId: string, name: string, labels: Record<string, string>, strip: string) =>
    run(
      () => patchJson(`${META_URL}/v1/nodes/${nodeId}/labels`, { labels: { ...labels, power: strip } }),
      `You moved ${name} to Power Strip ${strip}.`,
    ),
  addMachine: (name: string, labels: Record<string, string>) =>
    run(() => postJson<Proc>(sup("/nodes/add"), { display_name: name, labels }), `You added ${name}.`),
  rename: (pid: string, oldName: string, name: string) =>
    run(() => postJson<Proc>(sup(`/nodes/${pid}/rename`), { display_name: name }), `You renamed ${oldName} to ${name}.`),
  /** Hub address and next free id for the "Real laptop" join command (LAN mode). Null if unreachable. */
  clusterInfo: () => getJson<ClusterInfo>(sup("/cluster/info")).catch(() => null),
  clearAll: () => run(() => postJson<FaultList>(sup("/chaos/clear_all")), "You fixed every cable and speed problem."),
};

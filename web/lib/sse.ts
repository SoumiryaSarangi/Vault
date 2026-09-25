// One SSE connection → the zustand store (TECH_STACK §6.8). Owner: Anushka.
import { META_URL } from "./api";
import { useVault } from "./store";

export function connectStream(url = `${META_URL}/v1/stream`) {
  const es = new EventSource(url);
  es.addEventListener("snapshot", (e) => useVault.getState().setSnapshot(JSON.parse((e as MessageEvent).data)));
  es.addEventListener("event", (e) => useVault.getState().pushEvent(JSON.parse((e as MessageEvent).data)));
  es.onerror = () => useVault.getState().setConnected(false); // EventSource retries by itself
  es.onopen = () => useVault.getState().setConnected(true);
  return () => es.close();
}

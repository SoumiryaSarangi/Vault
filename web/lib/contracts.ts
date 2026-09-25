// Vault API contract v1: TypeScript mirror of backend/vault/common/models.py.
// FROZEN. Owner: Anushka. Same field names, snake_case kept as-is. Change both files together (gated).
// Mirrors the BROWSER-FACING subset only: what the dashboard reads or sends. Node, gateway and metadata
// internals (fragments, uploads, heartbeats, pulls, inventory, ledger) live only in models.py.

// ── Enums ──
export type NodeState =
  | "JOINING" | "ALIVE" | "SUSPECT" | "PARTITIONED" | "DOWN" | "DEAD" | "REJOINING" | "DRAINING" | "RETIRED";
export type FragState = "pending" | "ok" | "incoming" | "corrupt" | "missing" | "lost" | "trim";
export type JobKind = "repair" | "move" | "trim";
export type JobReason =
  | "under_replicated" | "corrupt" | "missing" | "fate" | "rebalance" | "drain"
  | "over_replicated" | "orphan" | "superseded" | "aborted";
export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";
export type Severity = "info" | "success" | "warn" | "danger";
export type Mode = "vault" | "naive";
export type PolicyName = "rep2" | "rep3" | "ec42";
export type SummaryLevel = "ok" | "degraded" | "at_risk" | "critical";

// ── Common ──
export interface ErrorBody { error: string; message: string; detail: Record<string, unknown> }
export interface Policy { name: string; type: "replication" | "erasure"; n: number; w: number; k?: number | null; m?: number | null }
export interface DomainRef { key: string; value: string }
export interface Reach { ok: boolean; rtt_ms?: number | null }

// ── 7.1 Gateway ──
export interface BucketInfo { bucket: string; policy: string }
export interface ObjectListItem { key: string; size: number; etag: string; seq: number; commit_seq: number; policy: string; updated_at: number }
export interface ObjectList { bucket: string; objects: ObjectListItem[] }
export interface PutResult {
  bucket: string; key: string; version_id: string; seq: number; commit_seq: number; etag: string; size: number; policy: string;
  stored: { chunks: number; fragments_acked: number; w: number; relayed: number };
}
export interface DeleteResult { deleted: boolean; commit_seq: number | null }
export interface ServiceHealth { pid: string; ok: boolean; config_version: number }

// ── 7.2 Metadata ──
export interface Bucket { name: string; policy: Policy }
export interface BucketList { buckets: Bucket[] }
export interface ModeResult { mode: Mode; config_version: number }
export interface FragLoc {
  frag_idx: number; node_id: string; addr: string; state: FragState; sha256?: string | null; route: string; slow: boolean;
}
export interface ChunkHealth { idx: number; needed: number; durable: number; available: number; fragments: FragLoc[] }
export interface ObjectHealth { ifl: number; target: number; chunks: ChunkHealth[]; shared: DomainRef[]; min_cut: DomainRef[] }
export interface Job {
  id: number; kind: JobKind; reason: JobReason; chunk_id?: string | null; frag_idx?: number | null;
  source_node?: string | null; target_node?: string | null; priority: number; state: JobState; attempts: number;
  bytes: number; error?: string | null; incident_id?: number | null; created_at: number; started_at?: number | null; finished_at?: number | null;
}
export interface RepairStatus { queued: Record<string, number>; active: Job[]; recent: Job[] }
export interface Incident {
  id: number; kind: string; subject?: string | null; fault_at?: number | null; detected_at?: number | null;
  repair_started_at?: number | null; recovered_at?: number | null; affected_chunks: number; remaining_chunks: number; bytes_repaired: number;
}
export interface IncidentList { incidents: Incident[] }
export interface InspectObject {
  bucket: string; key: string; state: "live" | "deleted"; seq: number; commit_seq: number; sha256?: string | null;
  size: number; durable_min: number; target: number; ifl: number;
}
export interface InspectPage { objects: InspectObject[]; next_cursor: string | null }
export interface ModeRequest { mode: Mode }
export interface LabelsPatch { labels: Record<string, string>; display_name?: string | null }

// ── 7.3 Node chaos state ──
export interface ChaosState {
  pid: string; blocked_out: string[]; blocked_in: string[]; delay_ms: number; frozen_until: number; node_faults: Record<string, unknown>;
}

// ── 7.4 Supervisor ──
export interface Proc { pid: string; port: number; state: "running" | "stopped"; os_pid?: number | null; uptime_s: number; display_name: string }
export interface ProcList { procs: Proc[] }
export interface PowerCutRequest { scope: "all" | "label"; label?: string | null; restore_after_s?: number | null }
export interface PowerCutResult { killed: string[] }
export interface PowerRestoreResult { started: string[] }
export interface CorruptResult { corrupted: string[] }
export interface LinkRequest { a: string; b: string; cut: boolean; direction: "both" | "a_to_b" }
export interface NodeChaosRequest { action: "slow" | "freeze" | "corrupt" | "disk_full" | "clear"; params: Record<string, unknown> }
export interface Fault { id: string; kind: string; subject: string; since: number; params: Record<string, unknown> }
export interface FaultList { faults: Fault[] }
export interface AddNodeRequest { display_name: string; labels: Record<string, string> }
export interface ChaosStep {
  t: number;
  action: "kill" | "start" | "restart_down" | "corrupt" | "link" | "slow" | "freeze" | "power_cut" | "power_restore" | "clear";
  params: Record<string, unknown>;
}
export interface ResetRequest { seed: boolean; mode: Mode }
export interface ResetResult { ok: boolean; elapsed_s: number }
export interface SeedRequest { bucket: string; count: number }
export interface ScriptRequest { name: "standard" | "heavy"; seed: number }
export interface ScriptStarted { script_id: string }
export interface RunCreated { run_id: string }
export interface CorruptRequest { count?: number | null; fids?: string[] | null; mode: "bitflip" | "zero" | "truncate" | "delete" }
export interface PowerRestoreRequest { scope: "all" | "label"; label?: string | null }
export interface SeedResult { uploaded: number }

// ── 7.5 Oracle ──
export type RunState = "preparing" | "running" | "settling" | "verifying" | "done" | "failed";
export type ViolationKind = "lost" | "damaged" | "resurrected" | "stale_read" | "phantom_read";
export interface RunRequest {
  mode: Mode; seed: number; duration_s: number; clients: number; keyspace: number; chaos_script: "standard" | "heavy";
}
export interface RunCounters {
  ops: number; acked_puts: number; acked_deletes: number; reads: number; unknown: number; failed: number; faults_injected: number;
}
export interface RunViolations { lost: number; damaged: number; resurrected: number; stale_read: number; phantom_read: number }
export interface RunHealth { under_protected: number }
export interface ViolationSample { kind: ViolationKind; key: string; human: string; technical: string }
export interface TimelineMark { t: number; kind: "chaos" | "violation"; label: string }
export interface RunStatus {
  run_id: string; mode: Mode; seed: number; state: RunState; started_at: number; elapsed_s: number;
  counters: RunCounters; violations: RunViolations; health: RunHealth; samples: ViolationSample[]; timeline: TimelineMark[];
}
export interface RunSummary {
  run_id: string; mode: Mode; seed: number; state: RunState; started_at: number; elapsed_s: number; chaos_script: string;
  counters: RunCounters; violations: RunViolations; health: RunHealth;
}
export interface RunList { runs: RunSummary[] }

// ── 8.1 Events ──
export interface VaultEvent {
  id: number; ts: number; type: string; severity: Severity; subject: Record<string, unknown>;
  human: string; technical: string; data: Record<string, unknown>;
}
export interface EventList { events: VaultEvent[] }

// ── 8.2 Snapshot ──
export interface Summary {
  level: SummaryLevel; human: string; technical: string; files: number; logical_bytes: number; raw_bytes: number;
  overhead: number; under_replicated_chunks: number; at_risk_files: number; unreadable_files: number; min_ifl: number;
}
export interface NodeView {
  id: string; display_name: string; addr: string; state: NodeState; phi: number; route: string; slow: boolean; fenced: boolean;
  epoch: number; labels: Record<string, string>; disk_used: number; capacity: number; fragments: number; faults: string[];
}
export interface ControlView { id: string; ok: boolean }
export interface LinkView { a: string; b: string; a_to_b: boolean; b_to_a: boolean; relay?: string | null }
export interface ActiveJob { job_id: number; kind: JobKind; reason: JobReason; src?: string | null; dst?: string | null; bytes: number; progress: number }
export interface RepairView { queued: Record<string, number>; active: ActiveJob[]; mbps: number }
export interface TrafficView { puts_per_s: number; gets_per_s: number }
export interface Snapshot {
  ts: number; mode: Mode; config_version: number; summary: Summary; nodes: NodeView[]; control: ControlView[];
  links: LinkView[]; repair: RepairView; incident: Incident | null; traffic: TrafficView;
}

// ── 8.3 Metrics, fate ──
export interface LastIncident { id: number; detect_s?: number | null; grace_s?: number | null; repair_s?: number | null; mttr_s?: number | null; bytes: number }
export interface Metrics {
  availability_60s: number; readable_now_pct: number;
  overhead: { cluster: number; by_policy: Record<string, number> };
  last_incident: LastIncident | null; incidents_avg_mttr_s: number | null; unnecessary_repairs_avoided: number;
  repair: { bytes_total: number; mbps_now: number };
  rebalance: { last: { moved_fraction: number; ideal_fraction: number } | null };
  ifl: { histogram: Record<string, number>; min: number; at_risk_files: number };
  scrub: { checked_last_pass: number; corrupt_found_total: number };
}
export interface FateDomain { key: string; value: string; nodes: string[]; cluster_wide: boolean }
export interface ClusterWideRisk { key: string; value: string; human: string }
export interface AtRiskFile { bucket: string; key: string; ifl: number; target: number; shared: DomainRef[] }
export interface Advice { human: string; technical: string }
export interface FateReport {
  fate_keys: string[]; domains: FateDomain[]; cluster_wide: ClusterWideRisk[];
  files: { histogram: Record<string, number>; at_risk: AtRiskFile[] }; advice: Advice[]; last_audit_at: number | null;
}

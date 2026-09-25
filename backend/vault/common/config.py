"""Load and validate vault.yaml (ARCHITECTURE §9). Owner: Anushka (shared).

Usage:
    from vault.common.config import load_config
    cfg = load_config()                  # VAULT_CONFIG or ./vault.yaml; VAULT_DEMO=1 → dead_after_s 8
    cfg.policy("rep3").n                 # 3
    cfg.addr("n3")                       # "127.0.0.1:7103"
"""
import json
import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vault.common.models import Policy


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ports(_C):
    supervisor: int = 7070
    metadata: int = 7000
    gateway: int = 7080
    oracle: int = 7090
    node_base: int = 7101


class ClusterCfg(_C):
    data_dir: str = "./data"
    logs_dir: str = "./logs"
    host: str = "127.0.0.1"
    ports: Ports = Field(default_factory=Ports)
    chunk_size: int = 1048576
    node_capacity_bytes: int = 2147483648
    default_buckets: dict[str, str] = Field(default_factory=dict)


class NodeCfg(_C):
    display_name: str
    labels: dict[str, str] = Field(default_factory=dict)
    addr: Optional[str] = None      # default: host:node_base+i (P2 multi-laptop: set a real address here)


class PolicyCfg(_C):
    type: Literal["replication", "erasure"]
    n: Optional[int] = None
    w: int
    k: Optional[int] = None
    m: Optional[int] = None


class FateCfg(_C):
    keys: list[str] = Field(default_factory=lambda: ["power", "switch", "disk_batch", "version"])
    improve_max_node_fill: float = 0.85
    improve_max_imbalance: float = 0.20


class PlacementCfg(_C):
    vnodes_per_node: int = 128


class DetectorCfg(_C):
    heartbeat_ms: int = 500
    ping_ms: int = 1000
    window: int = 100
    min_std_ms: int = 200
    phi_suspect: float = 8.0
    confirm_ms: int = 1000
    dead_after_s: float = 15
    lease_ttl_ms: int = 5000
    startup_grace_s: float = 10
    slow_rtt_ms: int = 300


class RepairCfg(_C):
    scan_ms: int = 500
    max_concurrent: int = 8
    per_node: int = 2
    bandwidth_mbps: float = 50
    max_attempts: int = 3


class ScrubCfg(_C):
    rate_mbps: float = 20
    recent_first_minutes: float = 5


class GcCfg(_C):
    interval_s: float = 5
    upload_timeout_s: float = 60
    superseded_grace_s: float = 30
    orphan_grace_s: float = 60


class InventoryCfg(_C):
    interval_s: float = 30


class GatewayCfg(_C):
    chunks_in_flight: int = 2
    spares: int = 2
    hedge_ms: int = 150
    timeout_control_s: float = 1.0
    timeout_data_s: float = 10.0


class SafetyCfg(_C):
    verify_on_receive: bool = True
    verify_on_read: bool = True
    durable_writes: bool = True
    quorum_writes: bool = True
    read_failover: bool = True
    repair: bool = True
    scrub: bool = True
    fate_aware_placement: bool = True
    fencing: bool = True
    relay: bool = True

    @classmethod
    def for_mode(cls, mode: str) -> "SafetyCfg":
        on = mode == "vault"
        return cls(**{name: on for name in cls.model_fields})


class StorageCfg(_C):
    sim_page_cache: bool = False
    dirty_expire_ms: int = 5000


class ChaosCfg(_C):
    enabled: bool = True


class OracleCfg(_C):
    clients: int = 8
    keyspace: int = 200
    duration_s: int = 60
    settle_max_s: int = 60
    seed: int = 42
    script: str = "standard"


class WebCfg(_C):
    origin: str = "http://localhost:3000"


class VaultConfig(_C):
    cluster: ClusterCfg = Field(default_factory=ClusterCfg)
    nodes: dict[str, NodeCfg] = Field(default_factory=dict)
    policies: dict[str, PolicyCfg] = Field(default_factory=dict)
    fate: FateCfg = Field(default_factory=FateCfg)
    placement: PlacementCfg = Field(default_factory=PlacementCfg)
    detector: DetectorCfg = Field(default_factory=DetectorCfg)
    repair: RepairCfg = Field(default_factory=RepairCfg)
    scrub: ScrubCfg = Field(default_factory=ScrubCfg)
    gc: GcCfg = Field(default_factory=GcCfg)
    inventory: InventoryCfg = Field(default_factory=InventoryCfg)
    gateway: GatewayCfg = Field(default_factory=GatewayCfg)
    safety: SafetyCfg = Field(default_factory=SafetyCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    chaos: ChaosCfg = Field(default_factory=ChaosCfg)
    oracle: OracleCfg = Field(default_factory=OracleCfg)
    web: WebCfg = Field(default_factory=WebCfg)

    @model_validator(mode="after")
    def _validate(self) -> "VaultConfig":
        for name in self.policies:
            p = self.policy(name)
            if p.w > p.n:
                raise ValueError(f"policy {name}: w={p.w} > n={p.n}")
            if p.w < p.needed:
                raise ValueError(f"policy {name}: w={p.w} < needed={p.needed}")
        if self.detector.lease_ttl_ms >= self.detector.dead_after_s * 1000:
            raise ValueError("detector.lease_ttl_ms must be < dead_after_s * 1000 (§4.6 fencing)")
        return self

    # ── helpers ──
    def policy(self, name: str) -> Policy:
        c = self.policies[name]
        if c.type == "erasure":
            assert c.k is not None and c.m is not None, f"erasure policy {name} needs k and m"
            return Policy(name=name, type="erasure", k=c.k, m=c.m, n=c.k + c.m, w=c.w)
        assert c.n is not None, f"replication policy {name} needs n"
        return Policy(name=name, type="replication", n=c.n, w=c.w)

    def node_ids(self) -> list[str]:
        return list(self.nodes)

    def port(self, pid: str) -> int:
        """Port for a participant id: meta, gw, sup, oracle, n1..nN (n7+ = added machines)."""
        p = self.cluster.ports
        fixed = {"meta": p.metadata, "gw": p.gateway, "sup": p.supervisor, "oracle": p.oracle}
        if pid in fixed:
            return fixed[pid]
        if pid.startswith("n") and pid[1:].isdigit():
            return p.node_base + int(pid[1:]) - 1
        raise KeyError(f"unknown participant id {pid!r}")

    def addr(self, pid: str) -> str:
        """host:port for a participant. A node's explicit `addr` in vault.yaml wins."""
        node = self.nodes.get(pid)
        if node is not None and node.addr:
            return node.addr
        return f"{self.cluster.host}:{self.port(pid)}"

    def url(self, pid: str) -> str:
        return f"http://{self.addr(pid)}"

    def data_path(self, *parts: str) -> Path:
        return Path(self.cluster.data_dir, *parts)


CONTROL_DISPLAY_NAMES = {"meta": "Vault index", "gw": "Vault gateway"}   # DESIGN §5.8

# A machine added at runtime (n7, n8, …) isn't in vault.yaml. The supervisor passes its identity
# to the node process through these env vars; nodes read it with node_identity().
ENV_NODE_DISPLAY_NAME = "VAULT_NODE_DISPLAY_NAME"
ENV_NODE_LABELS = "VAULT_NODE_LABELS"   # JSON object


def node_identity(cfg: "VaultConfig", node_id: str) -> tuple[str, dict[str, str]]:
    """(display_name, labels) for a node: from vault.yaml, else from the supervisor's env vars."""
    n = cfg.nodes.get(node_id)
    if n is not None:
        return n.display_name, dict(n.labels)
    labels = json.loads(os.environ.get(ENV_NODE_LABELS, "{}") or "{}")
    return os.environ.get(ENV_NODE_DISPLAY_NAME, node_id), {str(k): str(v) for k, v in labels.items()}


def load_config(path: Optional[str] = None) -> VaultConfig:
    path = path or os.environ.get("VAULT_CONFIG", "vault.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if os.environ.get("VAULT_DEMO") == "1":
        raw.setdefault("detector", {})["dead_after_s"] = 8
    return VaultConfig.model_validate(raw)

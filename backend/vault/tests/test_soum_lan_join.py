"""Owner: Soum. LAN mode on the hub's supervisor: laptops join through their node agent, turn off/on goes to the
agent, a sleeping laptop reads as stopped, rename (kept across reset), and the agent's own join/wipe.
No real processes: rpc is replaced by a fake network of agents and a metadata stub."""
import json

import pytest

from vault.common.config import load_config
from vault.common.models import AgentStatus, JoinRequest
from vault.common.netsim import NetSim
from vault.common.rpc import NetworkError, get_rpc, init_rpc
from vault.common.service import VaultHTTPError
from vault.node.soum_agent import Agent
from vault.supervisor.anushka_chaos_ctl import ChaosController
from vault.supervisor.anushka_cluster import ClusterOps
from vault.supervisor.anushka_procs import ASLEEP, Procs


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeNet:
    """agents: host → {"node_id", "running", "up"}; meta keeps labels/names; calls are recorded."""

    def __init__(self):
        self.agents: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.meta_nodes = {"n1": {"id": "n1", "labels": {"power": "A"}, "display_name": "Reception PC"}}

    def laptop(self, host, node_id, running=False):
        self.agents[host] = {"node_id": node_id, "running": running, "up": True}

    async def request(self, target, method, path, **kw):
        self.calls.append((target, method, path))
        rpc = get_rpc()
        if target == "meta":
            if path == "/v1/cluster":
                return _Resp(200, {"nodes": list(self.meta_nodes.values())})
            if method == "PATCH":
                node_id = path.split("/")[3]
                self.meta_nodes.setdefault(node_id, {"id": node_id})
                self.meta_nodes[node_id].update(labels=kw["json"]["labels"], display_name=kw["json"]["display_name"])
            return _Resp(200, {})
        if target.startswith("agent:"):
            host = rpc.addr(target).split(":")[0]
            a = self.agents.get(host)
            if a is None or not a["up"]:
                raise NetworkError(target, "ConnectTimeout")
            if path == "/agent/node/start":
                a["running"] = True
            elif path == "/agent/node/stop":
                a["running"] = False
            return _Resp(200, AgentStatus(node_id=a["node_id"], running=a["running"], host=host).model_dump())
        raise NetworkError(target, "not in the fake network")


@pytest.fixture
async def hub(monkeypatch, tmp_path):
    cfg = load_config("vault.lan.yaml")
    cfg.cluster.logs_dir = str(tmp_path)
    init_rpc("sup", cfg, NetSim("sup"))
    net = FakeNet()
    monkeypatch.setattr(get_rpc(), "request", net.request)
    procs = Procs(cfg)
    chaos = ChaosController(cfg, procs)
    ops = ClusterOps(cfg, procs, chaos)
    yield ops, procs, chaos, net
    await get_rpc().close()


def join_req(host, node_id="n2", name="Doctor's Desk", strip="B"):
    return JoinRequest(node_id=node_id, display_name=name, labels={"power": strip}, host=host, agent_port=7071)


# ── join ──

async def test_join_registers_remote_machine_and_its_addresses(hub):
    ops, procs, _, net = hub
    net.laptop("192.168.1.102", "n2", running=False)
    res = await ops.join(join_req("192.168.1.102"))
    assert res.new and res.node_id == "n2" and res.port == 7102 and res.display_name == "Doctor's Desk"
    assert get_rpc().addr("n2") == "192.168.1.102:7102" and get_rpc().addr("agent:n2") == "192.168.1.102:7071"
    v = procs.view("n2")
    assert v.remote and v.host == "192.168.1.102" and v.state == "stopped" and v.note is None
    assert "n2" in procs.node_ids() and ("meta", "POST", "/v1/incidents/fault") in net.calls


async def test_join_again_is_quiet_and_keeps_the_hub_name(hub):
    ops, procs, _, net = hub
    net.laptop("192.168.1.102", "n2")
    await ops.join(join_req("192.168.1.102"))
    procs.children["n2"].display_name = "Renamed"
    ops.renames["n2"] = "Renamed"
    net.calls.clear()
    res = await ops.join(join_req("192.168.1.102"))
    assert not res.new and res.display_name == "Renamed"
    assert ("meta", "POST", "/v1/incidents/fault") not in net.calls


async def test_join_refuses_hub_machine_id_and_unreachable_laptop(hub):
    ops, _, _, net = hub
    net.laptop("192.168.1.102", "n1")
    with pytest.raises(VaultHTTPError) as e:
        await ops.join(join_req("192.168.1.102", node_id="n1"))
    assert e.value.status == 409 and "n2" in e.value.body.message
    with pytest.raises(VaultHTTPError) as e:           # firewall: the hub can't call the agent back
        await ops.join(join_req("192.168.1.109", node_id="n9"))
    assert e.value.status == 502 and "Windows Firewall" in e.value.body.message
    with pytest.raises(VaultHTTPError) as e:
        await ops.join(join_req("192.168.1.102", node_id="x1"))
    assert e.value.status == 400


async def test_same_id_from_second_laptop_refused_but_ip_change_accepted(hub):
    ops, procs, _, net = hub
    net.laptop("192.168.1.102", "n2")
    await ops.join(join_req("192.168.1.102"))
    net.laptop("192.168.1.103", "n2")
    with pytest.raises(VaultHTTPError) as e:           # the first laptop still answers as n2
        await ops.join(join_req("192.168.1.103"))
    assert e.value.status == 409 and "--id n3" in e.value.body.message
    net.agents["192.168.1.102"]["up"] = False           # same laptop, new DHCP address
    res = await ops.join(join_req("192.168.1.103"))
    assert not res.new and procs.children["n2"].host == "192.168.1.103"
    assert get_rpc().addr("n2") == "192.168.1.103:7102"


# ── turn off / on, sleeping laptops, power cut ──

async def test_turn_on_off_goes_through_the_agent(hub):
    ops, procs, chaos, net = hub
    net.laptop("192.168.1.102", "n2")
    await ops.join(join_req("192.168.1.102"))
    assert (await chaos.start_proc("n2")).state == "running"
    assert ("agent:n2", "POST", "/agent/node/start") in net.calls
    assert (await chaos.kill_proc("n2")).state == "stopped"
    assert ("agent:n2", "POST", "/agent/node/stop") in net.calls and not net.agents["192.168.1.102"]["running"]


async def test_sleeping_laptop_reads_stopped_and_cant_be_started(hub):
    ops, procs, chaos, net = hub
    net.laptop("192.168.1.102", "n2", running=True)
    await ops.join(join_req("192.168.1.102"))
    assert procs.children["n2"].running
    net.agents["192.168.1.102"]["up"] = False           # lid closed
    await procs.poll_one(procs.children["n2"])
    v = procs.view("n2")
    assert v.state == "stopped" and v.note == ASLEEP
    with pytest.raises(VaultHTTPError) as e:
        await chaos.start_proc("n2")
    assert e.value.status == 503 and "Wake" in e.value.body.message


async def test_kill_many_skips_a_laptop_that_fell_asleep(hub):
    ops, procs, _, net = hub
    net.laptop("192.168.1.102", "n2", running=True)
    net.laptop("192.168.1.103", "n3", running=True)
    await ops.join(join_req("192.168.1.102"))
    await ops.join(join_req("192.168.1.103", node_id="n3", strip="C"))
    net.agents["192.168.1.103"]["up"] = False
    assert await procs.kill_many(["n2", "n3"]) == ["n2"]


async def test_reset_keeps_remote_machines_drops_simulated_ones(hub):
    ops, procs, _, net = hub
    net.laptop("192.168.1.102", "n2")
    await ops.join(join_req("192.168.1.102"))
    sim = procs.add_node("Storeroom PC", {"power": "A"})
    assert procs.drop_added_nodes() == [sim] and "n2" in procs.children


# ── rename ──

async def test_rename_updates_metadata_supervisor_and_survives(hub):
    ops, procs, _, net = hub
    v = await ops.rename("n1", "  Front   Desk ")
    assert v.display_name == "Front Desk" and ops.renames == {"n1": "Front Desk"}
    assert net.meta_nodes["n1"]["display_name"] == "Front Desk" and net.meta_nodes["n1"]["labels"] == {"power": "A"}
    assert ops.chaos.name("n1") == "Front Desk"
    for bad in ("", "x" * 41):
        with pytest.raises(VaultHTTPError) as e:
            await ops.rename("n1", bad)
        assert e.value.status == 400
    with pytest.raises(VaultHTTPError) as e:
        await ops.rename("meta", "Index")
    assert e.value.status == 404


async def test_cluster_info_for_the_dashboard(hub, monkeypatch):
    ops, _, _, _ = hub
    monkeypatch.setenv("VAULT_HUB", "192.168.1.101")
    info = ops.info()
    assert info.lan and info.agent_port == 7071 and info.next_node_id == "n2"


# ── the agent on the joining laptop ──

async def test_agent_joins_takes_hub_name_and_wipes_only_when_stopped(monkeypatch, tmp_path):
    cfg = load_config("vault.lan.yaml")
    cfg.cluster.data_dir, cfg.cluster.logs_dir = str(tmp_path / "data"), str(tmp_path / "logs")
    init_rpc("agent-n2", cfg, NetSim("agent-n2"))
    agent = Agent(cfg, "n2", "Laptop 2", {"power": "B"}, "127.0.0.1", 7071)
    assert agent.host == "127.0.0.1" and agent.port == 7102
    assert agent.procs._pidfile.name == "agent-n2.json" and list(agent.procs.children) == ["n2"]
    sent = []

    async def fake_request(target, method, path, **kw):
        sent.append((target, path, kw["json"]))
        return _Resp(200, {"node_id": "n2", "port": 7102, "display_name": "Doctor's Desk", "labels": {}, "new": True})
    monkeypatch.setattr(get_rpc(), "request", fake_request)
    res = await agent.join_once()
    assert res.display_name == "Doctor's Desk" and agent.child.display_name == "Doctor's Desk"
    assert sent[0][0] == "sup" and sent[0][1] == "/nodes/join" and sent[0][2]["host"] == "127.0.0.1"

    d = cfg.data_path("n2")
    d.mkdir(parents=True)
    (d / "x.blk").write_bytes(b"x")
    assert not agent.status().running
    await agent.wipe()
    assert not d.exists()
    await get_rpc().close()

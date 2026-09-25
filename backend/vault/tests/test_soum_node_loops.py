"""Owner: Soum (S4). Heartbeat reply handling (lease only from non-DEAD replies, measured from send time),
GET /v1/config parsing, pull (copy + ec_rebuild, bad sources reported, 424, fenced, idempotent), scrubber loop."""
import asyncio
import os
import time

import pytest

from vault.common import ids
from vault.common.config import SafetyCfg, load_config
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentHeader, HeartbeatReply, NodeState, PullEc, PullRequest, PullSource, Reach
from vault.common.netsim import NetSim
from vault.common.rpc import NetworkError, init_rpc
from vault.common.service import VaultHTTPError
from vault.common.soum_ec import ec_encode
from vault.node import soum_scrubber
from vault.node.soum_app import Node
from vault.node.soum_heartbeat import HeartbeatLoop, parse_config
from vault.node.soum_pull import pull
from vault.node.soum_storage import encode_meta


def header(payload: bytes, frag_idx: int = 0, vid: str = "", chunk: bytes = b"", policy="rep3") -> FragmentHeader:
    vid = vid or ids.new_version_id()
    cid = ids.chunk_id(vid, 0)
    chunk = chunk or payload
    return FragmentHeader(fid=ids.fid(cid, frag_idx), chunk_id=cid, frag_idx=frag_idx, version_id=vid,
                          bucket="clinic", key="k", policy=policy, chunk_sha256=sha256_hex(chunk),
                          frag_sha256=sha256_hex(payload), chunk_size=len(chunk), frag_size=len(payload), epoch=1)


@pytest.fixture
async def node(tmp_path):
    cfg = load_config("vault.yaml")
    cfg.cluster.data_dir = str(tmp_path)
    init_rpc("n1", cfg, NetSim("n1"))
    n = Node(cfg, "n1")
    await n.startup()
    n.epoch, n.lease_expiry = 1, time.time() + 60
    n.reports = []
    n.report = lambda fid, problem, context, node_id=None: n.reports.append((fid, node_id or "n1", problem, context))
    yield n
    await n.close()


def fake_fetch(table: dict[str, tuple[int, bytes, dict]]):
    """fid → (status, body, headers); missing fid → NetworkError."""
    async def fetch(src: PullSource):
        if src.fid not in table:
            raise NetworkError(src.node_id, "refused")
        return table[src.fid]
    return fetch


# ── config ──

def test_parse_config_pops_version_and_mode():
    cfg = load_config("vault.yaml")
    body = cfg.model_dump()
    body["safety"]["scrub"] = False
    body.update(config_version=12, mode="vault")
    full, safety, version = parse_config(body)
    assert version == 12 and full is not None and safety.scrub is False and safety.repair is True


def test_parse_config_falls_back_to_safety_only():
    full, safety, version = parse_config({"config_version": 3, "mode": "naive", "safety": SafetyCfg.for_mode("naive").model_dump(),
                                          "something_new": 1})
    assert full is None and version == 3 and not any(safety.model_dump().values())


# ── heartbeat replies ──

async def test_alive_reply_grants_lease_from_send_time(node):
    hb = HeartbeatLoop(node)
    node.config_version = 5                            # same as the reply: no config fetch
    sent_at = time.time() - 2
    await hb.handle_reply(HeartbeatReply(state=NodeState.ALIVE, epoch=4, lease_ttl_ms=5000, config_version=5), sent_at)
    assert node.epoch == 4 and node.lease_expiry == pytest.approx(sent_at + 5)
    assert (await node.storage.read_node_json())["epoch"] == 4


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


async def test_config_refetched_when_version_differs_even_lower(node, monkeypatch):
    """A restarted metadata may count config_version from 0 again: a LOWER version must refetch too."""
    from vault.common.rpc import get_rpc
    calls = []
    body = load_config("vault.yaml").model_dump()
    body["safety"] = SafetyCfg.for_mode("naive").model_dump()

    async def fake_request(target, method, path, **kw):
        calls.append(path)
        return _Resp(200, {**body, "config_version": 2, "mode": "naive"})
    monkeypatch.setattr(get_rpc(), "request", fake_request)
    hb = HeartbeatLoop(node)
    node.config_version = 7
    await hb.maybe_fetch_config(7)
    assert calls == []                                   # same version: nothing to do
    await hb.maybe_fetch_config(2)
    assert calls == ["/v1/config"] and node.config_version == 2 and node.safety.fencing is False
    assert get_rpc().relay_enabled is False and node.app_state is None


async def test_dead_reply_never_extends_lease(node):
    hb = HeartbeatLoop(node)
    hb.registered = True
    before = node.lease_expiry = time.time() + 1
    await hb.handle_reply(HeartbeatReply(state=NodeState.DEAD, epoch=9, lease_ttl_ms=5000, config_version=0),
                          time.time())
    assert hb.registered is False and node.lease_expiry == before and node.epoch == 1


async def test_heartbeat_payload(node):
    payload = b"x" * 100
    await node.storage.put(header(payload), payload)
    node.reach = {"meta": Reach(ok=False), "n2": Reach(ok=True, rtt_ms=1.5)}
    hb = HeartbeatLoop(node).build_heartbeat()
    assert hb.node_id == "n1" and hb.epoch == 1 and hb.fragments == 1 and hb.disk_used > 100
    assert hb.fenced is False and hb.reach["n2"].rtt_ms == 1.5 and hb.reach["meta"].ok is False
    node.lease_expiry = time.time() - 1
    assert HeartbeatLoop(node).build_heartbeat().fenced is True


# ── pull: copy ──

async def test_pull_copy_skips_missing_and_bad_sources(node):
    payload = os.urandom(5000)
    h = header(payload, frag_idx=2)
    bad = bytearray(payload); bad[0] ^= 1
    table = {"f_bad": (200, bytes(bad), {}), "f_ok": (200, payload, {}), "f_404": (404, b"", {})}
    req = PullRequest(fid=h.fid, header=h, expected_sha256=h.frag_sha256, mode="copy",
                      sources=[PullSource(node_id="n4", addr="", fid="f_down"),
                               PullSource(node_id="n5", addr="", fid="f_404"),
                               PullSource(node_id="n2", addr="", fid="f_bad"),
                               PullSource(node_id="n3", addr="", fid="f_ok")])
    res = await pull(node, req, fetch=fake_fetch(table))
    assert res.source_used == "n3" and res.sha256 == h.frag_sha256 and res.size == len(payload)
    assert await node.storage.get(h.fid) == (h, payload)
    assert node.reports == [("f_bad", "n2", "corrupt", "repair")]


async def test_pull_no_valid_source(node):
    payload = b"data"
    h = header(payload)
    req = PullRequest(fid=h.fid, header=h, expected_sha256=h.frag_sha256, mode="copy",
                      sources=[PullSource(node_id="n2", addr="", fid="gone")])
    with pytest.raises(VaultHTTPError) as e:
        await pull(node, req, fetch=fake_fetch({}))
    assert e.value.status == 424 and e.value.body.error == "no_valid_source"
    assert not node.storage.has(h.fid)


async def test_pull_refused_when_fenced(node):
    node.lease_expiry = None
    h = header(b"d")
    req = PullRequest(fid=h.fid, header=h, expected_sha256=h.frag_sha256, mode="copy", sources=[])
    with pytest.raises(VaultHTTPError) as e:
        await pull(node, req, fetch=fake_fetch({}))
    assert e.value.status == 503


async def test_pull_idempotent_when_already_stored(node):
    payload = b"already here"
    h = header(payload)
    await node.storage.put(h, payload)
    req = PullRequest(fid=h.fid, header=h, expected_sha256=h.frag_sha256, mode="copy", sources=[])
    res = await pull(node, req, fetch=fake_fetch({}))
    assert res.source_used == "local"


# ── pull: ec_rebuild ──

async def test_pull_ec_rebuild_with_one_damaged_source(node):
    chunk = os.urandom(1_000_003)
    frags = ec_encode(chunk, 4, 2)
    vid = ids.new_version_id()
    hdrs = [header(f, i, vid=vid, chunk=chunk, policy="ec42") for i, f in enumerate(frags)]
    table = {}
    for i, (h, f) in enumerate(zip(hdrs, frags)):
        body = f if i != 1 else b"\x00" + f[1:]         # fragment 1 is damaged in transit
        table[h.fid] = (200, body, {"x-vault-meta": encode_meta(h), "x-vault-sha256": h.frag_sha256})
    want = 5
    sources = [PullSource(node_id=f"n{i + 1}", addr="", fid=hdrs[i].fid) for i in range(6) if i != want]
    req = PullRequest(fid=hdrs[want].fid, header=hdrs[want], expected_sha256=hdrs[want].frag_sha256,
                      mode="ec_rebuild", sources=sources, ec=PullEc(k=4, n=6, frag_idx=want))
    res = await pull(node, req, fetch=fake_fetch(table))
    assert res.source_used.startswith("ec:") and "n2" not in res.source_used
    assert (await node.storage.get(hdrs[want].fid))[1] == frags[want]
    assert node.reports == [(hdrs[1].fid, "n2", "corrupt", "repair")]


async def test_pull_ec_rebuild_not_enough_fragments(node):
    chunk = os.urandom(4000)
    frags = ec_encode(chunk, 4, 2)
    vid = ids.new_version_id()
    hdrs = [header(f, i, vid=vid, chunk=chunk, policy="ec42") for i, f in enumerate(frags)]
    table = {hdrs[i].fid: (200, frags[i], {"x-vault-meta": encode_meta(hdrs[i])}) for i in range(3)}
    sources = [PullSource(node_id=f"n{i + 1}", addr="", fid=hdrs[i].fid) for i in range(5)]
    req = PullRequest(fid=hdrs[5].fid, header=hdrs[5], expected_sha256=hdrs[5].frag_sha256,
                      mode="ec_rebuild", sources=sources, ec=PullEc(k=4, n=6, frag_idx=5))
    with pytest.raises(VaultHTTPError) as e:
        await pull(node, req, fetch=fake_fetch(table))
    assert e.value.status == 424


# ── scrubber ──

async def test_scrubber_loop_finds_damage_and_honours_naive(node):
    payloads = [os.urandom(3000 + i) for i in range(3)]
    hs = [header(p, i) for i, p in enumerate(payloads)]
    for h, p in zip(hs, payloads):
        await node.storage.put(h, p)
    path = node.storage.entry(hs[0].fid).path
    path.write_bytes(path.read_bytes()[:-1] + b"\xff")

    node.safety = SafetyCfg.for_mode("naive")
    task = asyncio.create_task(soum_scrubber.run(node))
    await asyncio.sleep(0.1)
    assert node.scrub.last_pass_at is None and node.storage.has(hs[0].fid)   # paused in Naive

    node.safety = SafetyCfg()
    node.trigger_scrub()
    for _ in range(100):
        if node.scrub.last_pass_at:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert node.scrub.scanned == 3 and node.scrub.corrupt_found == 1
    assert not node.storage.has(hs[0].fid) and node.reports == [(hs[0].fid, "n1", "corrupt", "scrub")]

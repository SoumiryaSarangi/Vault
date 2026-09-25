"""Owner: Soum (S2). Cover: .blk round trip (header + payload); atomic write leaves no tmp on success;
startup deletes tmp/*.part and counts them; corrupt payload detected on read → quarantined; index rebuilt from disk."""
import asyncio
import os

import pytest

from vault.common import ids
from vault.common.config import SafetyCfg
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentHeader
from vault.node.soum_storage import MAGIC, Corrupt, NotFound, Storage, decode_blk, encode_blk


def make_frag(payload: bytes, frag_idx: int = 0, version_id: str = "") -> FragmentHeader:
    vid = version_id or ids.new_version_id()
    cid = ids.chunk_id(vid, 0)
    sha = sha256_hex(payload)
    return FragmentHeader(fid=ids.fid(cid, frag_idx), chunk_id=cid, frag_idx=frag_idx, version_id=vid,
                          bucket="clinic", key="xray-0042.png", policy="rep3", chunk_sha256=sha,
                          frag_sha256=sha, chunk_size=len(payload), frag_size=len(payload), epoch=1)


async def fresh(root, safety=SafetyCfg) -> Storage:
    st = Storage(root, "n1", safety=safety)
    await st.startup()
    return st


def flip_payload_byte(st: Storage, fid: str) -> None:
    p = st.entry(fid).path
    blob = bytearray(p.read_bytes())
    blob[-1] ^= 0xFF
    p.write_bytes(bytes(blob))


def test_blk_format():
    payload = b"hello vault"
    h = make_frag(payload)
    blob = encode_blk(h, payload)
    assert blob[:4] == MAGIC
    assert int.from_bytes(blob[4:8], "big") == len(h.model_dump_json().encode())
    assert decode_blk(blob) == (h, payload)
    with pytest.raises(ValueError):
        decode_blk(b"XXXX" + blob[4:])


async def test_round_trip(tmp_path):
    st = await fresh(tmp_path)
    payload = os.urandom(100_000)
    h = make_frag(payload)
    e = await st.put(h, payload)
    assert e.path == tmp_path / "blobs" / h.version_id[2:4] / f"{h.fid}.blk"
    assert await st.get(h.fid) == (h, payload)
    assert st.has(h.fid) and st.count() == 1 and st.disk_used() == e.path.stat().st_size


async def test_no_tmp_left_after_success(tmp_path):
    st = await fresh(tmp_path)
    for i in range(5):
        payload = os.urandom(1000)
        await st.put(make_frag(payload, i), payload)
    assert list((tmp_path / "tmp").iterdir()) == []


async def test_startup_discards_and_counts_tmp(tmp_path):
    st = await fresh(tmp_path)
    payload = b"kept"
    h = make_frag(payload)
    await st.put(h, payload)
    for i in range(3):
        (tmp_path / "tmp" / f"v_x_00000_f{i}.abcd.part").write_bytes(b"half a write")
    st2 = Storage(tmp_path, "n1")
    assert await st2.startup() == 3
    assert list((tmp_path / "tmp").iterdir()) == []
    assert await st2.get(h.fid) == (h, payload)


async def test_corrupt_payload_quarantined_on_read(tmp_path):
    st = await fresh(tmp_path)
    payload = os.urandom(4096)
    h = make_frag(payload)
    await st.put(h, payload)
    flip_payload_byte(st, h.fid)
    with pytest.raises(Corrupt):
        await st.get(h.fid)
    assert (tmp_path / "quarantine" / f"{h.fid}.blk").exists()
    assert not st.has(h.fid)
    with pytest.raises(NotFound):
        await st.get(h.fid)


async def test_verify_on_read_off_returns_bytes_unchecked(tmp_path):
    st = await fresh(tmp_path, safety=lambda: SafetyCfg.for_mode("naive"))
    payload = os.urandom(4096)
    h = make_frag(payload)
    await st.put(h, payload)
    flip_payload_byte(st, h.fid)
    _, got = await st.get(h.fid)
    assert got != payload and st.has(h.fid)


async def test_verify_quarantines_even_when_verify_on_read_off(tmp_path):
    st = await fresh(tmp_path, safety=lambda: SafetyCfg.for_mode("naive"))
    a, b = os.urandom(512), os.urandom(512)
    ha, hb = make_frag(a, 0), make_frag(b, 1)
    await st.put(ha, a)
    await st.put(hb, b)
    flip_payload_byte(st, hb.fid)
    assert await st.verify(ha.fid) is True
    assert await st.verify(hb.fid) is False
    assert not st.has(hb.fid) and (tmp_path / "quarantine" / f"{hb.fid}.blk").exists()


async def test_index_rebuilt_from_disk(tmp_path):
    st = await fresh(tmp_path)
    frags = {}
    for i in range(4):
        payload = os.urandom(2000 + i)
        h = make_frag(payload, i)
        await st.put(h, payload)
        frags[h.fid] = (h, payload)
    (tmp_path / "blobs" / "zz").mkdir()
    (tmp_path / "blobs" / "zz" / "junk.blk").write_bytes(b"not a fragment")
    st2 = await fresh(tmp_path)                      # "restart"
    assert st2.count() == 4
    assert {i.fid for i in st2.inventory()} == set(frags)
    for fid, (h, payload) in frags.items():
        assert await st2.get(fid) == (h, payload)
    inv = {i.fid: i for i in st2.inventory()}
    assert all(inv[fid].sha256 == h.frag_sha256 and inv[fid].size == len(p) for fid, (h, p) in frags.items())
    assert (tmp_path / "quarantine" / "junk.blk").exists()


async def test_naive_direct_write_round_trip(tmp_path):
    st = await fresh(tmp_path, safety=lambda: SafetyCfg.for_mode("naive"))
    payload = os.urandom(3000)
    h = make_frag(payload)
    await st.put(h, payload)
    assert list((tmp_path / "tmp").iterdir()) == []
    assert await st.get(h.fid) == (h, payload)


async def test_delete(tmp_path):
    st = await fresh(tmp_path)
    payload = b"bye"
    h = make_frag(payload)
    e = await st.put(h, payload)
    assert await st.delete(h.fid) is True
    assert not e.path.exists() and not st.has(h.fid)
    assert await st.delete(h.fid) is False


async def test_file_deleted_on_disk_is_not_found(tmp_path):
    st = await fresh(tmp_path)
    payload = b"gone"
    h = make_frag(payload)
    e = await st.put(h, payload)
    e.path.unlink()
    with pytest.raises(NotFound):
        await st.get(h.fid)
    assert not st.has(h.fid)


async def test_concurrent_puts_same_fid(tmp_path):
    st = await fresh(tmp_path)
    payload = os.urandom(10_000)
    h = make_frag(payload)
    await asyncio.gather(*(st.put(h, payload) for _ in range(5)))
    assert await st.get(h.fid) == (h, payload)
    assert list((tmp_path / "tmp").iterdir()) == []


async def test_scrub_order_recent_first(tmp_path):
    st = await fresh(tmp_path)
    old, new = b"old", b"new"
    ho, hn = make_frag(old, 0), make_frag(new, 1)
    await st.put(ho, old)
    await st.put(hn, new)
    st.entry(ho.fid).mtime -= 3600
    assert st.scrub_order(300) == [hn.fid, ho.fid]


async def test_node_json_round_trip(tmp_path):
    st = await fresh(tmp_path)
    assert await st.read_node_json() == {}
    await st.write_node_json({"epoch": 4, "lease_expiry": 1727330005.0})
    assert await st.read_node_json() == {"node_id": "n1", "epoch": 4, "lease_expiry": 1727330005.0}
    (tmp_path / "node.json.dead.tmp").write_text("{")
    await fresh(tmp_path)
    assert list(tmp_path.glob("node.json.*.tmp")) == []

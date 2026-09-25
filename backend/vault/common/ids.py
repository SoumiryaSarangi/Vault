"""Identifier formats (ARCHITECTURE §3.1). Owner: Anushka (shared).

    version  v_ + 20 hex             v_4f1c9a0e7b2d33a1c8e0
    chunk    <version_id>_<idx:05d>  v_4f1c9a0e7b2d33a1c8e0_00002
    fid      <chunk_id>_f<frag_idx>  v_4f1c9a0e7b2d33a1c8e0_00002_f1
"""
import secrets


def new_version_id() -> str:
    return "v_" + secrets.token_hex(10)


def chunk_id(version_id: str, idx: int) -> str:
    return f"{version_id}_{idx:05d}"


def fid(chunk_id_: str, frag_idx: int) -> str:
    return f"{chunk_id_}_f{frag_idx}"


def parse_fid(fid_: str) -> tuple[str, str, int, int]:
    """fid → (version_id, chunk_id, chunk_idx, frag_idx)."""
    chunk_part, frag_part = fid_.rsplit("_f", 1)
    version_id, idx = chunk_part.rsplit("_", 1)
    return version_id, chunk_part, int(idx), int(frag_part)


def version_of_chunk(chunk_id_: str) -> str:
    return chunk_id_.rsplit("_", 1)[0]


def short_fid(fid_: str) -> str:
    """For technical lines (DESIGN §6.4): v_4f1c…_00002_f1"""
    version_id, _, idx, frag = parse_fid(fid_)
    return f"{version_id[:6]}…_{idx:05d}_f{frag}"

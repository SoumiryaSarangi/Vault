"""Hashing helpers. Owner: Anushka (shared).

SHA-256 for integrity; blake2b(8 bytes) for placement. Never use Python's hash() for
placement: it is randomized per process (ARCHITECTURE §3.1).
"""
import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_sha256():
    """Incremental hasher for streaming (gateway ETag)."""
    return hashlib.sha256()


def placement_hash(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")

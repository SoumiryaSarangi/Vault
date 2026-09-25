"""Reed-Solomon erasure coding with zfec (ARCHITECTURE §3.3, TECH_STACK §6.1). Owner: Soum. Task S1.

Pure, no I/O. Used by the gateway (encode/decode) and node pull (ec_rebuild). Keep signatures stable.
Fragments 0..k-1 are data shards (systematic), k..n-1 parity. Pad chunk with zeros to a multiple of k.
"""


def ec_encode(chunk: bytes, k: int, m: int) -> list[bytes]:
    """→ n = k+m fragments of equal size."""
    raise NotImplementedError("S1")


def ec_decode(frags: dict[int, bytes], k: int, m: int, chunk_size: int) -> bytes:
    """Any k fragments (frag_idx → bytes) → original chunk truncated to chunk_size."""
    raise NotImplementedError("S1")


def ec_rebuild(frags: dict[int, bytes], k: int, m: int, want: int) -> bytes:
    """Any k fragments → the fragment with index `want` (byte-identical to the original)."""
    raise NotImplementedError("S1")

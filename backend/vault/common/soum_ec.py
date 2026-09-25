"""Reed-Solomon erasure coding with zfec (ARCHITECTURE §3.3, TECH_STACK §6.1). Owner: Soum. Task S1.

Pure, no I/O. Used by the gateway (encode/decode) and node pull (ec_rebuild). Keep signatures stable.
Fragments 0..k-1 are data shards (systematic), k..n-1 parity. Pad chunk with zeros to a multiple of k.
"""
import zfec


def ec_encode(chunk: bytes, k: int, m: int) -> list[bytes]:
    """→ n = k+m fragments of equal size."""
    n = k + m
    pad = (-len(chunk)) % k
    data = chunk + b"\0" * pad
    size = len(data) // k
    blocks = [data[i * size:(i + 1) * size] for i in range(k)]
    return zfec.Encoder(k, n).encode(blocks)          # 0..k-1 data (systematic), k..n-1 parity


def _primary(frags: dict[int, bytes], k: int, m: int) -> list[bytes]:
    """Recover the k data blocks from any k fragments."""
    if len(frags) < k:
        raise ValueError(f"need {k} fragments, got {len(frags)}")
    idx = sorted(frags)[:k]
    return zfec.Decoder(k, k + m).decode([frags[i] for i in idx], idx)


def ec_decode(frags: dict[int, bytes], k: int, m: int, chunk_size: int) -> bytes:
    """Any k fragments (frag_idx → bytes) → original chunk truncated to chunk_size."""
    return b"".join(_primary(frags, k, m))[:chunk_size]


def ec_rebuild(frags: dict[int, bytes], k: int, m: int, want: int) -> bytes:
    """Any k fragments → the fragment with index `want` (byte-identical to the original)."""
    return zfec.Encoder(k, k + m).encode(list(_primary(frags, k, m)), [want])[0]

"""Owner: Soum (S1). Cover: encode→decode round trip for sizes 0, 1, 1 MiB, 1 MiB-1; decode from any 4 of 6;
ec_rebuild(want=i) is byte-identical to the original fragment i."""
import itertools
import os

import pytest

from vault.common.soum_ec import ec_decode, ec_encode, ec_rebuild

K, M = 4, 2
MIB = 1024 * 1024


@pytest.mark.parametrize("size", [0, 1, MIB - 1, MIB])
def test_round_trip(size):
    chunk = os.urandom(size)
    frags = ec_encode(chunk, K, M)
    assert len(frags) == K + M
    assert len({len(f) for f in frags}) == 1
    assert ec_decode(dict(enumerate(frags)), K, M, size) == chunk


@pytest.mark.parametrize("size", [1, 5, MIB - 1])
def test_systematic_data_fragments(size):
    chunk = os.urandom(size)
    frags = ec_encode(chunk, K, M)
    padded = b"".join(frags[:K])
    assert len(padded) % K == 0
    assert padded[:size] == chunk and padded[size:] == b"\0" * (len(padded) - size)


def test_decode_from_any_4_of_6():
    chunk = os.urandom(MIB - 3)
    frags = ec_encode(chunk, K, M)
    for idx in itertools.combinations(range(K + M), K):
        assert ec_decode({i: frags[i] for i in idx}, K, M, len(chunk)) == chunk


def test_rebuild_is_byte_identical():
    chunk = os.urandom(MIB)
    frags = ec_encode(chunk, K, M)
    for want in range(K + M):
        for idx in itertools.combinations([i for i in range(K + M) if i != want], K):
            assert ec_rebuild({i: frags[i] for i in idx}, K, M, want) == frags[want]


def test_too_few_fragments():
    frags = ec_encode(b"abcdefgh", K, M)
    with pytest.raises(ValueError):
        ec_decode({0: frags[0], 1: frags[1], 2: frags[2]}, K, M, 8)

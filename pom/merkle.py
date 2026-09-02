"""Merkle commitment over the evidence bundle.

Why a tree and not a single hash of the whole bundle:

A single digest can only answer "was any of this altered?". A tree can additionally prove
one field in isolation — "the similarity score was 0.9386" — with an inclusion proof,
without revealing the URL, the image hash, or whose face it was. That matters here because
the ledger is public and permanent, and the subject of a face match should not have to
publish their identity to prove a match happened.

Layout matches OpenZeppelin's MerkleProof: keccak256, pairs sorted before hashing (so a
proof carries no left/right flags), odd node promoted unchanged to the next level.

Consequence of sorting pairs, stated plainly because it is easy to assume otherwise:
exchanging two leaves that happen to be siblings yields the *same* root. Leaf position
therefore carries no integrity guarantee by itself. The guarantee here comes from `leaf()`
binding a field's name into its hash, so a value cannot be replayed under a different name
and a reordered bundle recomputes to different leaves entirely. Both behaviours are pinned
by tests in tests/test_merkle.py and tests/test_evidence.py.
"""

from __future__ import annotations

import json

from eth_utils import keccak

ZERO32 = b"\x00" * 32


def canonical(value) -> bytes:
    """Deterministic encoding. Sorted keys, no incidental whitespace, UTF-8.

    Every party that recomputes a leaf must produce byte-identical input or the root will
    not match, so this is the single place serialisation is decided.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def leaf(key: str, value) -> bytes:
    """Hash one named field. The key is bound in so that two fields sharing a value
    produce different leaves, and so a leaf cannot be replayed under another name."""
    return keccak(canonical([key, value]))


def _parent(a: bytes, b: bytes) -> bytes:
    return keccak(a + b if a <= b else b + a)


def build(leaves: list[bytes]) -> list[list[bytes]]:
    """Return every level, leaves first, root last."""
    if not leaves:
        return [[ZERO32]]
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = [_parent(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2:
            nxt.append(cur[-1])  # promote the odd one out, unchanged
        levels.append(nxt)
    return levels


def root(leaves: list[bytes]) -> bytes:
    return build(leaves)[-1][0]


def proof(leaves: list[bytes], index: int) -> list[bytes]:
    """Sibling path for `leaves[index]`."""
    if not 0 <= index < len(leaves):
        raise IndexError(f"index {index} outside 0..{len(leaves) - 1}")

    out, levels = [], build(leaves)
    for level in levels[:-1]:
        if index % 2 == 0:
            sibling = index + 1
            if sibling < len(level):
                out.append(level[sibling])
            # else: promoted node, nothing to add
        else:
            out.append(level[index - 1])
        index //= 2
    return out


def verify(leaf_hash: bytes, path: list[bytes], expected_root: bytes) -> bool:
    node = leaf_hash
    for sibling in path:
        node = _parent(node, sibling)
    return node == expected_root

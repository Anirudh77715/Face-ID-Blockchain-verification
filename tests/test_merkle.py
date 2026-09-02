"""Merkle tree properties.

Odd node counts are where these implementations usually break, so every size in a range is
exercised rather than a couple of convenient powers of two.
"""

from __future__ import annotations

import pytest

from pom import merkle as M


def leaves(n: int) -> list[bytes]:
    return [M.leaf(f"field{i}", {"v": i}) for i in range(n)]


@pytest.mark.parametrize("n", range(1, 34))
def test_every_leaf_proves_against_the_root(n):
    lv = leaves(n)
    root = M.root(lv)
    for i in range(n):
        assert M.verify(lv[i], M.proof(lv, i), root), f"leaf {i} of {n} failed"


@pytest.mark.parametrize("n", range(1, 18))
def test_a_tampered_leaf_never_verifies(n):
    lv = leaves(n)
    root = M.root(lv)
    forged = M.leaf("field0", {"v": "tampered"})
    assert not M.verify(forged, M.proof(lv, 0), root)


@pytest.mark.parametrize("n", [2, 3, 7, 8, 9])
def test_a_proof_from_one_index_does_not_validate_another(n):
    lv = leaves(n)
    root = M.root(lv)
    # Using leaf 0's sibling path for leaf 1 must fail.
    assert not M.verify(lv[1], M.proof(lv, 0), root) or n == 1


def test_root_is_deterministic():
    assert M.root(leaves(9)) == M.root(leaves(9))


def test_root_changes_when_any_leaf_changes():
    a = leaves(6)
    b = leaves(6)
    b[3] = M.leaf("field3", {"v": "different"})
    assert M.root(a) != M.root(b)


def test_leaf_binds_its_name():
    """Two fields sharing a value must not share a leaf, or one could be replayed as
    the other."""
    assert M.leaf("similarity", 0.9) != M.leaf("threshold", 0.9)


def test_canonical_encoding_is_key_order_independent():
    assert M.canonical({"a": 1, "b": 2}) == M.canonical({"b": 2, "a": 1})


def test_canonical_encoding_distinguishes_types():
    assert M.canonical({"v": 1}) != M.canonical({"v": "1"})
    assert M.canonical({"v": None}) != M.canonical({"v": 0})


def test_canonical_encoding_has_no_incidental_whitespace():
    assert M.canonical({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'


def test_empty_tree_has_a_defined_root():
    assert M.root([]) == M.ZERO32


def test_single_leaf_tree_root_is_the_leaf():
    lv = leaves(1)
    assert M.root(lv) == lv[0]
    assert M.proof(lv, 0) == []


def test_proof_index_out_of_range():
    with pytest.raises(IndexError):
        M.proof(leaves(4), 4)
    with pytest.raises(IndexError):
        M.proof(leaves(4), -1)


def test_pairs_are_sorted_so_proofs_need_no_direction_flags():
    a, b = M.leaf("a", 1), M.leaf("b", 2)
    assert M._parent(a, b) == M._parent(b, a)


def test_swapping_siblings_does_not_change_the_root():
    """A documented consequence of sorted-pair hashing, asserted so it stays a known
    property rather than a surprise.

    Because each pair is sorted before hashing, exchanging two leaves that are siblings
    produces an identical root. Position therefore carries no security on its own. What
    protects this pipeline is that leaves bind their field name -- see
    `test_leaf_binds_its_name`, and `test_swapping_candidates_changes_the_root` in
    test_evidence.py, which covers the case that can actually arise from editing a bundle.
    """
    lv = leaves(5)
    siblings_swapped = [lv[1], lv[0]] + lv[2:]
    assert M.root(lv) == M.root(siblings_swapped)


def test_moving_a_leaf_across_pairs_does_change_the_root():
    lv = leaves(5)
    moved = [lv[0], lv[2], lv[1]] + lv[3:]
    assert M.root(lv) != M.root(moved)

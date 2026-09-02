"""Chain layer: deploy, record, read back, inclusion proofs.

Requires a node:  npx hardhat node
"""

from __future__ import annotations

import json

import pytest
from web3.exceptions import ContractLogicError

from pom import merkle as M
from pom.chain import AlreadyAttested, Chain, ChainError

pytestmark = pytest.mark.needs_chain


@pytest.fixture(scope="module")
def chain():
    return Chain("local")


@pytest.fixture(scope="module")
def deployed(chain):
    """A contract of this module's own, so tests never collide with demo state."""
    return chain.deploy().address


def leaves(tag: str, n: int = 8) -> list[bytes]:
    return [M.leaf(f"{tag}-{i}", {"v": i}) for i in range(n)]


def test_connects_and_has_an_account(chain):
    assert chain.w3.is_connected()
    assert chain.account.address.startswith("0x")


def test_deploy_returns_a_usable_receipt(chain):
    r = chain.deploy()
    assert r.address.startswith("0x")
    assert r.gas_used > 0
    assert r.block >= 0
    assert r.network == "local"


def test_record_then_read_back(chain, deployed):
    lv = leaves("roundtrip")
    root = M.root(lv)
    receipt = chain.record(root, len(lv), True, address=deployed)
    assert receipt.tx_hash.startswith("0x")

    assert chain.exists(root, address=deployed)
    stored = chain.get(root, address=deployed)
    assert stored["candidate_count"] == len(lv)
    assert stored["matched"] is True
    assert stored["submitter"] == chain.account.address
    assert stored["timestamp"] > 0


def test_unknown_root_does_not_exist(chain, deployed):
    assert not chain.exists(M.root(leaves("never-written")), address=deployed)


def test_get_on_an_unknown_root_raises(chain, deployed):
    """UnknownRoot, so an absent record is distinguishable from a zeroed one."""
    with pytest.raises(ContractLogicError, match="UnknownRoot"):
        chain.get(M.root(leaves("also-never-written")), address=deployed)


def test_recording_the_same_root_twice_raises_a_clear_error(chain, deployed):
    """Identical evidence produces an identical root by design, so a rerun collides.

    That must surface as something a person can act on, not as a bare
    `ContractLogicError: execution reverted`, because the case it actually happens in is
    rehearsing a demo and then running it again for real.
    """
    lv = leaves("duplicate")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)

    with pytest.raises(AlreadyAttested) as e:
        chain.record(root, len(lv), True, address=deployed)

    assert "already" in str(e.value).lower()
    assert e.value.root_hex.startswith("0x")
    assert e.value.record["submitter"] == chain.account.address


def test_inclusion_proof_is_accepted_on_chain(chain, deployed):
    lv = leaves("inclusion", 9)
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    for i in range(len(lv)):
        assert chain.verify_inclusion(root, lv[i], M.proof(lv, i), address=deployed)


def test_a_forged_leaf_is_rejected_on_chain(chain, deployed):
    lv = leaves("forgery", 6)
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    forged = M.leaf("forgery-0", {"v": "tampered"})
    assert not chain.verify_inclusion(root, forged, M.proof(lv, 0), address=deployed)


def test_inclusion_against_an_unrecorded_root_raises(chain, deployed):
    lv = leaves("unrecorded", 4)
    with pytest.raises(ContractLogicError, match="UnknownRoot"):
        chain.verify_inclusion(M.root(lv), lv[0], M.proof(lv, 0), address=deployed)


def test_a_zero_root_is_refused(chain, deployed):
    """EmptyRoot: a zero root is what an uninitialised variable looks like, so the
    contract refuses it rather than storing a meaningless attestation."""
    with pytest.raises(ContractLogicError, match="EmptyRoot"):
        chain.record(b"\x00" * 32, 0, False, address=deployed)


def test_matched_false_round_trips(chain, deployed):
    lv = leaves("nomatch")
    root = M.root(lv)
    chain.record(root, 0, False, address=deployed)
    assert chain.get(root, address=deployed)["matched"] is False


def test_unknown_network_is_rejected():
    with pytest.raises(ChainError):
        Chain("mainnet-please-no")


def test_sepolia_without_a_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    with pytest.raises(ChainError) as e:
        Chain("sepolia")
    assert "PRIVATE_KEY" in str(e.value) or "cannot reach" in str(e.value)


def test_deploy_does_not_persist_unless_asked(chain, tmp_path, monkeypatch):
    """Regression: deploy() used to always write deployments.json, so running the test
    suite - which preflight.py does - repointed the recorded address at a throwaway test
    contract that already held attestations."""
    from pom import chain as C

    marker = tmp_path / "deployments.json"
    monkeypatch.setattr(C, "DEPLOYMENTS", marker)

    chain.deploy()
    assert not marker.exists(), "deploy() persisted without being asked to"

    chain.deploy(remember=True)
    assert marker.exists()
    assert "local" in json.loads(marker.read_text(encoding="utf-8"))

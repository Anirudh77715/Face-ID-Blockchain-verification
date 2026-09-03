"""Chain layer: deploy, record, read back, inclusion proofs.

Requires a node:  npx hardhat node
"""

from __future__ import annotations

import json

import pytest
from eth_utils import keccak
from web3.exceptions import ContractLogicError

from pom import merkle as M
from pom.chain import (
    AlreadyAttested,
    Chain,
    ChainError,
    NotSubmitter,
    Revoked,
)

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


# --------------------------------------------------------------- consent + revocation

def test_consent_hash_round_trips(chain, deployed):
    lv = leaves("consent")
    root = M.root(lv)
    consent = keccak(b"a signed consent record")
    chain.record(root, len(lv), True, address=deployed, consent_hash=consent)

    stored = chain.get(root, address=deployed)
    assert stored["has_consent"]
    assert stored["consent_hash"] == "0x" + consent.hex()


def test_an_attestation_without_consent_is_distinguishable(chain, deployed):
    """Absent consent must not look like recorded consent."""
    lv = leaves("noconsent")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    assert chain.get(root, address=deployed)["has_consent"] is False


def test_revoking_marks_it_not_live_without_erasing_it(chain, deployed):
    lv = leaves("revokeme")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    assert chain.is_live(root, address=deployed)

    chain.revoke(root, keccak(b"wrong match"), address=deployed)

    assert not chain.is_live(root, address=deployed), "consumers must see it withdrawn"
    assert chain.exists(root, address=deployed), "history cannot be erased"
    assert chain.get(root, address=deployed)["revoked"]
    assert chain.get(root, address=deployed)["revoked_at"] > 0


def test_revoking_twice_is_refused(chain, deployed):
    lv = leaves("revoketwice")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    chain.revoke(root, address=deployed)
    with pytest.raises(Revoked, match="already revoked"):
        chain.revoke(root, address=deployed)


def test_revoking_an_unknown_root_is_refused(chain, deployed):
    with pytest.raises(ChainError, match="no attestation"):
        chain.revoke(M.root(leaves("never")), address=deployed)


def test_only_the_submitter_may_revoke(chain, deployed):
    """A registry where anyone can withdraw anyone's record is worse than one with no
    revocation at all."""
    from pom.chain import Chain as ChainCls

    lv = leaves("otherparty")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)

    stranger = ChainCls("local")
    # Hardhat's second pre-funded dev account - a different signer, same chain.
    stranger.account = stranger.w3.eth.account.from_key(
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")

    with pytest.raises(NotSubmitter, match="only the submitter"):
        stranger.revoke(root, address=deployed)

    assert chain.is_live(root, address=deployed), "a stranger must not be able to revoke"


def test_revocation_survives_a_fresh_read(chain, deployed):
    lv = leaves("persist")
    root = M.root(lv)
    chain.record(root, len(lv), True, address=deployed)
    chain.revoke(root, address=deployed)

    from pom.chain import Chain as ChainCls
    assert not ChainCls("local").is_live(root, address=deployed)


def test_revert_reasons_are_explained(chain):
    """`execution reverted: ... AlreadyRecorded(0x..)` is accurate and unreadable."""
    assert "already attested" in chain._explain(Exception("reverted AlreadyRecorded(0x1)"))
    assert "only the account" in chain._explain(Exception("reverted NotSubmitter(0x1)"))
    assert "no attestation exists" in chain._explain(Exception("reverted UnknownRoot(0x1)"))


def test_local_chain_needs_one_confirmation():
    from pom.chain import NETWORKS
    assert NETWORKS["local"]["confirmations"] == 1


def test_a_public_chain_waits_for_more_than_one():
    """A just-mined block can reorg out, leaving a bundle citing a transaction that
    never happened."""
    from pom.chain import NETWORKS
    assert NETWORKS["sepolia"]["confirmations"] > 1

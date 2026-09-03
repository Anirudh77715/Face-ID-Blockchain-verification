"""Writing the commitment to a chain, and reading it back.

Two targets, same code path:

  local     Hardhat's node. Needs no faucet, no wallet, no keys, so anyone who clones the
            repo can reproduce a full run offline. The brief permits a local/simulated
            chain explicitly.
  sepolia   Base Sepolia. One real public transaction, so the record is independently
            inspectable in a block explorer rather than only on the author's machine.

Reading back is the part that matters. A pipeline that only writes has demonstrated
nothing about tamper evidence: the claim is only testable if the stored root can be
fetched and compared against a root recomputed from the evidence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

ROOT_DIR = Path(__file__).resolve().parent.parent
ARTIFACT = (ROOT_DIR / "artifacts" / "contracts" / "AttestationRegistry.sol"
            / "AttestationRegistry.json")
DEPLOYMENTS = ROOT_DIR / "deployments.json"

# bytes32(0). Means "no consent recorded" in an attestation, and "no reason given" in a
# revocation - the contract treats both as absent rather than as a value.
ZERO32 = bytes(32)

NETWORKS = {
    "local": {
        "rpc": "http://127.0.0.1:8545",
        "chain_id": 31337,
        # A single-node dev chain cannot reorg, so one block is final.
        "confirmations": 1,
        # Hardhat's first pre-funded development account. This key is published in
        # Hardhat's own documentation and is worthless by construction — it exists so
        # local runs need no setup. Never send real funds to it.
        "key_env": None,
        "default_key": ("0xac0974bec39a17e36ba4a6b4d238ff944bacb478"
                        "cbed5efcae784d7bf4f2ff80"),
        "explorer": None,
    },
    "sepolia": {
        "rpc": os.environ.get("BASE_SEPOLIA_RPC", "https://sepolia.base.org"),
        "chain_id": 84532,
        # A public chain can reorg a freshly mined block back out of existence, which
        # would leave a bundle pointing at a transaction that no longer happened.
        "confirmations": 3,
        "key_env": "PRIVATE_KEY",
        "default_key": None,
        "explorer": "https://sepolia.basescan.org/tx/",
    },
}


class ChainError(Exception):
    pass


class AlreadyAttested(ChainError):
    """This exact evidence is already on chain.

    Identical evidence hashes to an identical root by design — the root *is* the identity
    of a run — so re-running a pipeline over the same inputs collides. That is not a
    failure of the run, and it should not surface as `execution reverted`, because the
    situation it arises in is rehearsing a demo and then performing it.
    """

    def __init__(self, root_hex: str, record: dict, address: str):
        self.root_hex = root_hex
        self.record = record
        self.address = address
        super().__init__(
            f"this evidence is already attested on {address}\n"
            f"  root       {root_hex}\n"
            f"  recorded   {record['timestamp']} by {record['submitter']}\n"
            f"  candidates {record['candidate_count']}   matched {record['matched']}\n"
            "  The commitment is already on chain, so verification of this bundle will\n"
            "  succeed. To produce a fresh transaction, redeploy (py deploy.py) or run\n"
            "  against a different image."
        )


class Revoked(ChainError):
    """The attestation exists but has been withdrawn."""


class NotSubmitter(ChainError):
    """Only the account that recorded an attestation may revoke it."""


@dataclass
class Receipt:
    tx_hash: str
    block: int
    gas_used: int
    address: str
    network: str

    @property
    def explorer_url(self) -> str | None:
        base = NETWORKS[self.network]["explorer"]
        return f"{base}{self.tx_hash}" if base else None


def _artifact() -> dict:
    if not ARTIFACT.exists():
        raise ChainError(
            f"contract artifact missing: {ARTIFACT}\n"
            "  build it first:  npx hardhat compile"
        )
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class Chain:
    def __init__(self, network: str = "local"):
        if network not in NETWORKS:
            raise ChainError(f"unknown network {network!r}; have {sorted(NETWORKS)}")
        self.network = network
        cfg = NETWORKS[network]

        self.w3 = Web3(Web3.HTTPProvider(cfg["rpc"], request_kwargs={"timeout": 60}))
        if not self.w3.is_connected():
            hint = ("  start it with:  npx hardhat node"
                    if network == "local" else f"  RPC: {cfg['rpc']}")
            raise ChainError(f"cannot reach the {network} RPC\n{hint}")

        key = cfg["default_key"] or os.environ.get(cfg["key_env"] or "", "")
        if not key:
            raise ChainError(
                f"{cfg['key_env']} is not set - needed to sign on {network}. "
                "Use --chain local to run without any wallet."
            )
        self.account = self.w3.eth.account.from_key(key)

        art = _artifact()
        self.abi, self.bytecode = art["abi"], art["bytecode"]

    # ------------------------------------------------------------------ plumbing

    @staticmethod
    def _explain(error: Exception) -> str:
        """Turn a raw revert into the sentence a person can act on.

        web3 surfaces a custom error as `execution reverted: ... AlreadyRecorded(0x..)`,
        which is accurate and unreadable.
        """
        text = str(error)
        for name, meaning in (
            ("AlreadyRecorded", "this evidence root is already attested"),
            ("AlreadyRevoked", "this attestation was already revoked"),
            ("NotSubmitter", "only the account that recorded it may revoke it"),
            ("UnknownRoot", "no attestation exists for that root"),
            ("EmptyRoot", "a zero root cannot be recorded"),
        ):
            if name in text:
                return f"{meaning} ({name})"
        return text

    def _send(self, tx) -> tuple[str, dict]:
        tx.setdefault("from", self.account.address)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(self.account.address))
        tx.setdefault("chainId", NETWORKS[self.network]["chain_id"])
        tx.setdefault("gasPrice", self.w3.eth.gas_price)
        tx.setdefault("gas", 900_000)

        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt["status"] != 1:
            raise ChainError(f"transaction reverted: {_hex(tx_hash)}")

        self._await_confirmations(receipt["blockNumber"])
        return tx_hash.hex(), receipt

    def _await_confirmations(self, block: int) -> None:
        """Wait until the block is buried deep enough to rely on.

        On the local chain this returns immediately. On a public one, treating a
        just-mined block as settled is how a bundle ends up citing a transaction that a
        reorg removed.
        """
        import time as _time

        wanted = NETWORKS[self.network].get("confirmations", 1)
        if wanted <= 1:
            return

        deadline = _time.time() + 300
        while _time.time() < deadline:
            depth = self.w3.eth.block_number - block + 1
            if depth >= wanted:
                return
            _time.sleep(3)
        raise ChainError(
            f"only reached {self.w3.eth.block_number - block + 1} of {wanted} "
            f"confirmations for block {block}")

    # ------------------------------------------------------------------- deploy

    def deploy(self, remember: bool = False) -> Receipt:
        """Deploy a new registry.

        `remember` is opt-in, and deploy.py is the only caller that sets it. Persisting
        by default meant the test suite — which deploys throwaway registries — silently
        repointed deployments.json at one of them. `preflight.py` runs the tests, so
        checking your setup would quietly replace the contract you had just deployed with
        a test fixture that already held attestations. That is the kind of thing you
        discover mid-recording.
        """
        contract = self.w3.eth.contract(abi=self.abi, bytecode=self.bytecode)
        tx = contract.constructor().build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": NETWORKS[self.network]["chain_id"],
            "gasPrice": self.w3.eth.gas_price,
        })
        tx_hash, receipt = self._send(tx)
        address = receipt["contractAddress"]
        if remember:
            self._remember(address)
        return Receipt(_hex(tx_hash), receipt["blockNumber"], receipt["gasUsed"],
                       address, self.network)

    def _remember(self, address: str) -> None:
        data = {}
        if DEPLOYMENTS.exists():
            data = json.loads(DEPLOYMENTS.read_text(encoding="utf-8"))
        data[self.network] = address
        DEPLOYMENTS.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def address(self, explicit: str | None = None) -> str:
        if explicit:
            return Web3.to_checksum_address(explicit)
        if DEPLOYMENTS.exists():
            found = json.loads(DEPLOYMENTS.read_text(encoding="utf-8")).get(self.network)
            if found:
                return Web3.to_checksum_address(found)
        raise ChainError(
            f"no contract deployed on {self.network}\n"
            f"  deploy it:  py deploy.py --chain {self.network}"
        )

    def contract(self, address: str | None = None):
        return self.w3.eth.contract(address=self.address(address), abi=self.abi)

    # -------------------------------------------------------------- read/write

    def record(self, root: bytes, candidate_count: int, matched: bool,
               address: str | None = None, consent_hash: bytes | None = None) -> Receipt:
        c = self.contract(address)

        # Checked before sending rather than caught after, so no gas is spent and the
        # caller gets the existing record instead of a decoded revert string.
        if c.functions.exists(root).call():
            raise AlreadyAttested(
                "0x" + root.hex(),
                self.get(root, address=address),
                c.address,
            )

        tx = c.functions.record(
            root, candidate_count, matched, consent_hash or (ZERO32)
        ).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": NETWORKS[self.network]["chain_id"],
            "gasPrice": self.w3.eth.gas_price,
        })
        tx_hash, receipt = self._send(tx)
        return Receipt(_hex(tx_hash), receipt["blockNumber"], receipt["gasUsed"],
                       c.address, self.network)

    def get(self, root: bytes, address: str | None = None) -> dict:
        (ts, count, matched, submitter, consent_hash,
         revoked_at, reason) = self.contract(address).functions.get(root).call()
        return {
            "timestamp": ts,
            "candidate_count": count,
            "matched": matched,
            "submitter": submitter,
            "consent_hash": "0x" + consent_hash.hex(),
            "has_consent": consent_hash != ZERO32,
            "revoked_at": revoked_at,
            "revoked": revoked_at != 0,
            "revocation_reason": "0x" + reason.hex(),
        }

    def is_live(self, root: bytes, address: str | None = None) -> bool:
        """Recorded and not withdrawn - the question a consumer should ask. `exists`
        stays true forever once written, including for revoked records."""
        return self.contract(address).functions.isLive(root).call()

    def revoke(self, root: bytes, reason: bytes | None = None,
               address: str | None = None) -> Receipt:
        """Withdraw an attestation. The record stays; readers learn not to rely on it."""
        c = self.contract(address)
        if not c.functions.exists(root).call():
            raise ChainError(f"no attestation exists for root 0x{root.hex()}")

        stored = self.get(root, address=address)
        if stored["revoked"]:
            raise Revoked(f"already revoked at {stored['revoked_at']}")
        if stored["submitter"].lower() != self.account.address.lower():
            raise NotSubmitter(
                f"recorded by {stored['submitter']}, "
                f"signing as {self.account.address} - only the submitter may revoke")

        tx = c.functions.revoke(root, reason or (ZERO32)).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": NETWORKS[self.network]["chain_id"],
            "gasPrice": self.w3.eth.gas_price,
        })
        tx_hash, receipt = self._send(tx)
        return Receipt(_hex(tx_hash), receipt["blockNumber"], receipt["gasUsed"],
                       c.address, self.network)

    def exists(self, root: bytes, address: str | None = None) -> bool:
        return self.contract(address).functions.exists(root).call()

    def find_record_tx(self, root: bytes, address: str | None = None,
                       from_block: int | str = 0) -> dict | None:
        """Recover the transaction that committed a root, from the event log.

        Used when a rerun collides with an existing attestation: the bundle should still
        point at the transaction that actually carries its commitment. Returns None if the
        log cannot be searched — some public RPCs cap the block range, and a missing tx
        hash is not worth failing a run over.
        """
        try:
            logs = self.contract(address).events.MatchRecorded().get_logs(
                from_block=from_block, argument_filters={"root": root})
        except Exception:
            return None
        if not logs:
            return None
        event = logs[-1]
        return {"tx_hash": _hex(event["transactionHash"]),
                "block": event["blockNumber"]}

    def verify_inclusion(self, root: bytes, leaf: bytes, proof: list[bytes],
                         address: str | None = None) -> bool:
        return self.contract(address).functions.verifyInclusion(
            root, leaf, proof).call()


def _hex(h) -> str:
    s = h if isinstance(h, str) else h.hex()
    return s if s.startswith("0x") else "0x" + s

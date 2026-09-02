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

NETWORKS = {
    "local": {
        "rpc": "http://127.0.0.1:8545",
        "chain_id": 31337,
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
                f"{cfg['key_env']} is not set — needed to sign on {network}. "
                "Use --chain local to run without any wallet."
            )
        self.account = self.w3.eth.account.from_key(key)

        art = _artifact()
        self.abi, self.bytecode = art["abi"], art["bytecode"]

    # ------------------------------------------------------------------ plumbing

    def _send(self, tx) -> tuple[str, dict]:
        tx.setdefault("from", self.account.address)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(self.account.address))
        tx.setdefault("chainId", NETWORKS[self.network]["chain_id"])
        tx.setdefault("gasPrice", self.w3.eth.gas_price)
        tx.setdefault("gas", 900_000)

        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt["status"] != 1:
            raise ChainError(f"transaction reverted: {tx_hash.hex()}")
        return tx_hash.hex(), receipt

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
               address: str | None = None) -> Receipt:
        c = self.contract(address)

        # Checked before sending rather than caught after, so no gas is spent and the
        # caller gets the existing record instead of a decoded revert string.
        if c.functions.exists(root).call():
            raise AlreadyAttested(
                "0x" + root.hex(),
                self.get(root, address=address),
                c.address,
            )

        tx = c.functions.record(root, candidate_count, matched).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": NETWORKS[self.network]["chain_id"],
            "gasPrice": self.w3.eth.gas_price,
        })
        tx_hash, receipt = self._send(tx)
        return Receipt(_hex(tx_hash), receipt["blockNumber"], receipt["gasUsed"],
                       c.address, self.network)

    def get(self, root: bytes, address: str | None = None) -> dict:
        ts, count, matched, submitter = self.contract(address).functions.get(root).call()
        return {"timestamp": ts, "candidate_count": count,
                "matched": matched, "submitter": submitter}

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

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

    def deploy(self) -> Receipt:
        contract = self.w3.eth.contract(abi=self.abi, bytecode=self.bytecode)
        tx = contract.constructor().build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": NETWORKS[self.network]["chain_id"],
            "gasPrice": self.w3.eth.gas_price,
        })
        tx_hash, receipt = self._send(tx)
        address = receipt["contractAddress"]
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

    def verify_inclusion(self, root: bytes, leaf: bytes, proof: list[bytes],
                         address: str | None = None) -> bool:
        return self.contract(address).functions.verifyInclusion(
            root, leaf, proof).call()


def _hex(h) -> str:
    s = h if isinstance(h, str) else h.hex()
    return s if s.startswith("0x") else "0x" + s

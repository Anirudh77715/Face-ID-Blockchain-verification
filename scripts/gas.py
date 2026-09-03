"""Measure what the contract actually costs, so the README does not quote stale numbers.

    npx hardhat node          # in another terminal
    py scripts/gas.py

Every figure in the README came from here. They went stale once already - the struct grew
two fields for consent and revocation, and the quoted costs silently became wrong - which
is the argument for measuring rather than remembering.

Deploys a throwaway registry each run, so the numbers are not affected by whatever else is
already stored.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_utils import keccak  # noqa: E402

from pom import merkle as M  # noqa: E402
from pom.chain import Chain, ChainError  # noqa: E402

LEAVES = 23  # a typical run: 12 candidates plus query, models, search, consent, decision


def root(tag: str) -> bytes:
    return M.root([M.leaf(f"{tag}-{i}", i) for i in range(LEAVES)])


def main() -> int:
    try:
        chain = Chain("local")
    except ChainError as e:
        print(e, file=sys.stderr)
        return 1

    print("measuring against a fresh contract\n")
    deploy = chain.deploy()
    at = deploy.address   # every call below must target THIS contract, not whatever
                          # deployments.json happens to point at - otherwise "first write
                          # on a fresh contract" is measured against a populated one.
    rows = [("deploy the registry", deploy.gas_used, "")]

    first = chain.record(root("first"), 12, True, address=at)
    rows.append(("record, no consent (first on a contract)", first.gas_used,
                 "pays for the roots array's cold slot"))

    later = chain.record(root("later"), 12, True, address=at)
    rows.append(("record, no consent (subsequent)", later.gas_used, ""))

    consented = chain.record(root("consent"), 12, True, address=at,
                             consent_hash=keccak(b"a signed consent record"))
    rows.append(("record, with consent", consented.gas_used,
                 f"+{consented.gas_used - later.gas_used} for the consent hash"))

    revoked = chain.revoke(root("consent"), address=at)
    rows.append(("revoke", revoked.gas_used, "two slots written, none allocated"))

    width = max(len(label) for label, _, _ in rows)
    print(f"| {'operation'.ljust(width)} | gas | |")
    print(f"|{'-' * (width + 2)}|-----|-|")
    for label, gas, note in rows:
        print(f"| {label.ljust(width)} | {gas:,} | {note} |")

    print(f"\ncontract {deploy.address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

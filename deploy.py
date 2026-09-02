"""Deploy AttestationRegistry and remember its address.

    npx hardhat node          # in another terminal, for --chain local
    py deploy.py --chain local
    py deploy.py --chain sepolia      # needs PRIVATE_KEY and a funded Base Sepolia account

The address is written to deployments.json so run.py finds it without being told. Each
evidence bundle also records the contract it used, so verification never depends on that
file still being around.
"""

from __future__ import annotations

import argparse
import sys

from pom.chain import Chain, ChainError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    args = ap.parse_args()

    try:
        chain = Chain(args.chain)
    except ChainError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"network   {args.chain}")
    print(f"deployer  {chain.account.address}")

    balance = chain.w3.eth.get_balance(chain.account.address)
    print(f"balance   {chain.w3.from_wei(balance, 'ether')} ETH")
    if balance == 0:
        print("\n  account has no balance; fund it before deploying", file=sys.stderr)
        return 1

    try:
        receipt = chain.deploy(remember=True)
    except ChainError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"\ncontract  {receipt.address}")
    print(f"tx        {receipt.tx_hash}")
    print(f"block     {receipt.block}   gas {receipt.gas_used}")
    if receipt.explorer_url:
        print(f"explorer  {receipt.explorer_url}")
    print("\nsaved to deployments.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

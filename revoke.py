"""Withdraw an attestation.

    py revoke.py --bundle evidence/run-abc123.json --reason "match was wrong"

A face match can be wrong, or a subject can change their mind. Chain history cannot be
erased, so the record stays and is marked withdrawn instead - `verify.py` then reports it
as NOT RELIABLE rather than VERIFIED, and `isLive` returns false for any consumer that
asks the contract directly.

Only the account that recorded an attestation can revoke it. A registry where anyone can
withdraw anyone's record would be worse than one with no revocation at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eth_utils import keccak

from pom import evidence
from pom.chain import Chain, ChainError, NotSubmitter, Revoked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    ap.add_argument("--reason", default="",
                    help="kept off chain; only its hash is stored")
    ap.add_argument("--contract")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    if not args.bundle.exists():
        print(f"no such bundle: {args.bundle}", file=sys.stderr)
        return 1

    bundle = evidence.load(args.bundle)
    root_hex = bundle.get("merkle", {}).get("root")
    attestation = bundle.get("attestation", {})
    if not root_hex or not attestation.get("written"):
        print("this bundle was never written to a chain", file=sys.stderr)
        return 1

    address = args.contract or attestation.get("contract")
    print(f"bundle    {args.bundle}")
    print(f"root      {root_hex}")
    print(f"contract  {address}")
    print(f"reason    {args.reason or '(none given)'}")
    print("\nRevoking is permanent. The record stays on chain and is marked withdrawn;")
    print("it cannot be un-revoked.")

    if not args.yes:
        # Irreversible and outward-facing on a public chain, so it asks unless told not to.
        try:
            if input("\nType 'revoke' to continue: ").strip() != "revoke":
                print("cancelled")
                return 1
        except EOFError:
            print("\nnot a terminal; pass --yes to revoke non-interactively",
                  file=sys.stderr)
            return 1

    try:
        chain = Chain(args.chain)
        receipt = chain.revoke(
            bytes.fromhex(root_hex[2:]),
            reason=keccak(args.reason.encode("utf-8")) if args.reason else None,
            address=address,
        )
    except (Revoked, NotSubmitter) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    except ChainError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1

    print("\nrevoked")
    print(f"  tx      {receipt.tx_hash}")
    print(f"  block   {receipt.block}   gas {receipt.gas_used}")
    if receipt.explorer_url:
        print(f"  explorer {receipt.explorer_url}")
    print(f"\n  py verify.py --bundle {args.bundle} --chain {args.chain}")
    print("  now reports NOT RELIABLE (exit 8).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

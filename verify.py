"""Re-verify an evidence bundle against the record on chain.

    py verify.py --bundle evidence/run-abc123.json --chain local

This is the half of the pipeline that makes "tamper-evident" mean anything. Writing a hash
to a chain proves nothing on its own; the claim only becomes testable when the stored root
is fetched back and compared against a root recomputed from the evidence that is supposed
to correspond to it.

Three checks run, in order of how specifically they can name a problem:

  1. leaf-level  every field is re-hashed and compared to its stored leaf, so an altered
                 field is named rather than merely detected
  2. root-level  the tree is rebuilt from those leaves
  3. chain-level the rebuilt root is looked up on chain

Exit codes:
    0  verified: the evidence matches the on-chain commitment
    1  usage or connection problem
    6  TAMPERED: the evidence no longer matches what was committed
    7  the root is not on chain at all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pom import evidence, merkle
from pom.chain import Chain, ChainError

GREEN, RED, YELLOW, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m")


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}\n" + "-" * 68)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    ap.add_argument("--contract", help="override the deployed address")
    ap.add_argument("--disclose", type=int, metavar="N",
                    help="prove candidate[N] on chain without revealing the rest")
    args = ap.parse_args()

    if not args.bundle.exists():
        print(f"no such bundle: {args.bundle}", file=sys.stderr)
        return 1

    bundle = evidence.load(args.bundle)
    committed = bundle.get("merkle", {}).get("root")
    attestation = bundle.get("attestation", {})

    print(f"bundle      {args.bundle}")
    print(f"run_id      {bundle.get('run_id')}")
    print(f"created     {bundle.get('created_at')}")
    print(f"committed   {committed}")

    # ------------------------------------------------------- 1. leaf integrity
    rule("1. FIELD-LEVEL INTEGRITY")
    diverged = evidence.diff_against_stored(bundle)
    if diverged:
        print(f"  {RED}{len(diverged)} field(s) no longer match the commitment:{OFF}")
        for name in diverged:
            print(f"    {RED}x{OFF} {name}")
    else:
        print(f"  {GREEN}all {bundle['merkle']['leaf_count']} fields match{OFF}")

    # ---------------------------------------------------------- 2. root rebuild
    rule("2. MERKLE ROOT")
    recomputed = "0x" + evidence.root(bundle).hex()
    print(f"  committed   {committed}")
    print(f"  recomputed  {recomputed}")
    root_ok = recomputed == committed
    print(f"  {GREEN}roots agree{OFF}" if root_ok else f"  {RED}ROOTS DIFFER{OFF}")

    # ------------------------------------------------------------- 3. on chain
    rule(f"3. ON-CHAIN RECORD ({args.chain})")
    if not attestation.get("written"):
        print(f"  {YELLOW}this bundle was never written to a chain{OFF}")
        print(f"  reason: {attestation.get('reason', 'unknown')}")
        return 6 if (diverged or not root_ok) else 7

    try:
        chain = Chain(args.chain)
        address = args.contract or attestation.get("contract")
        root_bytes = bytes.fromhex(recomputed[2:])

        if not chain.exists(root_bytes, address=address):
            print(f"  {RED}the recomputed root is NOT on chain{OFF}")
            print(f"  contract  {address}")
            print(f"  expected  {attestation.get('tx_hash')}")
            print(f"\n  {RED}The evidence does not correspond to any recorded run.{OFF}")
            return 6 if diverged or not root_ok else 7

        record = chain.get(root_bytes, address=address)
        print(f"  contract    {address}")
        print(f"  tx          {attestation.get('tx_hash')}")
        print(f"  block       {attestation.get('block')}")
        print(f"  recorded at {record['timestamp']} by {record['submitter']}")
        print(f"  candidates  {record['candidate_count']}   matched {record['matched']}")
        print(f"  {GREEN}root found on chain{OFF}")

        # ------------------------------------------ optional selective disclosure
        if args.disclose is not None:
            rule(f"4. SELECTIVE DISCLOSURE - candidate[{args.disclose}]")
            named = evidence.leaves(bundle)
            target = f"candidate[{args.disclose}]"
            index = next((i for i, (n, _) in enumerate(named) if n == target), None)
            if index is None:
                print(f"  no such leaf: {target}")
                return 1
            path = merkle.proof([h for _, h in named], index)
            ok = chain.verify_inclusion(root_bytes, named[index][1], path,
                                        address=address)
            cand = bundle["candidates"][args.disclose]
            print(f"  proving     similarity {cand['similarity']} "
                  f"accepted={cand['accepted']}")
            print(f"  proof       {len(path)} sibling hashes")
            print(f"  {GREEN}the contract accepts this leaf{OFF}" if ok
                  else f"  {RED}the contract rejects this leaf{OFF}")
            print("  Nothing else about the run was sent on chain to check it.")
            if not ok:
                return 6
    except ChainError as e:
        print(f"  {e}", file=sys.stderr)
        return 1

    # ----------------------------------------------------------------- verdict
    rule("VERDICT")
    if diverged or not root_ok:
        print(f"  {RED}{BOLD}TAMPERED{OFF}")
        print("  The evidence has changed since it was committed. Fields affected:")
        for name in diverged or ["(root mismatch with no single field identified)"]:
            print(f"    - {name}")
        return 6

    print(f"  {GREEN}{BOLD}VERIFIED{OFF}")
    print("  Every field hashes to its committed leaf, those leaves rebuild the")
    print("  committed root, and that root is the one recorded on chain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

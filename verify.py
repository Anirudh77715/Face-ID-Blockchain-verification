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

Those three answer "has this evidence been altered since it was committed?". With
--refetch a fourth answers a different question - "is the post still what it was?" - by
downloading the discovered post again, hashing it again, and comparing that against the
hash the chain holds. See pom/refetch.py for why a mismatch there is not tampering.

Exit codes:
    0  verified: the evidence matches the on-chain commitment
    1  usage or connection problem
    6  TAMPERED: the evidence no longer matches what was committed
    7  the root is not on chain at all
    8  NOT RELIABLE: the evidence is intact, but the attestation was revoked or its
       consent does not hold
    9  the evidence is intact, but the live post no longer matches what was attested
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pom import consent as consent_module
from pom import evidence, merkle, refetch
from pom.chain import Chain, ChainError

GREEN, RED, YELLOW, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m")

# How many earlier runs of the same photo to list before summarising the rest.
SHOW_REPEATS = 3


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}\n" + "-" * 68)


def refetch_section(bundle: dict, chain, address, root_bytes: bytes) -> bool:
    """Download the discovered post again, hash it again, compare against the chain.

    The headline is the best match, because "at least one real matching post" is the
    claim being made and one post carries it. The other accepted candidates are
    re-checked too, but summarised - a screenful of per-candidate output would bury the
    comparison that matters.

    Returns True if any post no longer matches what was attested.
    """
    candidates = bundle.get("candidates", [])
    accepted = [(i, c) for i, c in enumerate(candidates) if c.get("accepted")]
    if not accepted:
        print(f"  {YELLOW}this run accepted no candidate, so there is no post to "
              f"re-fetch{OFF}")
        return False

    # Lead with the post the run named as the best match.
    best_url = bundle.get("decision", {}).get("best_page_url")
    index, candidate = sorted(
        accepted, key=lambda pair: pair[1].get("page_url") != best_url)[0]

    print(f"  post        {candidate.get('page_url')}")
    print(f"  image       {candidate.get('image_url')}")
    print("  fetching it again, live (the cache is bypassed on purpose) ...")

    result = refetch.recheck(candidate)

    if result.status == refetch.UNREACHABLE:
        # Not a failure. The post being unreachable now says nothing about whether the
        # attestation was honest when it was made.
        print(f"\n  {YELLOW}could not reach the post: {result.note}{OFF}")
        print("  Nothing is proved either way. A deleted post, a hotlink block or a")
        print("  network fault all look like this, and none of them is tampering.")
        return False

    if result.status == refetch.NOT_RECORDED:
        print(f"\n  {YELLOW}{result.note}{OFF}")
        return False

    proved, siblings = _prove_on_chain(bundle, chain, address, root_bytes, index)

    print()
    print(f"  current post hash   {result.current_sha256}")
    print(f"                      SHA-256 of the {result.bytes_read} bytes just "
          f"downloaded")
    print(f"  attested post hash  {result.attested_sha256}")
    print(f"                      committed at run time as part of candidate[{index}]")
    if proved:
        print(f"  on chain            that record is proved present in root "
              f"{bundle['merkle']['root'][:18]}...")
        print(f"                      by an inclusion proof of {siblings} sibling "
              f"hashes, checked")
        print("                      by the contract itself")
    else:
        print(f"  on chain            {YELLOW}could not prove the record against the "
              f"root{OFF}")

    print()
    if result.status == refetch.UNCHANGED:
        print(f"  {GREEN}{BOLD}POST UNCHANGED{OFF} - the live post still hashes to "
              f"what is on chain")
    else:
        print(f"  {YELLOW}{BOLD}POST CHANGED{OFF} - the live post no longer hashes to "
              f"what was attested")

    # The rest of the accepted set, counted rather than listed.
    others = [c for i, c in accepted if i != index]
    if others:
        counts = refetch.summarise(refetch.recheck_all(others))
        print(f"\n  other accepted posts  {counts['unchanged']} unchanged, "
              f"{counts['changed']} changed, {counts['unreachable']} unreachable")
        if counts["changed"]:
            return True

    return result.status == refetch.CHANGED


def _prove_on_chain(bundle: dict, chain, address, root_bytes: bytes,
                    index: int) -> tuple[bool, int]:
    """Prove candidate[index]'s committed record is inside the root the chain holds.

    This is what makes "on-chain hash" more than a phrase: the post's SHA-256 is not
    stored on chain by itself, it is bound into a leaf whose membership the contract
    verifies.
    """
    named = evidence.leaves(bundle)
    target = f"candidate[{index}]"
    position = next((i for i, (n, _) in enumerate(named) if n == target), None)
    if position is None:
        return False, 0

    path = merkle.proof([h for _, h in named], position)
    try:
        return chain.verify_inclusion(root_bytes, named[position][1], path,
                                      address=address), len(path)
    except Exception:
        return False, len(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    ap.add_argument("--contract", help="override the deployed address")
    ap.add_argument("--disclose", type=int, metavar="N",
                    help="prove candidate[N] on chain without revealing the rest")
    ap.add_argument("--refetch", action="store_true",
                    help="download the discovered post again, hash it again, and compare "
                         "that against the hash on chain. Answers 'is the post still what "
                         "it was?', which checking the bundle alone cannot")
    args = ap.parse_args()

    if not args.bundle.exists():
        print(f"no such bundle: {args.bundle}", file=sys.stderr)
        return 1

    bundle = evidence.load(args.bundle)
    revoked_or_tampered = False
    post_changed = False
    committed = bundle.get("merkle", {}).get("root")
    attestation = bundle.get("attestation", {})

    source_name = bundle.get("query", {}).get("source_name")
    print(f"bundle      {args.bundle}")
    print(f"run_id      {bundle.get('run_id')}")
    print(f"created     {bundle.get('created_at')}")
    print(f"image name  {source_name}   {YELLOW}(not committed - context only){OFF}")
    print(f"committed   {committed}")

    # If this same evidence was recorded before under another name, say so. A photo
    # re-uploaded as something else is exactly the case where a reader wants to know
    # what it was called the first time.
    image_sha = bundle.get("query", {}).get("image_sha256")
    repeats = (evidence.same_image_runs(image_sha, exclude=args.bundle)
               if image_sha else [])
    if repeats:
        # Capped: a photo used for rehearsal can have a dozen prior runs, and a wall of
        # them buries the verdict. Names are what the reader is here for, so any that
        # differ from this bundle's are always shown.
        shown = [r for r in repeats if r["source_name"] != source_name][:SHOW_REPEATS]
        shown += [r for r in repeats if r not in shown][:max(0, SHOW_REPEATS - len(shown))]
        shown.sort(key=lambda r: r["created_at"] or "")

        print(f"\n{YELLOW}this photo has been scanned before{OFF}  "
              f"({len(repeats)} earlier run(s))")
        for other in shown:
            same = ("same name" if other["source_name"] == source_name
                    else f"{RED}DIFFERENT NAME{OFF}")
            mark = "attested" if other["attested"] else "not attested"
            print(f"  {other['created_at']}  {other['source_name']}  "
                  f"({same}, {mark})")
            print(f"    {other['path'].name}  root {(other['root'] or '')[:18]}...")
        if len(repeats) > len(shown):
            print(f"  ... and {len(repeats) - len(shown)} more")

        renamed = {r["source_name"] for r in repeats} - {source_name}
        if renamed:
            print(f"  {YELLOW}Submitted under a different name before: "
                  f"{', '.join(sorted(n for n in renamed if n))}{OFF}")
        print("  Same image bytes, a different search moment - so these are separate")
        print("  attestations, not one record being verified twice.")

    earlier = evidence.prior_runs(committed, exclude=args.bundle) if committed else []
    if earlier:
        print(f"\n{YELLOW}this evidence has been attested before{OFF}")
        for other in earlier:
            same = "same name" if other["source_name"] == source_name else "DIFFERENT NAME"
            print(f"  {other['created_at']}  {other['source_name']}  ({same})")
            print(f"    {other['path'].name}")
        print("  Identical bytes hash to an identical root, so this is a re-submission")
        print("  of the same image, not a second independent match.")

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

            # Distinguish "this evidence was altered" from "the chain was reset". Hardhat
            # deploys deterministically, so restarting the node and redeploying puts a
            # fresh, empty contract at the SAME address - and a stale bundle then points
            # at a live contract that simply has no records. Without this, a reset dev
            # chain is indistinguishable from tampering, which is a bad thing to hit
            # mid-recording.
            try:
                total = chain.contract(address).functions.count().call()
            except Exception:
                total = None

            if total == 0:
                print(f"\n  {YELLOW}This contract holds no attestations at all.{OFF}")
                print("  That points at a reset chain rather than altered evidence:")
                print("  restarting `npx hardhat node` wipes state, and a redeploy lands")
                print("  at the same deterministic address. Re-run the pipeline to")
                print("  produce a bundle against the current chain.")
            else:
                print(f"\n  {RED}The evidence does not correspond to any recorded run.{OFF}")
                if total is not None:
                    print(f"  (the contract does hold {total} other attestation(s))")
            return 6 if diverged or not root_ok else 7

        record = chain.get(root_bytes, address=address)
        print(f"  contract    {address}")
        print(f"  tx          {attestation.get('tx_hash')}")
        print(f"  block       {attestation.get('block')}")
        print(f"  recorded at {record['timestamp']} by {record['submitter']}")
        print(f"  candidates  {record['candidate_count']}   matched {record['matched']}")
        print("  live        " + (f"{GREEN}yes{OFF}"
              if chain.is_live(root_bytes, address=address)
              else f"{RED}no - revoked{OFF}"))
        print(f"  {GREEN}root found on chain{OFF}")

        # ------------------------------------------------------------ consent
        rule("3b. CONSENT")
        if not record["has_consent"]:
            print(f"  {YELLOW}no consent hash was recorded for this attestation{OFF}")
            print("  The scan may still have been authorised - the chain simply does")
            print("  not say so. That is a weaker claim than a recorded consent.")
        elif "consent" not in bundle:
            print(f"  {RED}the chain records a consent hash, but the bundle has no "
                  f"consent record{OFF}")
            print(f"  on chain: {record['consent_hash']}")
            revoked_or_tampered = True
        else:
            stored = consent_module.ConsentRecord(**bundle["consent"])
            report = consent_module.verify(stored, bundle["query"]["image_sha256"])
            matches_chain = stored.hash_hex.lower() == record["consent_hash"].lower()

            print(f"  subject     {stored.subject}")
            print(f"  signer      {stored.signer_address}")
            print(f"  signed at   {stored.signed_at}")
            print("  signature   " + (f"{GREEN}valid{OFF}" if report["signature_valid"]
                                       else f"{RED}INVALID{OFF}"))
            print("  covers      " + (f"{GREEN}this exact image{OFF}"
                                       if report["image_matches"]
                                       else f"{RED}A DIFFERENT IMAGE{OFF}"))
            print("  hash        " + (f"{GREEN}matches the chain{OFF}" if matches_chain
                                       else f"{RED}DIFFERS FROM THE CHAIN{OFF}"))
            for problem in report["problems"]:
                print(f"    {RED}{problem}{OFF}")
            if not (report["ok"] and matches_chain):
                revoked_or_tampered = True

        # --------------------------------------------------------- revocation
        if record["revoked"]:
            rule("3c. REVOCATION")
            print(f"  {RED}{BOLD}this attestation was withdrawn{OFF}")
            print(f"  revoked at  {record['revoked_at']}")
            print("  The evidence may still be intact, but the submitter has said it")
            print("  should no longer be relied on. Chain history cannot be erased,")
            print("  so the record stands alongside its withdrawal.")
            revoked_or_tampered = True

        # --------------------------------------------- 4. re-fetch the post
        if args.refetch:
            rule("4. RE-FETCH THE POST AND COMPARE")
            post_changed = refetch_section(bundle, chain, address, root_bytes)

        # ------------------------------------------ optional selective disclosure
        if args.disclose is not None:
            rule(f"5. SELECTIVE DISCLOSURE - candidate[{args.disclose}]")
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
    if revoked_or_tampered and not (diverged or not root_ok):
        print(f"  {YELLOW}{BOLD}NOT RELIABLE{OFF}")
        print("  The evidence matches its commitment, but the attestation is revoked")
        print("  or its consent does not hold. See above.")
        return 8

    if diverged or not root_ok:
        print(f"  {RED}{BOLD}TAMPERED{OFF}")
        print("  The evidence has changed since it was committed. Fields affected:")
        for name in diverged or ["(root mismatch with no single field identified)"]:
            print(f"    - {name}")
        return 6

    if post_changed:
        print(f"  {YELLOW}{BOLD}POST CHANGED{OFF}")
        print("  The evidence is intact and its root is on chain, but the post no")
        print("  longer hashes to what was attested. The attestation still records")
        print("  truthfully what was found at the time - the source has moved since.")
        return 9

    print(f"  {GREEN}{BOLD}VERIFIED{OFF}")
    print("  Every field hashes to its committed leaf, those leaves rebuild the")
    print("  committed root, and that root is the one recorded on chain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

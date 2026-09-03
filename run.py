"""End-to-end: face scan -> genuine reverse image search -> on-chain attestation.

    py run.py --image me.jpg                    # local chain, no wallet needed
    py run.py --image me.jpg --chain sepolia    # one real public transaction

Exit codes are meaningful, because "found nothing" is a real outcome and should not look
like success:

    0  a match cleared the threshold and its commitment is on chain
    2  no candidate cleared the threshold - nothing written
    3  the search provider served a bot challenge - nothing written
    4  no face in the input image
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pom import consent as consent_module
from pom import evidence
from pom import match as match_module
from pom.chain import AlreadyAttested, Chain, ChainError
from pom.face import COSINE_SAME_IDENTITY, FaceEncoder, NoFaceFound
from pom.match import best, verify
from pom.search import SearchBlocked, SearchUnavailable, get_backend


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * 68)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    ap.add_argument("--backend", default="auto",
                    choices=("auto", "bing_url", "bing_scripted", "serpapi", "replay"),
                    help="auto tries the no-key backends in order and uses the first that "
                         "returns results; replay re-parses a saved response for "
                         "development, performs no query, and is recorded as such")
    ap.add_argument("--image-url",
                    help="a publicly reachable copy of the image. Enables the URL-based "
                         "backends, which need no key and no upload. The photo for this "
                         "task is one you have posted publicly, so this URL already "
                         "exists.")
    ap.add_argument("--threshold", type=float, default=COSINE_SAME_IDENTITY)
    ap.add_argument("--limit", type=int, default=12, help="candidates to verify")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window - use this for the screen recording; "
                         "headless is the default because it is more reliable")
    ap.add_argument("--consent", type=Path,
                    help="a signed consent record from consent.py. Its hash is committed "
                         "on chain with the evidence root")
    ap.add_argument("--contract", help="override the deployed address")
    ap.add_argument("--no-chain", action="store_true",
                    help="run the pipeline but skip the write")
    ap.add_argument("--offline", action="store_true",
                    help="no network at all: replay the saved search and use only cached "
                         "candidate images. For rehearsal and reproducibility - the search "
                         "is not live, and the evidence records that")
    args = ap.parse_args()

    if not args.image.exists():
        print(f"no such image: {args.image}", file=sys.stderr)
        return 1

    if args.offline:
        # Offline is a promise about the whole run, not just the search, so it is
        # enforced here rather than left to each backend to honour.
        match_module.CACHE.offline = True
        if args.backend not in ("replay",):
            args.backend = "replay"
        if args.chain != "local":
            print("offline mode forces --chain local", file=sys.stderr)
            args.chain = "local"
        print("[33mOFFLINE - replaying a saved search, cached images only. "
              "No live query is performed.[0m")

    encoder = FaceEncoder()

    # ---------------------------------------------------------------- 1. face
    rule("1. FACE")
    try:
        scan = encoder.scan_path(args.image)
    except NoFaceFound as e:
        print(f"  no face detected: {e}", file=sys.stderr)
        return 4
    print(f"  bbox        {scan.bbox}")
    print(f"  confidence  {scan.score:.4f}   (faces in frame: {scan.faces_found})")
    print(f"  detected at {scan.detect_scale}px wide")
    print(f"  embedding   {scan.embedding.shape[-1]}-d  sha256 {scan.embedding_sha256[:32]}")
    print(f"  image       sha256 {scan.image_sha256[:32]}")

    # A rendering of the detected box, for the viewer and the recording. Derived from
    # the image, whose hash is already committed, so it adds no leaf. Written beside the
    # bundle in evidence/, which is gitignored - it contains the subject's photo.
    face_png = None
    try:
        evidence.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        face_png = evidence.EVIDENCE_DIR / f"face-{args.image.stem}.png"
        face_png.write_bytes(encoder.annotate(args.image.read_bytes(), scan))
        print(f"  annotated   {face_png}")
    except Exception as e:
        face_png = None
        print(f"  annotated   [33mskipped ({type(e).__name__})[0m")

    # ------------------------------------------------------------- 1b. consent
    consent_record, consent_hash = None, None
    if args.consent:
        try:
            record = consent_module.load(args.consent)
        except Exception as e:
            print(f"  consent record unreadable: {e}", file=sys.stderr)
            return 6
        report = consent_module.verify(record, scan.image_sha256)
        if not report["ok"]:
            # Refused rather than warned: a consent that does not cover this image is
            # worse than no consent, because it looks like authorisation.
            print("\n  CONSENT REJECTED", file=sys.stderr)
            for problem in report["problems"]:
                print(f"    {problem}", file=sys.stderr)
            return 6
        consent_record = record.as_dict()
        consent_hash = bytes.fromhex(record.hash_hex[2:])
        print(f"  consent     {record.subject} <{record.signer_address}>")
        print(f"              signed {record.signed_at}, covers this image")
        print(f"              hash {record.hash_hex[:34]}...")
    else:
        print("  consent     [33mnone recorded[0m "
              "(use --consent; see consent.py)")

    # -------------------------------------------------------------- 2. search
    rule("2. REVERSE IMAGE SEARCH")
    backend = (get_backend(args.backend, headed=args.headed)
               if args.backend in ("auto", "bing_url", "bing_scripted")
               else get_backend(args.backend))
    if backend.name == "replay":
        print("  [33mREPLAY - no live query; this is a development run[0m")
    print(f"  provider    {backend.name}")
    try:
        response = backend.search(args.image, image_url=args.image_url)
    except SearchBlocked as e:
        print(f"\n  BLOCKED\n  {e}", file=sys.stderr)
        return 3
    except SearchUnavailable as e:
        print(f"\n  UNAVAILABLE: {e}", file=sys.stderr)
        return 3
    if getattr(backend, "attempts", None) and len(backend.attempts) > 1:
        print(f"  tried       {' -> '.join(backend.attempts)}")
    print(f"  resolved by {response.provider}")
    print(f"  candidates  {len(response.candidates)} "
          f"({len(response.social_candidates)} on social platforms)")
    print(f"  raw saved   {response.raw_path}")
    print(f"  raw sha256  {response.raw_sha256[:32]}")

    # --------------------------------------------------------------- 3. match
    rule("3. VERIFY EACH CANDIDATE (re-download, re-embed, compare)")
    results = verify(encoder, scan.embedding, response.candidates,
                     threshold=args.threshold, limit=args.limit)
    print(f"  {'sim':>8}  {'ok':>5}  {'social':>6}  {'status':<15} page")
    for r in results:
        sim = f"{r.similarity:.4f}" if r.similarity is not None else "   -- "
        print(f"  {sim:>8}  {str(r.accepted):>5}  {str(r.social):>6}  "
              f"{r.status:<15} {r.page_url[:44]}")

    winner = best(results)
    print(f"\n  image cache {match_module.CACHE.summary}")
    print(f"  threshold   {args.threshold}")
    print(f"  accepted    {sum(1 for r in results if r.accepted)}/{len(results)}")

    # ------------------------------------------------------------ 4. evidence
    rule("4. EVIDENCE BUNDLE")
    bundle = evidence.finalize(evidence.build(
        scan, response, results, args.threshold, args.image.name,
        consent_record=consent_record))
    if face_png:
        # Outside the committed fields on purpose: it is a rendering, not evidence, and
        # the image it renders is already bound by query.image_sha256.
        bundle["face_render"] = face_png.name
    root_hex = bundle["merkle"]["root"]
    print(f"  leaves      {bundle['merkle']['leaf_count']}")
    print(f"  root        {root_hex}")

    if winner is None:
        bundle["attestation"] = {"written": False, "reason": "no candidate cleared threshold"}
        path = evidence.save(bundle)
        print(f"  saved       {path}")
        rule("NO MATCH")
        print("  Nothing written to the chain. A run that found nothing should leave")
        print("  no attestation behind - the record is for matches, not for attempts.")
        return 2

    print(f"  best match  {winner.page_url}")
    print(f"  similarity  {winner.similarity:.4f}")

    # --------------------------------------------------------------- 5. chain
    if args.no_chain:
        path = evidence.save(bundle)
        print(f"\n  --no-chain set; bundle saved to {path}")
        return 0

    rule(f"5. ATTEST ON CHAIN ({args.chain})")
    root = bytes.fromhex(root_hex[2:])
    try:
        chain = Chain(args.chain)
        receipt = chain.record(root, len(results), True, address=args.contract,
                               consent_hash=consent_hash)
    except AlreadyAttested as e:
        # Identical evidence hashes to an identical root by design, so a rerun collides.
        # The commitment is already there, so this is reported rather than failed -
        # verification of this bundle still succeeds against the existing record.
        print("  \033[33malready attested - no new transaction sent\033[0m")
        print(f"  contract    {e.address}")
        print(f"  root        {e.root_hex}")
        print(f"  recorded    {e.record['timestamp']} by {e.record['submitter']}")

        prior = chain.find_record_tx(root, address=args.contract) or {}
        bundle["attestation"] = {
            "written": True,
            "pre_existing": True,
            "network": args.chain,
            "contract": e.address,
            "tx_hash": prior.get("tx_hash"),
            "block": prior.get("block"),
            "gas_used": 0,
            "explorer_url": None,
        }
        path = evidence.save(bundle)
        if prior.get("tx_hash"):
            print(f"  original tx {prior['tx_hash']}  (block {prior['block']})")
        print(f"  bundle      {path}")
        print("\n  For a fresh transaction: py deploy.py, or use a different image.")
        rule("NEXT")
        print(f"  py verify.py --bundle {path} --chain {args.chain}")
        return 0
    except ChainError as e:
        print(f"  {e}", file=sys.stderr)
        bundle["attestation"] = {"written": False, "reason": str(e)}
        evidence.save(bundle)
        return 5

    bundle["attestation"] = {
        "written": True,
        "network": receipt.network,
        "contract": receipt.address,
        "tx_hash": receipt.tx_hash,
        "block": receipt.block,
        "gas_used": receipt.gas_used,
        "explorer_url": receipt.explorer_url,
    }
    path = evidence.save(bundle)

    print(f"  contract    {receipt.address}")
    print(f"  tx          {receipt.tx_hash}")
    print(f"  block       {receipt.block}   gas {receipt.gas_used}")
    if receipt.explorer_url:
        print(f"  explorer    {receipt.explorer_url}")
    print(f"  bundle      {path}")

    rule("NEXT")
    print(f"  py verify.py --bundle {path} --chain {args.chain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

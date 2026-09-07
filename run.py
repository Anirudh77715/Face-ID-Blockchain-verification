"""End-to-end: face scan -> genuine reverse image search -> on-chain attestation.

The photo can be a local file or an image URL, and one URL can serve as both the image to
scan and the public copy the search needs:

    py run.py --image me.jpg --image-url https://...   # local file, hosted copy
    py run.py --image https://...                      # one URL, used for both
    py run.py --image-url https://...                  # same thing, shorter
    py run.py --image me.jpg --chain sepolia           # one real public transaction

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
from pom import faceid as faceid_module
from pom import match as match_module
from pom.chain import AlreadyAttested, Chain, ChainError
from pom.face import COSINE_SAME_IDENTITY, FaceEncoder, NoFaceFound
from pom.match import best, verify
from pom.search import SearchBlocked, SearchUnavailable, get_backend
from pom.source import SourceError, is_url, resolve


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * 68)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image",
                    help="the photo to scan: a local path OR an http(s) image URL. "
                         "If a URL and --image-url is not given, the same URL is used "
                         "for the search.")
    ap.add_argument("--chain", default="local", choices=("local", "sepolia"))
    ap.add_argument("--backend", default="auto",
                    choices=("auto", "bing_url", "yandex_url", "bing_scripted",
                             "serpapi", "replay"),
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
    ap.add_argument("--no-faceid", action="store_true",
                    help="skip the persistent Face ID lookup entirely")
    ap.add_argument("--faceid-high", type=float, metavar="X",
                    help="similarity at or above which a face is treated as the same "
                         f"Face ID (default {faceid_module.DEFAULT_HIGH_CONFIDENCE_THRESHOLD}, "
                         "or POM_FACEID_HIGH)")
    ap.add_argument("--faceid-review", type=float, metavar="X",
                    help="similarity at or above which a face is flagged for review "
                         f"(default {faceid_module.DEFAULT_REVIEW_THRESHOLD}, "
                         "or POM_FACEID_REVIEW)")
    ap.add_argument("--offline", action="store_true",
                    help="no network at all: replay the saved search and use only cached "
                         "candidate images. For rehearsal and reproducibility - the search "
                         "is not live, and the evidence records that")
    args = ap.parse_args()

    # Either flag can carry the photo. Given only --image-url, that URL is both the image
    # to scan and the public copy the search backends need.
    if not args.image and not args.image_url:
        print("give --image (a path or an image URL), or --image-url", file=sys.stderr)
        return 1
    if not args.image:
        args.image = args.image_url
    if is_url(args.image) and not args.image_url:
        args.image_url = args.image

    try:
        source = resolve(args.image)
    except SourceError as e:
        print(e, file=sys.stderr)
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
        scan = encoder.scan(source.data)
    except NoFaceFound as e:
        print(f"  no face detected: {e}", file=sys.stderr)
        return 4
    except ValueError as e:
        print(f"  {e}", file=sys.stderr)
        return 4
    print(f"  source      {source.location}" + (
        "  (downloaded)" if source.origin == "url" else ""))
    print(f"  bbox        {scan.bbox}")
    print(f"  confidence  {scan.score:.4f}   (faces in frame: {scan.faces_found})")
    print(f"  detected at {scan.detect_scale}px wide")
    print(f"  embedding   {scan.embedding.shape[-1]}-d  sha256 {scan.embedding_sha256[:32]}")
    print(f"  image       sha256 {scan.image_sha256[:32]}")

    # Someone re-submitting a photo that has been scanned before is worth saying out
    # loud, and the chain cannot answer it - it holds no filenames and no image hashes
    # in the clear. The local bundles can.
    seen_before = evidence.same_image_runs(scan.image_sha256)
    if seen_before:
        first = seen_before[0]
        print(f"  seen before these exact bytes were scanned in "
              f"{len(seen_before)} earlier run(s)")
        print(f"              first as '{first['source_name']}' on {first['created_at']}")
        names = {r["source_name"] for r in seen_before if r["source_name"]}
        others = sorted(names - {first["source_name"]})
        if others:
            print(f"              also submitted as: {', '.join(others)}")
        print("              (matched on image bytes; the filename is not committed)")

    # A rendering of the detected box, for the viewer and the recording. Derived from
    # the image, whose hash is already committed, so it adds no leaf. Written beside the
    # bundle in evidence/, which is gitignored - it contains the subject's photo.
    face_png = None
    try:
        evidence.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(source.name).stem or "image"
        face_png = evidence.EVIDENCE_DIR / f"face-{stem}.png"
        face_png.write_bytes(encoder.annotate(source.data, scan))
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

    # ------------------------------------------------------------ 1c. face id
    # Deliberately after the consent gate: a scan refused for lack of consent must not
    # leave a biometric record behind. A Face ID is an anonymous application label - it
    # never carries a person's name and says nothing about who anyone is.
    faceid_decision = None
    registry = None
    if not args.no_faceid:
        rule("1c. PERSISTENT FACE ID")
        try:
            thresholds = faceid_module.Thresholds.from_env(
                high=args.faceid_high, review=args.faceid_review)
            registry = faceid_module.FaceRegistry(thresholds=thresholds)
            faceid_decision = registry.observe(
                scan.embedding, scan.image_sha256, encoder.cosine,
                source_name=source.name)
        except faceid_module.FaceIdError as e:
            print(f"  face registry unavailable: {e}")
            print("  The pipeline continues - Face ID is an added layer, not a "
                  "dependency.")
            registry = None
        else:
            d = faceid_decision
            print(f"  face id     {d.face_id}")
            print(f"  status      {d.headline}")
            if d.similarity is not None:
                print(f"  similarity  {d.similarity:.4f}   "
                      f"(match >= {d.thresholds.high}, "
                      f"review >= {d.thresholds.review})")
            else:
                print("  similarity  --      (the registry was empty)")
            print(f"  photos      {d.photo_count} recorded for this face id")
            if d.ranked[:3]:
                print("  ranked      " + ", ".join(
                    f"{fid} {score:.3f}" for fid, score in d.ranked[:3]))
            if d.status == faceid_module.REVIEW:
                print("  REVIEW REQUIRED - too close to call, so a new Face ID was")
                print("  created rather than merging two identities on a guess.")
            print("  note        an anonymous label for a face, not a claim about")
            print("              who anyone is")

    # -------------------------------------------------------------- 2. search
    rule("2. REVERSE IMAGE SEARCH")
    backend = (get_backend(args.backend, headed=args.headed)
               if args.backend in ("auto", "bing_url", "yandex_url", "bing_scripted")
               else get_backend(args.backend))
    if backend.name == "replay":
        print("  [33mREPLAY - no live query; this is a development run[0m")
    print(f"  provider    {backend.name}")
    try:
        response = backend.search(Path(source.location) if source.origin == "file"
                                  else args.image, image_url=args.image_url)
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
    built = evidence.build(
        scan, response, results, args.threshold, source.name,
        consent_record=consent_record)
    if faceid_decision:
        # Committed as a leaf, so which Face ID a run belonged to is as tamper-evident
        # as the rest of the evidence. The embedding itself never goes on chain - only
        # this, and query.embedding_sha256.
        built["faceid"] = faceid_decision.as_dict()
    bundle = evidence.finalize(built)
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

        # Someone has re-submitted a photo that was attested before. The chain cannot
        # say which file it was - it holds no filenames - but the local bundles can.
        earlier = evidence.prior_runs(root_hex, exclude=None)
        if earlier:
            first = earlier[0]
            print(f"  first seen  {first['source_name']}")
            print(f"              {first['path'].name}, {first['created_at']}")
            names = {r["source_name"] for r in earlier if r["source_name"]}
            if len(names) > 1:
                print(f"              also submitted as: "
                      f"{', '.join(sorted(names - {first['source_name']}))}")
            print("              (the filename is not committed on chain - identical")
            print("               bytes under any name produce this same root)")

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

    if registry and faceid_decision and faceid_decision.face_id:
        registry.attach_attestation(
            faceid_decision.face_id, scan.image_sha256,
            {"network": receipt.network, "contract": receipt.address,
             "tx_hash": receipt.tx_hash, "block": receipt.block,
             "root": root_hex, "bundle": path.name})

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

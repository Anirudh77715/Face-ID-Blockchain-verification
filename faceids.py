"""Inspect the persistent Face ID registry, and compare a photo against it.

    py faceids.py list                     every Face ID, with photo counts
    py faceids.py show F-001               one Face ID in detail
    py faceids.py check --image photo.jpg  what would this photo match? (reads only)
    py faceids.py forget F-002 --yes       delete one Face ID and its embeddings

A Face ID is an anonymous label - `F-001` means "the face first seen in that run" and
nothing else. The registry holds no names, and a similarity score is evidence about
faces, never proof of who someone is.

`check` performs no search, writes nothing, and touches no chain. It is the honest way
to demonstrate matching without accumulating registry state mid-recording.
"""

from __future__ import annotations

import argparse
import sys

from pom import faceid as faceid_module
from pom.face import FaceEncoder, NoFaceFound
from pom.source import SourceError, resolve

GREEN, RED, YELLOW, BOLD, DIM, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m")


def registry_for(args) -> faceid_module.FaceRegistry:
    thresholds = faceid_module.Thresholds.from_env(
        high=getattr(args, "high", None), review=getattr(args, "review", None))
    return faceid_module.FaceRegistry(thresholds=thresholds)


def cmd_list(args) -> int:
    reg = registry_for(args)
    if not reg.faces:
        print("no faces registered yet - run the pipeline on a photo first")
        return 0

    print(f"{'face id':10} {'photos':>6}  {'created':22} attested")
    for face in reg.faces:
        attested = len(face.attestations())
        print(f"{face.face_id:10} {face.photo_count:>6}  {face.created_at:22} "
              f"{attested}")
    print(f"\n{len(reg.faces)} face(s)   registry {reg.path}")
    print(f"{DIM}Anonymous labels. The registry stores no names.{OFF}")
    return 0


def cmd_show(args) -> int:
    reg = registry_for(args)
    face = reg.get(args.face_id)
    if not face:
        print(f"no such face id: {args.face_id}", file=sys.stderr)
        return 1

    print(f"{BOLD}{face.face_id}{OFF}   created {face.created_at}")
    print(f"  {face.photo_count} photograph(s) recorded\n")
    for i, s in enumerate(face.sightings):
        print(f"  [{i}] {s.added_at}   {s.source_name or '-'}")
        print(f"      image sha256      {s.image_sha256}")
        print(f"      embedding sha256  {s.embedding_sha256}")
        print(f"      embedding         {len(s.embedding)}-d, held off-chain")
        if s.attestation:
            a = s.attestation
            print(f"      attested          {a.get('network')} "
                  f"tx {str(a.get('tx_hash'))[:22]}...")
            print(f"      root              {str(a.get('root'))[:34]}...")
        else:
            print("      attested          no")
    return 0


def cmd_check(args) -> int:
    """Score a photo against the registry without recording anything."""
    try:
        source = resolve(args.image)
    except SourceError as e:
        print(e, file=sys.stderr)
        return 1

    encoder = FaceEncoder()
    try:
        scan = encoder.scan(source.data)
    except (NoFaceFound, ValueError) as e:
        print(f"no face detected: {e}", file=sys.stderr)
        return 4

    reg = registry_for(args)
    decision = reg.identify(scan.embedding, encoder.cosine)

    print(f"image       {source.name}")
    print(f"image hash  {scan.image_sha256}")
    print(f"{DIM}            a different photo of the same person has a different image")
    print(f"            hash - that is expected, and never used to decide identity{OFF}")
    print(f"thresholds  match >= {decision.thresholds.high}, "
          f"review >= {decision.thresholds.review}")
    print()

    if not decision.ranked:
        print(f"{YELLOW}the registry is empty - nothing to compare against{OFF}")
        return 0

    print(f"{'face id':10} {'similarity':>11}   verdict")
    for face_id, score in decision.ranked:
        state = decision.thresholds.classify(score)
        colour = {faceid_module.MATCH: GREEN,
                  faceid_module.REVIEW: YELLOW}.get(state, DIM)
        print(f"{face_id:10} {score:>11.4f}   {colour}{state}{OFF}")

    print()
    colour = {faceid_module.MATCH: GREEN,
              faceid_module.REVIEW: YELLOW}.get(decision.status, RED)
    print(f"  {colour}{BOLD}{decision.headline}{OFF}")
    if decision.status == faceid_module.MATCH:
        print(f"  {decision.photo_count} photograph(s) already recorded for "
              f"{decision.face_id}")
    print(f"  {DIM}nothing was written - this was a read-only check{OFF}")
    return 0


def cmd_forget(args) -> int:
    reg = registry_for(args)
    face = reg.get(args.face_id)
    if not face:
        print(f"no such face id: {args.face_id}", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"{face.face_id} holds {face.photo_count} embedding(s). "
              f"Re-run with --yes to delete it.")
        return 1

    reg.faces = [f for f in reg.faces if f.face_id != args.face_id]
    reg.save()
    print(f"deleted {args.face_id} and its {face.photo_count} embedding(s)")
    print(f"{DIM}Evidence bundles and chain records are untouched - they record what")
    print(f"was found at the time, and this only forgets the biometric data.{OFF}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--high", type=float, help="override the match threshold")
    ap.add_argument("--review", type=float, help="override the review threshold")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every Face ID")

    show = sub.add_parser("show", help="one Face ID in detail")
    show.add_argument("face_id")

    check = sub.add_parser("check", help="score a photo without recording it")
    check.add_argument("--image", required=True, help="a path or an image URL")

    forget = sub.add_parser("forget", help="delete a Face ID")
    forget.add_argument("face_id")
    forget.add_argument("--yes", action="store_true")

    args = ap.parse_args()
    return {
        "list": cmd_list, "show": cmd_show,
        "check": cmd_check, "forget": cmd_forget,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

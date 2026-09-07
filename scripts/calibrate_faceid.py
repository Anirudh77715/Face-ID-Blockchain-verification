"""Measure where the persistent Face ID thresholds should sit.

    py scripts/calibrate_faceid.py

The web-candidate threshold (0.363, OpenCV's published same-identity figure) is not
automatically right for persistent identity. Those two jobs differ:

  candidate matching   "is this returned image the same face as the query?" - and the
                       candidates are near-duplicates of a photo the subject posted, so
                       the comparison is easy and a permissive threshold is fine.
  persistent Face ID   "is this a person the registry has seen before?" - compared
                       against every stored face, across different photographs, poses
                       and years. Every extra Face ID in the registry is another chance
                       to be wrong, so a false merge is much cheaper to cause and much
                       more damaging: two people collapse into one identity.

So this measures the two distributions on the local set and prints the gap between them.
The numbers it produces are provisional defaults for a 12-image, 8-person set - they are
a starting point chosen from measurement, not an authoritative figure, and the module
docstring in pom/faceid.py says so where a reader will see it.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pom.face import FaceEncoder, NoFaceFound  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FACES = ROOT / "bench" / "faces"


def person(path: Path) -> str:
    """bench/faces names files <person>-<variant>.jpg."""
    return path.stem.split("-")[0]


def main() -> int:
    if not FACES.is_dir():
        print(f"no calibration set at {FACES}", file=sys.stderr)
        return 1

    encoder = FaceEncoder()
    embeddings: dict[Path, object] = {}
    for path in sorted(FACES.glob("*.jpg")):
        try:
            embeddings[path] = encoder.scan_path(path).embedding
        except (NoFaceFound, ValueError) as e:
            print(f"  skipped {path.name}: {e}")

    same, different = [], []
    for a, b in itertools.combinations(sorted(embeddings), 2):
        score = encoder.cosine(embeddings[a], embeddings[b])
        (same if person(a) == person(b) else different).append(
            (score, a.stem, b.stem))

    same.sort(reverse=True)
    different.sort(reverse=True)

    print(f"\nimages {len(embeddings)}   people {len({person(p) for p in embeddings})}")

    print(f"\nSAME PERSON, different photograph  ({len(same)} pairs)")
    for score, a, b in same:
        print(f"  {score:+.4f}  {a} vs {b}")

    print(f"\nDIFFERENT PEOPLE  ({len(different)} pairs) - highest 8 shown")
    for score, a, b in different[:8]:
        print(f"  {score:+.4f}  {a} vs {b}")

    if not same or not different:
        print("\nnot enough pairs to calibrate")
        return 1

    lowest_same = same[-1][0]
    highest_diff = different[0][0]

    print(f"\n  lowest same-person   {lowest_same:+.4f}")
    print(f"  highest different    {highest_diff:+.4f}")
    print(f"  separation           {lowest_same - highest_diff:+.4f}")

    if lowest_same <= highest_diff:
        print("\n  The distributions OVERLAP on this set. No threshold separates them")
        print("  cleanly; REVIEW exists for exactly this region.")
    else:
        print("\n  Suggested, sitting inside the observed gap:")
        print(f"    HIGH_CONFIDENCE  {lowest_same - (lowest_same - highest_diff) / 4:.2f}")
        print(f"    REVIEW           {highest_diff + (lowest_same - highest_diff) / 4:.2f}")
    print("\n  A 12-image set cannot support a false-match rate. Treat these as")
    print("  provisional and re-measure on your own data before relying on them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

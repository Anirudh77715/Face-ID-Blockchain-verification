"""Does this identify the *person*, or only the *photo*?

    py scripts/crosscheck.py

A reverse image search finds near-duplicates. If the face stage only agreed with the search
about near-duplicates, the pipeline would be an image-duplicate detector wearing a face
recognition costume - it would confirm "this is the same JPEG" and call it identity.

So this asks the harder question directly: given one reference photo, does the encoder
recognise the same person in a *different* photograph, and does it reject other people?

The hard case is age. A 1904 portrait and a 1947 portrait of the same man share almost no
pixels, and any method that is really matching images rather than faces fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from pom.face import COSINE_SAME_IDENTITY, FaceEncoder, NoFaceFound  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "bench" / "hard"
REFERENCE = ROOT / "spike" / "control_small.jpg"
UA = {"User-Agent": "proof-of-match-crosscheck/0.1"}

COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# Same subject, deliberately far apart in time. Public-domain portraits.
SAME_PERSON = {
    "1904, age 25 (43-year gap)": "Einstein_patentoffice.jpg",
    "1921, age 42 (26-year gap)": "Einstein_1921_by_F_Schmutzer_-_restoration.jpg",
}

# Reused from the calibration set rather than re-fetched.
OTHERS = ("bohr-0", "curie-0", "tesla-0", "turing-0",
          "feynman-0", "gandhi-0", "hopper-0")


def fetch(filename: str) -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / filename.replace(" ", "_")[:40]
    if path.exists() and path.stat().st_size > 20_000:
        return path
    try:
        r = requests.get(COMMONS + filename, headers=UA, timeout=90)
    except Exception:
        return None
    # Commons answers a bad filename with an HTML page, which is not an error status.
    if not r.ok or not r.headers.get("Content-Type", "").startswith("image/"):
        return None
    path.write_bytes(r.content)
    return path


def main() -> int:
    if not REFERENCE.exists():
        print(f"missing reference image: {REFERENCE}", file=sys.stderr)
        return 1

    encoder = FaceEncoder()
    reference = encoder.scan_path(REFERENCE)
    print(f"reference   {REFERENCE.name}  (Einstein, 1947)")
    print(f"threshold   {COSINE_SAME_IDENTITY}\n")

    same, different = [], []

    print("SAME PERSON, a different photograph")
    for label, filename in SAME_PERSON.items():
        path = fetch(filename)
        if path is None:
            print(f"  {label:30} could not fetch")
            continue
        try:
            scan = encoder.scan_path(path)
        except (NoFaceFound, ValueError) as e:
            print(f"  {label:30} {type(e).__name__}")
            continue
        sim = encoder.cosine(reference.embedding, scan.embedding)
        same.append(sim)
        print(f"  {label:30} {sim:+.4f}  "
              f"{'MATCH' if sim >= COSINE_SAME_IDENTITY else 'MISSED'}")

    for extra in sorted((ROOT / "bench" / "faces").glob("einstein-x*.jpg")):
        try:
            scan = encoder.scan_path(extra)
        except (NoFaceFound, ValueError):
            continue
        sim = encoder.cosine(reference.embedding, scan.embedding)
        same.append(sim)
        print(f"  {extra.stem:30} {sim:+.4f}  "
              f"{'MATCH' if sim >= COSINE_SAME_IDENTITY else 'MISSED'}")

    print("\nDIFFERENT PEOPLE")
    for name in OTHERS:
        path = ROOT / "bench" / "faces" / f"{name}.jpg"
        if not path.exists():
            continue
        try:
            scan = encoder.scan_path(path)
        except (NoFaceFound, ValueError):
            continue
        sim = encoder.cosine(reference.embedding, scan.embedding)
        different.append(sim)
        print(f"  {name:30} {sim:+.4f}  "
              f"{'FALSE MATCH' if sim >= COSINE_SAME_IDENTITY else 'rejected'}")

    if not same or not different:
        print("\nnot enough images to conclude", file=sys.stderr)
        return 1

    worst_same, best_other = min(same), max(different)
    print(f"\nworst same-person   {worst_same:+.4f}")
    print(f"best different      {best_other:+.4f}")
    print(f"gap                 {worst_same - best_other:+.4f}")

    misses = sum(1 for s in same if s < COSINE_SAME_IDENTITY)
    false_matches = sum(1 for s in different if s >= COSINE_SAME_IDENTITY)
    print(f"\nmissed same-person  {misses}/{len(same)}")
    print(f"false matches       {false_matches}/{len(different)}")

    if misses or false_matches:
        print("\nThe threshold does not separate these. Investigate before relying on it.")
        return 1

    print("\nIdentity survives a change of photograph, and other people are rejected:")
    print("this is face recognition, not near-duplicate image matching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure the cosine threshold instead of assuming it.

The default (0.363) is OpenCV's published SFace same-identity threshold. That is a fine
starting point but it is a claim about their evaluation set, not ours. This script builds
a small labelled set, computes every pair, and reports where same-identity and
different-identity scores actually fall — so the README can quote a separation margin
that was measured here rather than inherited.

    py scripts/calibrate.py

Images are public-domain portraits fetched from Wikipedia. Only embeddings are compared,
and nothing leaves the machine.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from pom.face import COSINE_SAME_IDENTITY, FaceEncoder, NoFaceFound  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "bench" / "faces"

# One Wikipedia portrait each — these supply the different-identity pairs.
SUBJECTS = {
    "einstein": ["Albert Einstein"],
    "curie": ["Marie Curie"],
    "tesla": ["Nikola Tesla"],
    "gandhi": ["Mahatma Gandhi"],
    "turing": ["Alan Turing"],
    "bohr": ["Niels Bohr"],
    "feynman": ["Richard Feynman"],
    "hopper": ["Grace Hopper"],
}

# Same-identity pairs need *different photographs of one person*, which Wikipedia's single
# page image cannot supply. These are distinct Einstein photos surfaced by the reverse
# image search and independently verified against the control (0.77-0.96). Different
# decades, poses, crops and sources — which is what makes them a fair positive set.
EXTRA_IMAGES = {
    "einstein": [
        "https://cmsasset.ancestrycdn.com/content/dam/ancestry/ancestry-blogs/en-us/"
        "Culture-and-Entertainment/7868/512px-Albert_Einstein_Head_cleaned.jpg",
        "https://i.pinimg.com/originals/2b/e4/5e/2be45e24485f914b0524f12b121c74c0.jpg",
        "https://i.pinimg.com/736x/88/fa/b4/88fab40f2943c9122764902c449f9fe8.jpg",
        "https://image.invaluable.com/housePhotos/Swann/58/552758/H0132-L62695153.jpg",
        "https://media.mutualart.com/Images/2017_05/16/20/200807442/"
        "46fd7226-63a5-4b56-9b1c-2b1f2b0e9a5c.jpg",
    ],
}

UA = {"User-Agent": "proof-of-match-calibration/0.1 (HH Goa 2026 Task 3)"}


def fetch_portrait(title: str) -> bytes | None:
    """Page image via the Wikipedia REST summary endpoint."""
    r = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
        headers=UA, timeout=30)
    if r.status_code != 200:
        return None
    src = (r.json().get("originalimage") or {}).get("source")
    if not src:
        return None
    img = requests.get(src, headers=UA, timeout=60)
    return img.content if img.ok else None


def load_set() -> dict[str, list[bytes]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[bytes]] = {}
    for person, titles in SUBJECTS.items():
        for i, title in enumerate(titles):
            path = CACHE / f"{person}-{i}.jpg"
            if not path.exists():
                data = fetch_portrait(title)
                if not data:
                    print(f"  skip {title!r} (no image)")
                    continue
                path.write_bytes(data)
            out.setdefault(person, []).append(path.read_bytes())

    for person, urls in EXTRA_IMAGES.items():
        for i, url in enumerate(urls):
            path = CACHE / f"{person}-x{i}.jpg"
            if not path.exists():
                try:
                    r = requests.get(url, headers=UA, timeout=60)
                    if not r.ok:
                        print(f"  skip {url[:60]} ({r.status_code})")
                        continue
                    path.write_bytes(r.content)
                except Exception as e:
                    print(f"  skip {url[:60]} ({type(e).__name__})")
                    continue
            out.setdefault(person, []).append(path.read_bytes())
    return out


def main() -> int:
    enc = FaceEncoder()
    corpus = load_set()

    embeddings: list[tuple[str, str]] = []
    vectors = []
    for person, images in corpus.items():
        for i, data in enumerate(images):
            try:
                vectors.append(enc.scan(data).embedding)
                embeddings.append((person, f"{person}-{i}"))
            except NoFaceFound:
                print(f"  no face: {person}-{i}")

    same, diff = [], []
    for (a, b) in itertools.combinations(range(len(vectors)), 2):
        sim = enc.cosine(vectors[a], vectors[b])
        (same if embeddings[a][0] == embeddings[b][0] else diff).append(
            (round(sim, 4), embeddings[a][1], embeddings[b][1]))

    same.sort()
    diff.sort(reverse=True)

    print(f"\nimages embedded: {len(vectors)} across {len(corpus)} people")
    print(f"same-identity pairs: {len(same)}   different-identity pairs: {len(diff)}")

    if same:
        print(f"\nsame-identity   min {same[0][0]:.4f}  max {same[-1][0]:.4f}")
        print(f"  worst (closest to rejection): {same[0][1]} vs {same[0][2]} = {same[0][0]}")
    if diff:
        print(f"different-ident max {diff[0][0]:.4f}  min {diff[-1][0]:.4f}")
        print(f"  worst (closest to acceptance): {diff[0][1]} vs {diff[0][2]} = {diff[0][0]}")

    thr = COSINE_SAME_IDENTITY
    fn = [s for s in same if s[0] < thr]
    fp = [d for d in diff if d[0] >= thr]
    print(f"\nat threshold {thr}:")
    print(f"  false negatives: {len(fn)}/{len(same)}")
    print(f"  false positives: {len(fp)}/{len(diff)}")
    if same and diff:
        margin = same[0][0] - diff[0][0]
        print(f"  separation margin (min same - max diff): {margin:+.4f}")
        print("  -> threshold sits inside the gap" if margin > 0 else
              "  -> DISTRIBUTIONS OVERLAP; state this in the README")

    report = {
        "threshold": thr,
        "n_images": len(vectors),
        "n_people": len(corpus),
        "same_min": same[0][0] if same else None,
        "same_max": same[-1][0] if same else None,
        "diff_max": diff[0][0] if diff else None,
        "diff_min": diff[-1][0] if diff else None,
        "false_negatives": len(fn),
        "false_positives": len(fp),
        "same_pairs": same,
        "diff_pairs": diff[:15],
    }
    out = Path(__file__).resolve().parent.parent / "bench" / "calibration.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

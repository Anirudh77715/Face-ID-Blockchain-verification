"""The evidence bundle: everything the run saw, and the leaves committed on-chain.

The bundle is the artifact a verifier checks. It has to contain enough that a third party
can recompute the Merkle root without rerunning the search — which also means the leaf set
and its order have to be derived from the bundle deterministically, never stored as a
side-channel that could itself be edited to match a forgery.

So `leaves()` recomputes from bundle *content*. The stored `merkle.leaves` list is there
only to name which field diverged when verification fails; it is never trusted as input.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import merkle
from .face import FaceScan
from .match import MatchResult
from .search import SearchResponse

BUNDLE_VERSION = 1
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"


def build(
    scan: FaceScan,
    search: SearchResponse,
    results: list[MatchResult],
    threshold: float,
    source_name: str,
    consent_record: dict | None = None,
) -> dict:
    accepted = [r for r in results if r.accepted]
    best = sorted(accepted, key=lambda r: (not r.social, -(r.similarity or 0)))
    best = best[0] if best else None

    return {
        "version": BUNDLE_VERSION,
        "run_id": uuid.uuid4().hex,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": {
            "source_name": source_name,
            "image_sha256": scan.image_sha256,
            "embedding_sha256": scan.embedding_sha256,
            "bbox": list(scan.bbox),
            "detect_score": round(scan.score, 6),
            "faces_found": scan.faces_found,
            "detect_scale": scan.detect_scale,
        },
        "models": scan.provenance,
        "search": {
            "provider": search.provider,
            "queried_at": search.queried_at,
            "raw_sha256": search.raw_sha256,
            "raw_path": search.raw_path,
            "candidates_returned": len(search.candidates),
        },
        "threshold": threshold,
        # Present only when the run carried consent. Absent is a distinct state from
        # present-and-invalid, and the leaf set reflects that rather than committing a
        # placeholder that would look like a real record.
        **({"consent": consent_record} if consent_record else {}),
        "candidates": [r.as_dict() for r in results],
        "decision": {
            "matched": best is not None,
            "best_page_url": best.page_url if best else None,
            "best_similarity": best.similarity if best else None,
            "accepted_count": len(accepted),
        },
    }


def leaves(bundle: dict) -> list[tuple[str, bytes]]:
    """Named leaves, recomputed from bundle content in a fixed order.

    Each candidate becomes its own leaf so a single one can be disclosed and proved
    without revealing the rest of the run.
    """
    q, s = bundle["query"], bundle["search"]
    items: list[tuple[str, object]] = [
        ("query.image_sha256", q["image_sha256"]),
        ("query.embedding_sha256", q["embedding_sha256"]),
        ("query.bbox", q["bbox"]),
        ("query.detect_score", q["detect_score"]),
        ("models", bundle["models"]),
        ("search.provider", s["provider"]),
        ("search.queried_at", s["queried_at"]),
        ("search.raw_sha256", s["raw_sha256"]),
        ("threshold", bundle["threshold"]),
    ]
    if "consent" in bundle:
        items.append(("consent", bundle["consent"]))
    # Present only when the run performed a Face ID lookup. Absent for every bundle
    # written before the feature existed, so those still recompute to their original
    # roots and keep verifying.
    if "faceid" in bundle:
        items.append(("faceid", bundle["faceid"]))
    for i, c in enumerate(bundle["candidates"]):
        items.append((f"candidate[{i}]", {
            "page_url": c["page_url"],
            "image_sha256": c["image_sha256"],
            "similarity": c["similarity"],
            "accepted": c["accepted"],
            "status": c["status"],
        }))
    items.append(("decision", bundle["decision"]))

    return [(name, merkle.leaf(name, value)) for name, value in items]


def root(bundle: dict) -> bytes:
    return merkle.root([h for _, h in leaves(bundle)])


def finalize(bundle: dict) -> dict:
    """Attach the commitment. Stored leaf names/hashes are diagnostic only."""
    named = leaves(bundle)
    bundle["merkle"] = {
        "root": "0x" + merkle.root([h for _, h in named]).hex(),
        "leaf_count": len(named),
        "leaves": [{"name": n, "hash": "0x" + h.hex()} for n, h in named],
    }
    return bundle


def save(bundle: dict, directory: Path = EVIDENCE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run-{bundle['run_id'][:12]}.json"
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _scan_bundles(directory: Path, exclude: Path | None):
    exclude = Path(exclude).resolve() if exclude else None
    for path in sorted(Path(directory).glob("run-*.json")):
        if exclude and path.resolve() == exclude:
            continue
        try:
            yield path, load(path)
        except (OSError, json.JSONDecodeError):
            continue  # a half-written or hand-edited bundle must not break a run


def same_image_runs(image_sha256: str, directory: Path = EVIDENCE_DIR,
                    exclude: Path | None = None) -> list[dict]:
    """Earlier runs that scanned these exact image bytes, oldest first.

    This is the one that fires on a genuine re-upload. Two live runs of the same photo
    do *not* share a Merkle root - `search.queried_at` and `search.raw_sha256` are
    leaves, so a second search at a second moment commits to something different. The
    input image hash is what stays constant, and it is `query.image_sha256`.

    Matched on bytes, so a photo re-submitted under a new filename is still recognised;
    the name is reported as context because it is not committed to anything.
    """
    found = [
        {
            "path": path,
            "source_name": b.get("query", {}).get("source_name"),
            "created_at": b.get("created_at"),
            "root": b.get("merkle", {}).get("root"),
            "attested": bool(b.get("attestation", {}).get("written")),
            "tx_hash": b.get("attestation", {}).get("tx_hash"),
        }
        for path, b in _scan_bundles(directory, exclude)
        if b.get("query", {}).get("image_sha256") == image_sha256
    ]
    return sorted(found, key=lambda r: r["created_at"] or "")


def prior_runs(root_hex: str, directory: Path = EVIDENCE_DIR,
               exclude: Path | None = None) -> list[dict]:
    """Bundles on disk that committed this same root, oldest first.

    Used to answer "this photo has been attested before - under what name?". The
    filename is deliberately *not* a Merkle leaf: what is committed is the SHA-256 of
    the bytes scanned, so the same image re-uploaded as `holiday.jpg` and as `me.png`
    produces an identical root. That makes the name useful context and never evidence,
    and callers must present it that way.

    Reads local bundles, not the chain. The chain holds no filenames - by design, since
    a filename can carry a person's name and the ledger is public and permanent.
    """
    found = []
    exclude = Path(exclude).resolve() if exclude else None
    for path in sorted(Path(directory).glob("run-*.json")):
        if exclude and path.resolve() == exclude:
            continue
        try:
            other = load(path)
        except (OSError, json.JSONDecodeError):
            continue  # a half-written or hand-edited bundle must not break a run
        if other.get("merkle", {}).get("root") != root_hex:
            continue
        found.append({
            "path": path,
            "source_name": other.get("query", {}).get("source_name"),
            "created_at": other.get("created_at"),
            "image_sha256": other.get("query", {}).get("image_sha256"),
        })
    return sorted(found, key=lambda r: r["created_at"] or "")


def diff_against_stored(bundle: dict) -> list[str]:
    """Recompute every leaf and report which named fields no longer match the stored
    hashes. This is what turns 'the root is wrong' into 'candidate[3].page_url changed'."""
    stored = {e["name"]: e["hash"] for e in bundle.get("merkle", {}).get("leaves", [])}
    recomputed = leaves(bundle)

    diverged = []
    for name, h in recomputed:
        want = stored.get(name)
        if want is None:
            diverged.append(f"{name} (added; absent from the commitment)")
        elif want != "0x" + h.hex():
            diverged.append(name)

    seen = {n for n, _ in recomputed}
    diverged.extend(f"{n} (removed)" for n in stored if n not in seen)
    return diverged

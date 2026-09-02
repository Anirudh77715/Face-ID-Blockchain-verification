"""Verify candidates instead of trusting them.

A reverse image search returns pages that look *visually similar*. That is not the same
claim as "this is the same face", and a pipeline that reports search hits as identity
matches is asserting something it never checked.

So every candidate image is re-downloaded, re-encoded with the same model, and scored
against the query embedding. Only candidates clearing the cosine threshold are accepted,
and the score is recorded either way — including for rejects, so the bundle shows what was
considered and dismissed rather than only what survived.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .face import COSINE_SAME_IDENTITY, FaceEncoder, NoFaceFound, sha256_bytes
from .search import Candidate

MAX_IMAGE_BYTES = 12 * 1024 * 1024
DOWNLOAD_TIMEOUT = 15
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@dataclass
class MatchResult:
    page_url: str
    image_url: str
    status: str           # ok | no_image_url | download_failed | not_an_image | no_face
    similarity: float | None
    accepted: bool
    social: bool
    image_sha256: str | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _fetch(url: str) -> bytes:
    import requests

    with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True,
                      headers={"User-Agent": UA}) as r:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if ctype and not ctype.split(";")[0].strip().startswith("image/"):
            raise ValueError(f"content-type {ctype!r} is not an image")

        buf = bytearray()
        for chunk in r.iter_content(64 * 1024):
            buf.extend(chunk)
            if len(buf) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
        return bytes(buf)


def verify(
    encoder: FaceEncoder,
    query_embedding,
    candidates: list[Candidate],
    threshold: float = COSINE_SAME_IDENTITY,
    limit: int = 12,
    social_only: bool = True,
) -> list[MatchResult]:
    """Score candidates against the query face. Social candidates are checked first so
    the run reaches a usable result early rather than burning the budget on stock-photo
    sites, but non-social ones still get checked if budget remains."""
    ordered = sorted(candidates, key=lambda c: not c.social) if social_only else candidates

    results: list[MatchResult] = []
    for cand in ordered[:limit]:
        base = {"page_url": cand.page_url, "image_url": cand.image_url,
                "social": cand.social}

        if not cand.image_url:
            results.append(MatchResult(**base, status="no_image_url",
                                       similarity=None, accepted=False))
            continue

        try:
            data = _fetch(cand.image_url)
        except Exception as e:
            results.append(MatchResult(**base, status="download_failed", similarity=None,
                                       accepted=False, note=f"{type(e).__name__}: {e}"[:160]))
            continue

        try:
            scan = encoder.scan(data)
        except NoFaceFound:
            results.append(MatchResult(**base, status="no_face", similarity=None,
                                       accepted=False, image_sha256=sha256_bytes(data)))
            continue
        except Exception as e:
            results.append(MatchResult(**base, status="not_an_image", similarity=None,
                                       accepted=False, note=f"{type(e).__name__}: {e}"[:160]))
            continue

        sim = encoder.cosine(query_embedding, scan.embedding)
        results.append(MatchResult(
            **base,
            status="ok",
            similarity=round(sim, 6),
            accepted=bool(sim >= threshold),
            image_sha256=scan.image_sha256,
        ))

    return results


def best(results: list[MatchResult]) -> MatchResult | None:
    """Highest-scoring accepted match, preferring social sources."""
    ok = [r for r in results if r.accepted]
    if not ok:
        return None
    return sorted(ok, key=lambda r: (not r.social, -(r.similarity or 0)))[0]

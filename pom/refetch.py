"""Re-fetch the discovered post and compare it against what was attested.

Checking the evidence bundle proves that the *file* has not been altered since it was
committed. It does not prove that the *post* is still what it was. Those are different
claims, and the second one is the one a viewer usually has in mind:

    get the post again -> hash it again -> compare against the hash on chain

That is what this module does. Two things it deliberately does not do:

**It never reads the cache.** `pom.cache` exists so a rerun reproduces rather than drifts,
which is exactly wrong here - the cached bytes are a copy of what was already hashed, so
comparing them against their own digest would always agree and prove nothing. Every
re-check goes to the network.

**A mismatch is not tampering.** A post can be deleted, edited by its author, or served
re-encoded by a CDN that never touched the pixels. All of those change the hash with
nobody acting in bad faith. So `changed` and `unreachable` are reported as their own
outcomes rather than folded into the bundle's tamper verdict, which stays reserved for
evidence that no longer matches its own commitment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .face import sha256_bytes

UNCHANGED = "unchanged"
CHANGED = "changed"
UNREACHABLE = "unreachable"
NOT_RECORDED = "not_recorded"


@dataclass(frozen=True)
class Recheck:
    """One candidate, re-downloaded and re-hashed."""

    page_url: str
    image_url: str | None
    attested_sha256: str | None
    current_sha256: str | None
    status: str
    bytes_read: int = 0
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == UNCHANGED


def live_download(url: str) -> bytes:
    """Always the network, never the cache. See the module docstring."""
    from .match import _download

    return _download(url)


def recheck(candidate: dict, download: Callable[[str], bytes] = live_download) -> Recheck:
    """Re-download one candidate's image and compare its SHA-256 to the attested one."""
    page = candidate.get("page_url") or ""
    image = candidate.get("image_url")
    attested = candidate.get("image_sha256")

    if not attested or not image:
        # A candidate that failed to download during the run has no hash to compare
        # against. Saying so is honest; treating it as a match would not be.
        return Recheck(page, image, attested, None, NOT_RECORDED,
                       note="no image hash was recorded for this candidate")

    try:
        data = download(image)
    except Exception as e:
        return Recheck(page, image, attested, None, UNREACHABLE,
                       note=f"{type(e).__name__}: {e}"[:160])

    current = sha256_bytes(data)
    return Recheck(page, image, attested, current,
                   UNCHANGED if current == attested else CHANGED, len(data))


def recheck_all(candidates: list[dict],
                download: Callable[[str], bytes] = live_download,
                only_accepted: bool = True) -> list[Recheck]:
    return [recheck(c, download) for c in candidates
            if not only_accepted or c.get("accepted")]


def summarise(results: list[Recheck]) -> dict[str, int]:
    counts = {UNCHANGED: 0, CHANGED: 0, UNREACHABLE: 0, NOT_RECORDED: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts

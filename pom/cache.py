"""Content-addressed cache for candidate images.

Three reasons this exists, in order of how much they matter:

1. Offline runs. Replaying a saved search still re-downloaded every candidate image, so
   "offline" was never actually offline. With a warm cache the whole pipeline - face,
   search, verification, commitment, chain - runs with the network unplugged.
2. Rehearsal. A demo you practise five times should not hit third-party image hosts five
   times. That is both slow and impolite.
3. Determinism. A candidate image that changes or 404s between runs silently changes the
   evidence. Cached bytes make a rerun reproduce rather than drift.

Keyed by SHA-256 of the URL, storing the bytes verbatim. The stored bytes are what get
hashed into the evidence bundle, so a cache hit and a live fetch of unchanged content
produce an identical commitment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "cache"


class OfflineMiss(Exception):
    """Offline mode was requested and the URL is not cached."""


class ImageCache:
    def __init__(self, directory: Path = CACHE_DIR, offline: bool = False):
        self.dir = Path(directory)
        self.offline = offline
        self.hits = 0
        self.misses = 0
        self.stored = 0

    # ------------------------------------------------------------------ paths

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _path(self, url: str) -> Path:
        return self.dir / f"{self._key(url)}.bin"

    def _meta_path(self, url: str) -> Path:
        return self.dir / f"{self._key(url)}.json"

    # ------------------------------------------------------------------- api

    def get(self, url: str) -> bytes | None:
        path = self._path(url)
        if path.exists():
            self.hits += 1
            return path.read_bytes()
        self.misses += 1
        return None

    def put(self, url: str, data: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(url).write_bytes(data)
        # The URL is kept alongside so a cache directory stays inspectable; the filename
        # alone is a hash and tells a reader nothing.
        self._meta_path(url).write_text(
            json.dumps({"url": url, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}, indent=2),
            encoding="utf-8")
        self.stored += 1

    def fetch(self, url: str, downloader) -> bytes:
        """Cache-first fetch. `downloader` is called only on a miss, and never offline."""
        cached = self.get(url)
        if cached is not None:
            return cached

        if self.offline:
            raise OfflineMiss(
                f"offline mode and this image is not cached:\n  {url}\n"
                "  Warm the cache with one online run first."
            )

        data = downloader(url)
        self.put(url, data)
        return data

    # ---------------------------------------------------------------- report

    @property
    def summary(self) -> str:
        total = self.hits + self.misses
        if not total:
            return "unused"
        return (f"{self.hits}/{total} hits"
                + (f", {self.stored} stored" if self.stored else ""))

    def count(self) -> int:
        return len(list(self.dir.glob("*.bin"))) if self.dir.exists() else 0

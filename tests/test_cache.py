"""Image cache and offline behaviour."""

from __future__ import annotations

import pytest

from pom.cache import ImageCache, OfflineMiss


def downloader(payload: bytes = b"bytes"):
    calls = []

    def fn(url: str) -> bytes:
        calls.append(url)
        return payload

    fn.calls = calls
    return fn


def test_first_fetch_downloads_and_stores(tmp_path):
    cache = ImageCache(tmp_path)
    dl = downloader()
    assert cache.fetch("https://e/1.jpg", dl) == b"bytes"
    assert dl.calls == ["https://e/1.jpg"]
    assert cache.stored == 1


def test_second_fetch_does_not_download(tmp_path):
    cache = ImageCache(tmp_path)
    dl = downloader()
    cache.fetch("https://e/1.jpg", dl)
    cache.fetch("https://e/1.jpg", dl)
    assert dl.calls == ["https://e/1.jpg"], "a cache hit must not hit the network"
    assert cache.hits == 1


def test_cache_survives_a_new_instance(tmp_path):
    ImageCache(tmp_path).fetch("https://e/1.jpg", downloader(b"abc"))

    fresh = ImageCache(tmp_path)
    dl = downloader(b"different")
    assert fresh.fetch("https://e/1.jpg", dl) == b"abc"
    assert dl.calls == []


def test_bytes_are_returned_verbatim(tmp_path):
    """The stored bytes are hashed into the evidence, so a cache hit and a live fetch of
    unchanged content must produce an identical commitment."""
    payload = bytes(range(256))
    cache = ImageCache(tmp_path)
    cache.fetch("https://e/x.png", downloader(payload))
    assert ImageCache(tmp_path).fetch("https://e/x.png", downloader(b"nope")) == payload


def test_distinct_urls_do_not_collide(tmp_path):
    cache = ImageCache(tmp_path)
    cache.fetch("https://e/1.jpg", downloader(b"one"))
    cache.fetch("https://e/2.jpg", downloader(b"two"))
    assert cache.get("https://e/1.jpg") == b"one"
    assert cache.get("https://e/2.jpg") == b"two"


def test_offline_miss_raises_rather_than_downloading(tmp_path):
    cache = ImageCache(tmp_path, offline=True)
    dl = downloader()
    with pytest.raises(OfflineMiss, match="not cached"):
        cache.fetch("https://e/1.jpg", dl)
    assert dl.calls == [], "offline mode must never reach for the network"


def test_offline_hit_works(tmp_path):
    ImageCache(tmp_path).fetch("https://e/1.jpg", downloader(b"warm"))

    offline = ImageCache(tmp_path, offline=True)
    dl = downloader()
    assert offline.fetch("https://e/1.jpg", dl) == b"warm"
    assert dl.calls == []


def test_offline_miss_names_the_url(tmp_path):
    with pytest.raises(OfflineMiss) as e:
        ImageCache(tmp_path, offline=True).fetch("https://e/missing.jpg", downloader())
    assert "https://e/missing.jpg" in str(e.value)
    assert "warm the cache" in str(e.value).lower()


def test_metadata_keeps_the_directory_inspectable(tmp_path):
    """Filenames are hashes and tell a reader nothing on their own."""
    import json
    cache = ImageCache(tmp_path)
    cache.fetch("https://e/photo.jpg", downloader(b"abc"))
    meta = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert meta["url"] == "https://e/photo.jpg"
    assert meta["bytes"] == 3


def test_summary_reports_usage(tmp_path):
    cache = ImageCache(tmp_path)
    assert cache.summary == "unused"
    cache.fetch("https://e/1.jpg", downloader())
    cache.fetch("https://e/1.jpg", downloader())
    assert "1/2 hits" in cache.summary


def test_count_reflects_stored_files(tmp_path):
    cache = ImageCache(tmp_path)
    assert cache.count() == 0
    cache.fetch("https://e/1.jpg", downloader())
    assert cache.count() == 1


def test_downloader_errors_propagate(tmp_path):
    def boom(url):
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        ImageCache(tmp_path).fetch("https://e/1.jpg", boom)


def test_a_failed_download_is_not_cached(tmp_path):
    def boom(url):
        raise ConnectionError("down")

    cache = ImageCache(tmp_path)
    with pytest.raises(ConnectionError):
        cache.fetch("https://e/1.jpg", boom)
    assert cache.get("https://e/1.jpg") is None
    assert cache.count() == 0

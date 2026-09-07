"""Re-fetching the discovered post and comparing it against what was attested.

The interesting branches cannot be produced on demand against a real third-party post -
you cannot make someone edit their Pinterest image to order - so they are driven here
with a stub downloader.
"""

from __future__ import annotations

import hashlib

from pom import refetch

PAYLOAD = b"the post's image bytes"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def candidate(**over) -> dict:
    base = {
        "page_url": "https://social.example/post/1",
        "image_url": "https://cdn.example/1.jpg",
        "image_sha256": DIGEST,
        "similarity": 0.71,
        "accepted": True,
        "status": "ok",
    }
    base.update(over)
    return base


def downloader(payload: bytes = PAYLOAD):
    calls = []

    def fn(url: str) -> bytes:
        calls.append(url)
        return payload

    fn.calls = calls
    return fn


def boom(exc: Exception):
    def fn(url: str) -> bytes:
        raise exc

    return fn


# ------------------------------------------------------------------- outcomes

def test_unchanged_when_the_post_still_hashes_the_same():
    result = refetch.recheck(candidate(), downloader())
    assert result.status == refetch.UNCHANGED
    assert result.ok
    assert result.current_sha256 == result.attested_sha256 == DIGEST
    assert result.bytes_read == len(PAYLOAD)


def test_changed_when_the_bytes_differ():
    result = refetch.recheck(candidate(), downloader(b"a different image"))
    assert result.status == refetch.CHANGED
    assert not result.ok
    assert result.current_sha256 != result.attested_sha256


def test_unreachable_is_not_reported_as_changed():
    """A deleted post or a hotlink block must not read as evidence of tampering."""
    result = refetch.recheck(candidate(), boom(OSError("404")))
    assert result.status == refetch.UNREACHABLE
    assert result.status != refetch.CHANGED
    assert result.current_sha256 is None
    assert "404" in result.note


def test_candidate_without_a_recorded_hash_is_not_a_match():
    result = refetch.recheck(candidate(image_sha256=None), downloader())
    assert result.status == refetch.NOT_RECORDED
    assert not result.ok


def test_candidate_without_an_image_url_is_not_a_match():
    result = refetch.recheck(candidate(image_url=None), downloader())
    assert result.status == refetch.NOT_RECORDED


def test_the_url_actually_fetched_is_the_image_not_the_page():
    dl = downloader()
    refetch.recheck(candidate(), dl)
    assert dl.calls == ["https://cdn.example/1.jpg"]


# ----------------------------------------------------------------- the set

def test_recheck_all_skips_rejected_candidates_by_default():
    dl = downloader()
    results = refetch.recheck_all(
        [candidate(), candidate(accepted=False), candidate()], dl)
    assert len(results) == 2
    assert len(dl.calls) == 2


def test_recheck_all_can_include_rejected_candidates():
    results = refetch.recheck_all(
        [candidate(), candidate(accepted=False)], downloader(), only_accepted=False)
    assert len(results) == 2


def test_summarise_counts_every_outcome():
    counts = refetch.summarise([
        refetch.recheck(candidate(), downloader()),
        refetch.recheck(candidate(), downloader(b"other")),
        refetch.recheck(candidate(), boom(OSError("gone"))),
        refetch.recheck(candidate(image_sha256=None), downloader()),
    ])
    assert counts == {
        refetch.UNCHANGED: 1,
        refetch.CHANGED: 1,
        refetch.UNREACHABLE: 1,
        refetch.NOT_RECORDED: 1,
    }


# --------------------------------------------------------------- the cache

def test_live_download_bypasses_the_cache(monkeypatch):
    """The whole check is vacuous if it reads back the bytes it already hashed.

    pom.match._fetch is the cache-first path used during a run; refetch must call
    _download, which always goes to the network.
    """
    from pom import match

    monkeypatch.setattr(match, "_download", lambda url: b"live bytes")
    monkeypatch.setattr(match, "_fetch",
                        lambda url: (_ for _ in ()).throw(
                            AssertionError("refetch used the cache")))

    assert refetch.live_download("https://cdn.example/1.jpg") == b"live bytes"


def test_default_downloader_is_the_live_one():
    assert refetch.recheck.__defaults__[0] is refetch.live_download

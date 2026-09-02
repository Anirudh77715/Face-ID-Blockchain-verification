"""Candidate verification: thresholding, ordering, and failure classification.

Network access is stubbed so these stay in the always-run tier. What is under test is the
decision logic, not requests.
"""

from __future__ import annotations

import numpy as np
import pytest

from pom import match as M
from pom.match import MatchResult, best, verify
from pom.search import Candidate


class FakeEncoder:
    """Maps image bytes to a fixed similarity, so thresholds can be tested exactly."""

    def __init__(self, table: dict[bytes, float], no_face: set[bytes] = frozenset()):
        self.table = table
        self.no_face = set(no_face)

    def scan(self, data: bytes):
        from pom.face import NoFaceFound
        if data in self.no_face:
            raise NoFaceFound("stub")
        if data not in self.table:
            raise ValueError("stub: undecodable")
        return type("Scan", (), {
            "embedding": np.array([[self.table[data]]], np.float32),
            "image_sha256": "s" * 64,
        })()

    def cosine(self, a, b) -> float:
        return float(b[0][0])


@pytest.fixture
def stub_fetch(monkeypatch):
    store: dict[str, bytes] = {}

    def fake(url: str) -> bytes:
        if url not in store:
            raise ConnectionError(f"stub has no {url}")
        return store[url]

    monkeypatch.setattr(M, "_fetch", fake)
    return store


def cand(page="https://x.com/a", image="https://i/a.jpg", social=True):
    return Candidate(page_url=page, image_url=image, social=social)


def test_candidate_above_threshold_is_accepted(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    enc = FakeEncoder({b"A": 0.90})
    r = verify(enc, None, [cand()], threshold=0.363)
    assert r[0].status == "ok"
    assert r[0].accepted
    assert r[0].similarity == pytest.approx(0.90)


def test_candidate_below_threshold_is_rejected_but_still_scored(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    enc = FakeEncoder({b"A": 0.20})
    r = verify(enc, None, [cand()], threshold=0.363)
    assert r[0].status == "ok"
    assert not r[0].accepted
    assert r[0].similarity == pytest.approx(0.20), (
        "rejects must keep their score - the bundle should show what was dismissed")


def test_threshold_boundary_is_inclusive(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    enc = FakeEncoder({b"A": 0.363})
    assert verify(enc, None, [cand()], threshold=0.363)[0].accepted


def test_just_below_threshold_is_rejected(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    enc = FakeEncoder({b"A": 0.3629})
    assert not verify(enc, None, [cand()], threshold=0.363)[0].accepted


def test_missing_image_url_is_classified(stub_fetch):
    r = verify(FakeEncoder({}), None, [cand(image="")], threshold=0.363)
    assert r[0].status == "no_image_url"
    assert r[0].similarity is None and not r[0].accepted


def test_download_failure_is_classified_and_noted(stub_fetch):
    r = verify(FakeEncoder({}), None, [cand()], threshold=0.363)
    assert r[0].status == "download_failed"
    assert r[0].note
    assert not r[0].accepted


def test_candidate_without_a_face_is_classified(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    enc = FakeEncoder({}, no_face={b"A"})
    r = verify(enc, None, [cand()], threshold=0.363)
    assert r[0].status == "no_face"
    assert not r[0].accepted


def test_undecodable_candidate_is_classified(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"junk"
    r = verify(FakeEncoder({}), None, [cand()], threshold=0.363)
    assert r[0].status == "not_an_image"
    assert not r[0].accepted


def test_no_single_failure_aborts_the_batch(stub_fetch):
    stub_fetch["https://i/b.jpg"] = b"B"
    enc = FakeEncoder({b"B": 0.8})
    cands = [cand(image=""), cand(page="https://x.com/dead", image="https://i/dead.jpg"),
             cand(page="https://x.com/b", image="https://i/b.jpg")]
    r = verify(enc, None, cands, threshold=0.363)
    assert len(r) == 3
    assert [x.status for x in r] == ["no_image_url", "download_failed", "ok"]


def test_social_candidates_are_checked_first(stub_fetch):
    for i in "abc":
        stub_fetch[f"https://i/{i}.jpg"] = i.encode()
    enc = FakeEncoder({b"a": 0.5, b"b": 0.5, b"c": 0.5})
    cands = [
        cand(page="https://example.com/1", image="https://i/a.jpg", social=False),
        cand(page="https://example.com/2", image="https://i/b.jpg", social=False),
        cand(page="https://x.com/3", image="https://i/c.jpg", social=True),
    ]
    r = verify(enc, None, cands, threshold=0.363)
    assert r[0].page_url == "https://x.com/3"


def test_limit_caps_the_number_verified(stub_fetch):
    for i in range(5):
        stub_fetch[f"https://i/{i}.jpg"] = str(i).encode()
    enc = FakeEncoder({str(i).encode(): 0.9 for i in range(5)})
    cands = [cand(page=f"https://x.com/{i}", image=f"https://i/{i}.jpg")
             for i in range(5)]
    assert len(verify(enc, None, cands, threshold=0.363, limit=2)) == 2


def test_empty_candidate_list_is_fine():
    assert verify(FakeEncoder({}), None, [], threshold=0.363) == []


# ------------------------------------------------------------------------- best()

def mk(sim, accepted, social, page="p"):
    return MatchResult(page_url=page, image_url="i", status="ok", similarity=sim,
                       accepted=accepted, social=social)


def test_best_returns_none_when_nothing_accepted():
    assert best([mk(0.1, False, True), mk(0.2, False, False)]) is None


def test_best_prefers_social_over_a_higher_scoring_non_social():
    chosen = best([mk(0.99, True, False, "nonsocial"), mk(0.80, True, True, "social")])
    assert chosen.page_url == "social"


def test_best_picks_the_highest_score_within_social():
    chosen = best([mk(0.80, True, True, "lower"), mk(0.95, True, True, "higher")])
    assert chosen.page_url == "higher"


def test_best_falls_back_to_non_social_when_no_social_accepted():
    chosen = best([mk(0.9, True, False, "only"), mk(0.99, False, True, "rejected")])
    assert chosen.page_url == "only"


def test_best_ignores_unscored_results():
    assert best([MatchResult("p", "i", "no_face", None, False, True)]) is None


def test_match_result_serialises():
    d = mk(0.5, True, True).as_dict()
    assert set(d) >= {"page_url", "similarity", "accepted", "status", "social"}


# ------------------------------------------------------- hotlink-blocked social CDNs

def test_falls_back_to_the_second_url_when_the_first_is_blocked(stub_fetch):
    """Social platforms block hotlinking of their own CDN, so for exactly the candidates
    this task cares about the primary URL is the one that will not fetch. Without a
    fallback, every social hit scored `download_failed` and nothing could be verified."""
    stub_fetch["https://tbn.gstatic/x.jpg"] = b"A"        # only the mirror is fetchable
    enc = FakeEncoder({b"A": 0.77})
    c = Candidate(page_url="https://instagram.com/p/1",
                  image_url="https://scontent.cdninstagram.com/blocked.jpg",
                  social=True,
                  image_fallback="https://tbn.gstatic/x.jpg")
    r = verify(enc, None, [c], threshold=0.363)
    assert r[0].status == "ok"
    assert r[0].accepted


def test_the_url_actually_fetched_is_the_one_recorded(stub_fetch):
    """The bundle should say where the compared bytes came from, not which URL was
    tried first."""
    stub_fetch["https://tbn.gstatic/x.jpg"] = b"A"
    enc = FakeEncoder({b"A": 0.77})
    c = Candidate(page_url="https://instagram.com/p/1",
                  image_url="https://blocked.example/x.jpg",
                  social=True,
                  image_fallback="https://tbn.gstatic/x.jpg")
    assert verify(enc, None, [c], threshold=0.363)[0].image_url == \
        "https://tbn.gstatic/x.jpg"


def test_both_urls_failing_is_still_a_download_failure(stub_fetch):
    c = Candidate(page_url="https://instagram.com/p/1",
                  image_url="https://blocked.example/a.jpg",
                  social=True,
                  image_fallback="https://also-blocked.example/b.jpg")
    r = verify(FakeEncoder({}), None, [c], threshold=0.363)
    assert r[0].status == "download_failed"
    assert r[0].note


def test_primary_is_preferred_when_both_work(stub_fetch):
    """The fallback is a smaller copy, so it must not be used when the original fetches."""
    stub_fetch["https://good.example/big.jpg"] = b"BIG"
    stub_fetch["https://tbn.gstatic/small.jpg"] = b"SMALL"
    enc = FakeEncoder({b"BIG": 0.9, b"SMALL": 0.4})
    c = Candidate(page_url="https://x.com/1", image_url="https://good.example/big.jpg",
                  social=True, image_fallback="https://tbn.gstatic/small.jpg")
    r = verify(enc, None, [c], threshold=0.363)
    assert r[0].image_url == "https://good.example/big.jpg"
    assert r[0].similarity == pytest.approx(0.9)


def test_candidate_without_a_fallback_still_works(stub_fetch):
    stub_fetch["https://i/a.jpg"] = b"A"
    r = verify(FakeEncoder({b"A": 0.8}), None, [cand()], threshold=0.363)
    assert r[0].status == "ok"

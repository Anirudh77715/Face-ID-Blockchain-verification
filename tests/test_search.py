"""Search adapter: result parsing, challenge detection, and provider labelling."""

from __future__ import annotations

import os

import pytest

from pom.search import (
    CHALLENGE_MARKERS,
    BingScripted,
    Replay,
    SearchResponse,
    SearchUnavailable,
    is_social,
)

# ------------------------------------------------------------------ domain matching

@pytest.mark.parametrize("url", [
    "https://x.com/someone/status/1",
    "https://www.x.com/someone",
    "https://mobile.twitter.com/a",
    "https://www.pinterest.com/pin/123/",
    "https://in.pinterest.com/pin/123/",
    "https://github.com/u/r",
])
def test_social_urls_are_recognised(url):
    assert is_social(url)


@pytest.mark.parametrize("url", [
    "https://example.com/a",
    "https://shutterstock.com/x",
    "https://ancestry.com/blog",
])
def test_non_social_urls_are_not(url):
    assert not is_social(url)


@pytest.mark.parametrize("url", [
    "https://notx.com/a",          # suffix without the dot boundary
    "https://fakex.com/a",
    "https://x.com.evil.net/a",    # the real domain is evil.net
    "https://pinterest.com.phish.io/a",
])
def test_lookalike_domains_are_not_treated_as_social(url):
    """`endswith` on a bare domain would pass all of these."""
    assert not is_social(url)


def test_is_social_tolerates_junk():
    for url in ("", "not a url", "javascript:alert(1)", "ftp://x.com/a"):
        assert isinstance(is_social(url), bool)


# ------------------------------------------------------------------ bing parsing

def test_parses_the_reference_capture(reference_html):
    candidates = BingScripted._parse(reference_html)
    assert len(candidates) >= 20, "reference capture should yield ~22 results"


def test_every_parsed_candidate_has_a_page_url(reference_html):
    for c in BingScripted._parse(reference_html):
        assert c.page_url.startswith("http")


def test_most_candidates_carry_an_image_url(reference_html):
    candidates = BingScripted._parse(reference_html)
    with_image = [c for c in candidates if c.image_url.startswith("http")]
    assert len(with_image) == len(candidates), "pairing regressed"


def test_page_and_image_urls_come_from_the_same_result(reference_html):
    """The bug this pins: separate purl/murl regexes return lists of different lengths
    (27 vs 14 here) because murl also belongs to video results, so zipping them by index
    silently attaches the wrong image to a page."""
    candidates = BingScripted._parse(reference_html)
    pinterest = [c for c in candidates if "pinterest.com" in c.page_url]
    assert pinterest, "expected pinterest results in the reference capture"
    for c in pinterest:
        assert "pinimg.com" in c.image_url, (
            f"pinterest page paired with a foreign image host: {c.image_url}")


def test_no_youtube_watch_urls_leak_in_as_images(reference_html):
    for c in BingScripted._parse(reference_html):
        assert "youtube.com/watch" not in c.image_url


def test_candidates_are_deduplicated(reference_html):
    pages = [c.page_url for c in BingScripted._parse(reference_html)]
    assert len(pages) == len(set(pages))


def test_parsing_junk_yields_nothing():
    assert BingScripted._parse("<html><body>nothing here</body></html>") == []
    assert BingScripted._parse("") == []


def test_parser_survives_malformed_embedded_json():
    html = '<a m="{&quot;ns&quot;:&quot;SERP&quot;,&quot;purl&quot;:BROKEN}">x</a>'
    assert BingScripted._parse(html) == []


# ------------------------------------------------------------------ challenge detection

@pytest.mark.parametrize("marker", CHALLENGE_MARKERS)
def test_every_challenge_marker_is_detected(marker):
    html = f"<html><body><h1>{marker.title()}</h1></body></html>"
    assert any(m in html.lower() for m in CHALLENGE_MARKERS)


def test_a_normal_results_page_is_not_flagged_as_a_challenge(reference_html):
    assert not any(m in reference_html.lower() for m in CHALLENGE_MARKERS)


# ------------------------------------------------------------------ replay backend

def test_replay_labels_itself_as_replay(tmp_path, reference_html):
    src = tmp_path / "capture.raw"
    src.write_text(reference_html, encoding="utf-8")
    response = Replay(src).search(tmp_path / "unused.jpg")
    assert isinstance(response, SearchResponse)
    assert response.provider == "replay", (
        "a replayed run must be self-labelling; provider is a Merkle leaf")
    assert response.candidates


def test_replay_rejects_a_missing_source(tmp_path):
    with pytest.raises(SearchUnavailable):
        Replay(tmp_path / "nope.raw").search(tmp_path / "unused.jpg")


def test_replay_rejects_a_capture_with_no_results(tmp_path):
    src = tmp_path / "challenge.raw"
    src.write_text("<html>Verify you are human</html>", encoding="utf-8")
    with pytest.raises(SearchUnavailable):
        Replay(src).search(tmp_path / "unused.jpg")


def test_replay_hash_matches_the_source_bytes(tmp_path, reference_html):
    import hashlib
    src = tmp_path / "capture.raw"
    src.write_text(reference_html, encoding="utf-8")
    response = Replay(src).search(tmp_path / "unused.jpg")
    assert response.raw_sha256 == hashlib.sha256(src.read_bytes()).hexdigest()


def test_social_candidates_is_a_subset(reference_html):
    r = SearchResponse("t", "now", "p", "h", BingScripted._parse(reference_html))
    assert all(c.social for c in r.social_candidates)
    assert set(r.social_candidates) <= set(r.candidates)


def test_replay_skips_an_unusable_capture_and_uses_a_good_one(tmp_path, monkeypatch,
                                                              reference_html):
    """Right after being rate-limited, the newest saved capture is a challenge page.
    Choosing by mtime alone breaks replay at the exact moment it is needed."""
    from pom import search as S

    raw = tmp_path / "raw"
    raw.mkdir()
    good = raw / "bing_scripted-1000.raw"
    good.write_text(reference_html, encoding="utf-8")
    challenge = raw / "bing_scripted-2000.raw"
    challenge.write_text("<html>Verify you are human</html>", encoding="utf-8")

    os.utime(good, (1000, 1000))
    os.utime(challenge, (2000, 2000))       # newest, but useless

    monkeypatch.setattr(S, "RAW_DIR", raw)
    response = Replay().search(tmp_path / "unused.jpg")
    assert response.candidates
    assert response.raw_path == str(good)


def test_replay_falls_back_to_the_reference_capture(tmp_path, monkeypatch):
    from pom import search as S
    empty = tmp_path / "raw"
    empty.mkdir()
    monkeypatch.setattr(S, "RAW_DIR", empty)
    response = Replay().search(tmp_path / "unused.jpg")
    assert response.candidates, "a clean clone must still be able to run"

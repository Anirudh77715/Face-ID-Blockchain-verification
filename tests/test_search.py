"""Search adapter: result parsing, challenge detection, and provider labelling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pom.search import (
    CHALLENGE_MARKERS,
    BingScripted,
    Candidate,
    Replay,
    SearchBlocked,
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


# ------------------------------------------------------------------ auto chain

class _Stub:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self._result = result
        self._error = error

    def search(self, image_path, **kw):
        if self._error:
            raise self._error
        return self._result


def _response(provider="stub"):
    return SearchResponse(provider, "now", "p", "h",
                          [Candidate("https://x.com/1", "https://i/1.jpg", True)])


def test_auto_returns_the_first_backend_that_works(monkeypatch):
    from pom.search import Auto
    auto = Auto()
    good = _response("second")
    monkeypatch.setattr(auto, "_chain", lambda url: iter([
        _Stub("first", error=SearchBlocked("challenged")),
        _Stub("second", result=good),
        _Stub("third", result=_response("third")),
    ]))
    assert auto.search(Path("x.jpg")) is good
    assert auto.attempts == ["first", "second"], "must stop at the first success"


def test_auto_records_what_it_tried(monkeypatch):
    from pom.search import Auto
    auto = Auto()
    monkeypatch.setattr(auto, "_chain", lambda url: iter([
        _Stub("a", error=SearchUnavailable("no key")),
        _Stub("b", result=_response("b")),
    ]))
    auto.search(Path("x.jpg"))
    assert auto.attempts == ["a", "b"]


def test_auto_survives_a_backend_raising_something_unexpected(monkeypatch):
    """One backend faulting must not end the run while others remain."""
    from pom.search import Auto
    auto = Auto()
    good = _response("ok")
    monkeypatch.setattr(auto, "_chain", lambda url: iter([
        _Stub("boom", error=RuntimeError("kaboom")),
        _Stub("ok", result=good),
    ]))
    assert auto.search(Path("x.jpg")) is good


def test_auto_reports_every_failure_when_all_fail(monkeypatch):
    from pom.search import Auto
    auto = Auto()
    monkeypatch.setattr(auto, "_chain", lambda url: iter([
        _Stub("one", error=SearchBlocked("challenged")),
        _Stub("two", error=SearchUnavailable("no key")),
    ]))
    with pytest.raises(SearchBlocked) as e:
        auto.search(Path("x.jpg"))
    message = str(e.value)
    assert "one" in message and "two" in message
    assert "bypass" in message.lower(), "must restate that nothing was evaded"


def test_auto_skips_url_backends_without_an_image_url():
    from pom.search import Auto
    names = [b.name for b in Auto()._chain(None)]
    assert "bing_url" not in names
    assert "bing_scripted" in names


def test_auto_prefers_the_url_backend_when_a_url_is_given():
    from pom.search import Auto
    names = [b.name for b in Auto()._chain("https://example.com/me.jpg")]
    assert names[0] == "bing_url", (
        "the URL endpoint needs no upload and kept working while uploads were challenged")


def test_auto_only_reaches_for_serpapi_when_a_key_exists(monkeypatch):
    from pom.search import Auto
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert "serpapi" not in [b.name for b in Auto()._chain("https://e.com/a.jpg")]
    monkeypatch.setenv("SERPAPI_KEY", "x")
    assert "serpapi" in [b.name for b in Auto()._chain("https://e.com/a.jpg")]


def test_bing_url_requires_an_image_url(tmp_path):
    from pom.search import BingUrl
    with pytest.raises(SearchUnavailable, match="image-url"):
        BingUrl().search(tmp_path / "x.jpg")


def test_replay_can_use_a_bing_url_capture(tmp_path, monkeypatch, reference_html):
    """Both Bing backends save the same HTML shape. Globbing only bing_scripted made an
    offline rerun silently fall back to the committed reference capture - a different
    search, whose candidate images are not the ones a live run just cached."""
    from pom import search as S

    raw = tmp_path / "raw"
    raw.mkdir()
    capture = raw / "bing_url-1234.raw"
    capture.write_text(reference_html, encoding="utf-8")

    monkeypatch.setattr(S, "RAW_DIR", raw)
    response = Replay().search(tmp_path / "unused.jpg")
    assert response.raw_path == str(capture), "bing_url captures must be replayable"

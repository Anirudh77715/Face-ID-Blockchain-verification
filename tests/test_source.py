"""Resolving the input image from a file or a URL."""

from __future__ import annotations

import pytest

from pom.source import SourceError, from_file, is_url, resolve


@pytest.mark.parametrize("spec", [
    "https://example.com/a.jpg",
    "http://example.com/a.jpg",
    "https://cdn.example.com/path/to/photo.png?x=1",
])
def test_urls_are_recognised(spec):
    assert is_url(spec)


@pytest.mark.parametrize("spec", [
    "me.jpg", "A:/photos/me.jpg", "/home/me/a.png", "C:\\Users\\me\\a.jpg",
    "ftp://example.com/a.jpg",       # a scheme, but not one to fetch over
    "https://",                      # no host
    "", "not a url",
])
def test_non_urls_are_not(spec):
    assert not is_url(spec)


def test_a_windows_path_is_never_mistaken_for_a_url():
    """The original bug: argparse turned a URL into a Path and mangled it into
    `https:\\example.com\\a.jpg`. The inverse must not happen either."""
    assert not is_url("C:\\Users\\me\\https\\a.jpg")


def test_reads_a_local_file(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff not really a jpeg")
    got = resolve(path)
    assert got.data == path.read_bytes()
    assert got.origin == "file"
    assert got.name == "photo.jpg"


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(SourceError, match="no such image"):
        resolve(tmp_path / "absent.jpg")


def test_a_directory_is_not_an_image(tmp_path):
    with pytest.raises(SourceError, match="not a file"):
        from_file(tmp_path)


def test_an_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")
    with pytest.raises(SourceError, match="empty"):
        resolve(path)


# ------------------------------------------------------------------------ url

class FakeResponse:
    def __init__(self, chunks, ctype="image/jpeg", status=200):
        self._chunks = chunks
        self.headers = {"Content-Type": ctype}
        self.status = status

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def iter_content(self, _n):
        yield from self._chunks


@pytest.fixture
def fake_get(monkeypatch):
    import requests

    holder = {}

    def fake(url, **kw):
        holder["url"] = url
        return holder["response"]

    monkeypatch.setattr(requests, "get", fake)
    return holder


def test_downloads_an_image(fake_get):
    fake_get["response"] = FakeResponse([b"abc", b"def"])
    got = resolve("https://example.com/photo.jpg")
    assert got.data == b"abcdef"
    assert got.origin == "url"
    assert got.name == "photo.jpg"
    assert got.location == "https://example.com/photo.jpg"


def test_an_html_page_is_refused_with_advice(fake_get):
    """The commonest mistake: pasting the page a photo sits on rather than the image."""
    fake_get["response"] = FakeResponse([b"<html>"], ctype="text/html; charset=utf-8")
    with pytest.raises(SourceError) as e:
        resolve("https://example.com/page")
    assert "not an image" in str(e.value)
    assert "Copy image address" in str(e.value)


def test_an_oversized_download_is_stopped(fake_get, monkeypatch):
    from pom import source
    monkeypatch.setattr(source, "MAX_BYTES", 10)
    fake_get["response"] = FakeResponse([b"x" * 6, b"x" * 6])
    with pytest.raises(SourceError, match="exceeds"):
        resolve("https://example.com/big.jpg")


def test_an_empty_response_is_refused(fake_get):
    fake_get["response"] = FakeResponse([])
    with pytest.raises(SourceError, match="no data"):
        resolve("https://example.com/nothing.jpg")


def test_an_http_error_is_reported(fake_get):
    fake_get["response"] = FakeResponse([b"x"], status=404)
    with pytest.raises(SourceError, match="could not fetch"):
        resolve("https://example.com/missing.jpg")


def test_a_url_without_a_filename_still_gets_a_name(fake_get):
    fake_get["response"] = FakeResponse([b"x"])
    assert resolve("https://example.com/").name == "downloaded"


def test_a_missing_content_type_is_allowed(fake_get):
    """Some hosts send no Content-Type. Refusing those would reject valid images; the
    decoder rejects genuinely bad bytes a moment later anyway."""
    fake_get["response"] = FakeResponse([b"x"], ctype="")
    assert resolve("https://example.com/a.jpg").data == b"x"

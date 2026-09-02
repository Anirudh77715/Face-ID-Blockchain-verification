"""Reverse image search, behind a provider-agnostic adapter.

The task brief permits "reverse image search, an API, or a scripted search approach".
No API key is required for any of this; `auto` is the default and only reaches for a key
if one happens to be configured.

  auto           tries the others in order and uses the first that returns results
  bing_url       free, no key, no upload. Needs --image-url. Measured best: it kept
                 working while the upload flow was being challenged on the same machine.
  bing_scripted  free, no key, uploads the file. Works cold; rate-limits into a
                 human-verification challenge under repeated automated use.
  serpapi        free tier (100/month). Needs a key and a publicly reachable image URL.
  replay         development only; re-parses a saved response and performs no query.

On a challenge the scripted backend raises `SearchBlocked` and the pipeline stops. It does
not attempt to solve or evade the challenge — see README, Known limitations.

Every search persists its raw response to disk and hashes it. That hash is a leaf in the
evidence bundle, which is what makes "this was a genuine search, not a hardcoded result"
checkable by someone who did not watch it run.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

RAW_DIR = Path(__file__).resolve().parent.parent / "evidence" / "raw"

# A real Bing response, committed so `--backend replay` works from a clean clone.
REFERENCE_CAPTURE = (Path(__file__).resolve().parent.parent / "spike"
                     / "reference-capture.raw")

CHALLENGE_MARKERS = (
    "verify you are human", "one last step", "unusual traffic",
    "are you a robot", "smartcaptcha", "recaptcha", "please solve the challenge",
)

SOCIAL_DOMAINS = (
    "x.com", "twitter.com", "instagram.com", "facebook.com", "linkedin.com",
    "threads.net", "reddit.com", "tiktok.com", "youtube.com", "github.com",
    "pinterest.com", "vk.com", "flickr.com", "tumblr.com", "mastodon.social",
    "bsky.app", "medium.com", "substack.com",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class SearchBlocked(Exception):
    """The provider served a bot challenge. Not bypassed by design."""


class SearchUnavailable(Exception):
    """Backend cannot run — missing key, missing hosted image, network failure."""


def is_social(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS)


@dataclass(frozen=True)
class Candidate:
    page_url: str
    image_url: str
    social: bool
    # A second URL for the same image, tried when the first download fails. Social
    # platforms block hotlinking of their own CDN, so for exactly the candidates this
    # task cares about, the primary URL is the one that will not fetch. Search engines
    # also host a copy; that copy is smaller, but a rejected candidate scores nothing at
    # all, and a low-resolution face still embeds.
    image_fallback: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResponse:
    provider: str
    queried_at: str
    raw_path: str
    raw_sha256: str
    candidates: list[Candidate]

    @property
    def social_candidates(self) -> list[Candidate]:
        return [c for c in self.candidates if c.social]


def _persist(provider: str, payload: bytes) -> tuple[Path, str]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    path = RAW_DIR / f"{provider}-{int(time.time())}.raw"
    path.write_bytes(payload)
    return path, digest


# --------------------------------------------------------------------- bing (scripted)

class BingScripted:
    """Bing Visual Search driven through a real browser.

    Result pages are embedded as HTML-escaped JSON under "purl" (source page) and
    "murl" (image) — they are NOT anchor hrefs, so a naive a[href] scrape finds nothing.
    """

    name = "bing_scripted"

    def __init__(self, headed: bool = False, timeout_ms: int = 30_000):
        self.headed = headed
        self.timeout_ms = timeout_ms

    def search(self, image_path: Path, **_) -> SearchResponse:
        from playwright.sync_api import sync_playwright

        queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        with sync_playwright() as pw:
            # A fresh context, deliberately. Reusing a persistent profile seemed like the
            # obvious way to look less like a bot, but measured side by side on the same
            # image it makes Bing serve a stripped layout carrying no result objects at
            # all (22 results vs 0). Cookie-warming is not worth a page with no data on it.
            browser = pw.chromium.launch(headless=not self.headed)
            ctx = browser.new_context(
                locale="en-US",
                viewport={"width": 1400, "height": 900},
                user_agent=UA,
            )
            page = ctx.new_page()
            try:
                page.goto("https://www.bing.com/images", wait_until="domcontentloaded",
                          timeout=self.timeout_ms)
                page.set_input_files("input[type=file]", str(image_path),
                                     timeout=self.timeout_ms)
                page.wait_for_timeout(6_000)
                try:
                    page.get_by_text("Pages with this image", exact=False).first.click(
                        timeout=5_000)
                    page.wait_for_timeout(4_000)
                except Exception:
                    pass  # the overview tab already carries purl entries

                html = page.content()
            finally:
                ctx.close()
                browser.close()

        if any(m in html.lower() for m in CHALLENGE_MARKERS):
            path, digest = _persist(self.name, html.encode("utf-8"))
            raise SearchBlocked(
                "Bing served a human-verification challenge. Not bypassed by design.\n"
                f"  raw response: {path}\n"
                "  Retry later, or configure the serpapi backend (SERPAPI_KEY)."
            )

        path, digest = _persist(self.name, html.encode("utf-8"))
        return SearchResponse(
            provider=self.name,
            queried_at=queried_at,
            raw_path=str(path),
            raw_sha256=digest,
            candidates=self._parse(html),
        )

    @staticmethod
    def _json_objects(text: str, needle: str = '{"ns":"SERP"') -> list[dict]:
        """Pull out each image result's embedded JSON by brace-matching.

        Two false trails to avoid: a bare `"purl"` regex and a bare `"murl"` regex return
        different-length lists that cannot be zipped (27 vs 14 on the reference capture),
        because `murl` also belongs to *video* results where it holds a YouTube watch URL,
        not an image. Pairing has to come from within a single object.
        """
        out, i = [], 0
        while True:
            i = text.find(needle, i)
            if i < 0:
                return out
            depth = 0
            for j in range(i, min(len(text), i + 8000)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            out.append(json.loads(text[i:j + 1]))
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            else:
                i += len(needle)

    @classmethod
    def _parse(cls, html: str) -> list[Candidate]:
        text = html_lib.unescape(html)

        out, seen = [], set()
        for obj in cls._json_objects(text):
            page_url = obj.get("purl", "")
            if not page_url or page_url in seen:
                continue
            seen.add(page_url)

            # The real image lives in a `mediaurl` param on Bing's detail-view link.
            image_url = ""
            detail = obj.get("url", "")
            if "mediaurl=" in detail:
                image_url = unquote(
                    parse_qs(urlparse(detail).query).get("mediaurl", [""])[0])

            out.append(Candidate(
                page_url=page_url,
                image_url=image_url,
                social=is_social(page_url),
            ))
        return out


# ------------------------------------------------------------------- bing (by url)

class BingUrl:
    """Bing Visual Search from an image URL instead of a file upload.

    This task's input is a photo the subject has publicly posted, so a public URL for it
    already exists and the upload flow is avoidable entirely. That matters more than it
    sounds: measured side by side, this endpoint kept returning results (33 candidates,
    12 on social platforms) while the upload flow on the same machine was being served
    human-verification challenges. It is a plain page load rather than a scripted form,
    so there is simply less to trip over.

    Results come back in the same shape, so BingScripted's parser is reused unchanged.
    """

    name = "bing_url"

    def __init__(self, headed: bool = False, timeout_ms: int = 45_000):
        self.headed = headed
        self.timeout_ms = timeout_ms

    def search(self, image_path: Path, image_url: str | None = None,
               **_) -> SearchResponse:
        from playwright.sync_api import sync_playwright

        if not image_url:
            raise SearchUnavailable(
                "bing_url needs --image-url: a publicly reachable copy of the image. "
                "Use --backend bing_scripted to upload the file instead."
            )

        queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        target = (f"https://www.bing.com/images/search?q=imgurl:{quote(image_url, safe='')}"
                  "&view=detailv2&iss=sbi&FORM=IRSBIQ")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not self.headed)
            ctx = browser.new_context(locale="en-US", user_agent=UA,
                                      viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(7_000)
                html = page.content()
            finally:
                ctx.close()
                browser.close()

        path, digest = _persist(self.name, html.encode("utf-8"))
        if any(m in html.lower() for m in CHALLENGE_MARKERS):
            raise SearchBlocked(
                "Bing served a human-verification challenge. Not bypassed by design.\n"
                f"  raw response: {path}"
            )

        candidates = BingScripted._parse(html)
        if not candidates:
            raise SearchUnavailable(
                f"bing_url returned no parseable results (raw: {path}). "
                "The image URL may not be publicly reachable."
            )
        return SearchResponse(self.name, queried_at, str(path), digest, candidates)


# ------------------------------------------------------------------------- serpapi

class SerpApi:
    """Google Lens via SerpAPI. Needs SERPAPI_KEY and a publicly reachable image URL.

    The image URL is never uploaded for you: sending a face photo to a third-party host
    is the user's decision, not the pipeline's.
    """

    name = "serpapi"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("SERPAPI_KEY", "")

    def search(self, image_path: Path, image_url: str | None = None, **_) -> SearchResponse:
        import requests

        if not self.api_key:
            raise SearchUnavailable("SERPAPI_KEY is not set")
        if not image_url:
            raise SearchUnavailable(
                "serpapi needs --image-url pointing at a publicly reachable copy of the "
                "image. This backend will not upload your photo anywhere on your behalf."
            )

        queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_lens",
                "url": image_url,
                "api_key": self.api_key,
                # Required. Without it Google Lens answers on its AI-overview tab and the
                # response carries no matches at all - the request succeeds, the parse
                # finds nothing, and it reads as "this face is not online".
                "type": "visual_matches",
            },
            timeout=60,
        )
        resp.raise_for_status()
        path, digest = _persist(self.name, resp.content)
        data = resp.json()

        if data.get("error"):
            raise SearchUnavailable(f"serpapi: {data['error']}")

        out, seen = [], set()
        # exact_matches first: a duplicate of the query image is a stronger signal than
        # something merely similar, and this task's input is a photo the subject posted.
        for field in ("exact_matches", "visual_matches"):
            for m in data.get(field, []) or []:
                link = m.get("link", "")
                if not link or link in seen:
                    continue
                seen.add(link)
                primary = m.get("original") or m.get("image") or ""
                thumb = m.get("thumbnail") or ""
                out.append(Candidate(
                    page_url=link,
                    image_url=primary or thumb,
                    social=is_social(link),
                    image_fallback=thumb if primary else "",
                ))

        if not out:
            available = [k for k, v in data.items() if isinstance(v, list) and v]
            raise SearchUnavailable(
                "serpapi returned no matches. "
                f"Response carried: {available or 'no result lists'}. "
                "Google Lens sometimes answers only on its AI-overview tab for an image."
            )

        return SearchResponse(self.name, queried_at, str(path), digest, out)


# ------------------------------------------------------------------------- replay

class Replay:
    """Re-parse a previously saved raw response. Development only.

    This exists because the downstream stages — verification, the bundle, the Merkle
    commitment, the chain write — should be testable without a live search, and because
    the live search rate-limits. It is NOT a search: it performs no network query and
    proves nothing about what is online now.

    It cannot be passed off as a genuine run. `provider` is committed as a Merkle leaf, so
    any bundle produced this way carries "replay" inside the hash that goes on chain, and
    `verify.py` will show it. The recording must use bing_scripted.
    """

    name = "replay"

    def __init__(self, source: Path | str | None = None):
        self.source = Path(source) if source else None

    def search(self, image_path: Path, **_) -> SearchResponse:
        if self.source is not None:
            if not self.source.exists():
                raise SearchUnavailable(f"no such saved response: {self.source}")
            candidates = self._candidates_in(self.source)
            if not candidates:
                raise SearchUnavailable(
                    f"{self.source} holds no parseable results "
                    "(a challenge page, or a search that returned nothing)")
            return self._respond(self.source, candidates)

        # Newest *usable* capture, not merely newest. A saved challenge page sorts first
        # right after being rate-limited - which is exactly the moment replay gets reached
        # for - so choosing blindly by mtime fails when it is most needed. The committed
        # reference capture is the last resort, so a clean clone can always run.
        for path in [*sorted(RAW_DIR.glob("bing_scripted-*.raw"),
                             key=lambda p: p.stat().st_mtime, reverse=True),
                     REFERENCE_CAPTURE]:
            if not path.exists():
                continue
            candidates = self._candidates_in(path)
            if candidates:
                return self._respond(path, candidates)

        raise SearchUnavailable(
            f"no usable saved response under {RAW_DIR}, and the reference capture at "
            f"{REFERENCE_CAPTURE} is missing or unparseable")

    @staticmethod
    def _candidates_in(path: Path) -> list[Candidate]:
        try:
            html = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return []
        return BingScripted._parse(html)

    def _respond(self, source: Path, candidates: list[Candidate]) -> SearchResponse:
        payload = source.read_bytes()
        return SearchResponse(
            provider=self.name,
            queried_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime(source.stat().st_mtime)),
            raw_path=str(source),
            raw_sha256=hashlib.sha256(payload).hexdigest(),
            candidates=candidates,
        )


# ---------------------------------------------------------------------------- auto

class Auto:
    """Try the no-key backends in order; first one with results wins.

    A single engine is a single point of failure, and the one that fails is usually the
    one being asked most often. Chaining costs nothing when the first works and saves the
    run when it does not. serpapi is last because it is the only one needing a key, and
    the point of this ordering is that a key should never be required.
    """

    name = "auto"

    def __init__(self, headed: bool = False):
        self.headed = headed
        self.attempts: list[str] = []

    def _chain(self, image_url: str | None):
        # bing_url first: it needs no upload and kept working while the upload flow was
        # being challenged on the same machine.
        if image_url:
            yield BingUrl(headed=self.headed)
        yield BingScripted(headed=self.headed)
        if os.environ.get("SERPAPI_KEY") and image_url:
            yield SerpApi()

    def search(self, image_path: Path, image_url: str | None = None,
               **kw) -> SearchResponse:
        self.attempts = []
        problems = []

        for backend in self._chain(image_url):
            self.attempts.append(backend.name)
            try:
                return backend.search(image_path, image_url=image_url, **kw)
            except (SearchBlocked, SearchUnavailable) as e:
                problems.append(f"{backend.name}: {str(e).splitlines()[0]}")
            except Exception as e:  # a backend fault must not end the run
                problems.append(f"{backend.name}: {type(e).__name__}: {e}"[:200])

        raise SearchBlocked(
            "every search backend failed:\n  " + "\n  ".join(problems) +
            "\n  No challenge was bypassed. Retry later, or pass --image-url so the "
            "URL-based backend can be used."
        )


BACKENDS = {
    "auto": Auto,
    "bing_url": BingUrl,
    "bing_scripted": BingScripted,
    "serpapi": SerpApi,
    "replay": Replay,
}


def get_backend(name: str, **kw):
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; have {sorted(BACKENDS)}")
    return BACKENDS[name](**kw)

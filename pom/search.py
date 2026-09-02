"""Reverse image search, behind a provider-agnostic adapter.

The task brief permits "reverse image search, an API, or a scripted search approach".
Two backends ship:

  bing_scripted  free, no key, Playwright. Works on a cold run; rate-limits into a
                 human-verification challenge under repeated automated use.
  serpapi        free tier (100/month). Deterministic, but needs a key and a publicly
                 reachable image URL.

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
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

RAW_DIR = Path(__file__).resolve().parent.parent / "evidence" / "raw"

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".browser-profile"

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

    def __init__(self, headed: bool = True, timeout_ms: int = 30_000):
        self.headed = headed
        self.timeout_ms = timeout_ms

    def search(self, image_path: Path, **_) -> SearchResponse:
        from playwright.sync_api import sync_playwright

        queried_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            # A persistent profile keeps cookies between runs, which materially reduces
            # how often Bing challenges a request.
            ctx = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=not self.headed,
                locale="en-US",
                viewport={"width": 1400, "height": 900},
                user_agent=UA,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
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
            params={"engine": "google_lens", "url": image_url, "api_key": self.api_key},
            timeout=60,
        )
        resp.raise_for_status()
        path, digest = _persist(self.name, resp.content)
        data = resp.json()

        out = []
        for m in data.get("visual_matches", []):
            link = m.get("link", "")
            if link:
                out.append(Candidate(
                    page_url=link,
                    image_url=m.get("thumbnail", "") or m.get("image", ""),
                    social=is_social(link),
                ))
        return SearchResponse(self.name, queried_at, str(path), digest, out)


BACKENDS = {"bing_scripted": BingScripted, "serpapi": SerpApi}


def get_backend(name: str, **kw):
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; have {sorted(BACKENDS)}")
    return BACKENDS[name](**kw)

"""Day-1 spike: can a scripted reverse image search return real result URLs, no API key?

This answers one question and nothing else. It is throwaway; the decision it produces
(which engine works) is what carries into pom/search.py.

    py spike/search_spike.py --image spike/control.jpg --engine yandex

Engines are tried in the order given. Each writes its raw HTML to spike/raw/ so the
"genuine search, not hardcoded" claim is evidenced from the very first commit.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

RAW = Path(__file__).parent / "raw"

SOCIAL = (
    "x.com", "twitter.com", "instagram.com", "facebook.com", "linkedin.com",
    "threads.net", "reddit.com", "tiktok.com", "youtube.com", "github.com",
    "pinterest.com", "vk.com", "flickr.com", "tumblr.com", "mastodon.social",
)


def is_social(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in SOCIAL)


def clean(url: str) -> str:
    """Unwrap the redirector links search engines wrap results in."""
    if not url:
        return ""
    q = parse_qs(urlparse(url).query)
    for key in ("url", "u", "q", "imgurl", "target"):
        if key in q and q[key][0].startswith("http"):
            return unquote(q[key][0])
    return url


# --------------------------------------------------------------------------- engines

def yandex(page, image: Path) -> None:
    page.goto("https://yandex.com/images/", wait_until="domcontentloaded")
    page.set_input_files("input[type=file]", str(image), timeout=20_000)
    page.wait_for_url(re.compile(r"rpt=imageview"), timeout=45_000)
    page.wait_for_timeout(4_000)
    # "Sites containing this image" lives behind a tab on some layouts
    for label in ("Similar", "Sites containing", "duplicates"):
        try:
            page.get_by_text(label, exact=False).first.click(timeout=2_500)
            page.wait_for_timeout(2_500)
            break
        except Exception:
            continue


def google_lens(page, image: Path) -> None:
    page.goto("https://lens.google.com/", wait_until="domcontentloaded")
    page.set_input_files("input[type=file]", str(image), timeout=20_000)
    page.wait_for_timeout(8_000)


def bing(page, image: Path) -> None:
    page.goto("https://www.bing.com/images", wait_until="domcontentloaded")
    page.set_input_files("input[type=file]", str(image), timeout=20_000)
    page.wait_for_timeout(6_000)
    # The Overview tab shows thumbnails; the source pages live behind this tab.
    try:
        page.get_by_text("Pages with this image", exact=False).first.click(timeout=5_000)
        page.wait_for_timeout(5_000)
    except Exception:
        pass  # overview already carries purl entries


def tineye(page, image: Path) -> None:
    """Exact / near-duplicate matching. Narrower than face search, but for this task
    the input IS a photo the subject posted, so a duplicate hit is a legitimate match."""
    page.goto("https://tineye.com/", wait_until="domcontentloaded")
    try:
        page.set_input_files("input[type=file]", str(image), timeout=6_000)
    except Exception:
        # No plain input; the upload button opens a native file chooser.
        with page.expect_file_chooser(timeout=15_000) as fc:
            page.get_by_text(re.compile("upload|choose|browse", re.I)).first.click()
        fc.value.set_files(str(image))
    page.wait_for_timeout(10_000)


ENGINES = {"yandex": yandex, "lens": google_lens, "bing": bing, "tineye": tineye}


# ----------------------------------------------------------------------------- probe

def probe(engine: str, image: Path, headed: bool) -> dict:
    result = {"engine": engine, "ok": False, "error": None,
              "urls": 0, "social": [], "raw": None, "elapsed_s": 0.0}
    t0 = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        try:
            ENGINES[engine](page, image)

            html = page.content()
            RAW.mkdir(parents=True, exist_ok=True)
            raw = RAW / f"{engine}-{int(t0)}.html"
            raw.write_text(html, encoding="utf-8")
            result["raw"] = str(raw)
            result["raw_sha256"] = hashlib.sha256(html.encode()).hexdigest()[:16]

            # Anchors carry navigation chrome; the actual result pages are embedded as
            # HTML-escaped JSON ("purl" = source page, "murl" = image). Both are needed.
            hrefs = {clean(h) for h in page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)") if h}
            unescaped = html_lib.unescape(html)
            hrefs |= set(re.findall(r'"purl":"(https?://[^"]+)"', unescaped))
            hrefs = {h for h in hrefs if h.startswith("http")}
            result["urls"] = len(hrefs)
            result["pages"] = sorted(set(re.findall(
                r'"purl":"(https?://[^"]+)"', unescaped)))[:40]
            result["social"] = sorted({h for h in hrefs if is_social(h)})[:25]

            blocked = any(w in html.lower() for w in
                          ("captcha", "unusual traffic", "are you a robot",
                           "confirm that you", "smartcaptcha"))
            result["blocked"] = blocked
            result["ok"] = bool(result["social"]) and not blocked

            page.screenshot(path=str(RAW / f"{engine}-{int(t0)}.png"), full_page=False)
        except PWTimeout as e:
            result["error"] = f"timeout: {str(e)[:200]}"
        except Exception as e:  # spike: any failure is just data
            result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            result["elapsed_s"] = round(time.time() - t0, 1)
            browser.close()

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--engine", default="yandex,lens,bing",
                    help="comma-separated, tried in order")
    ap.add_argument("--headed", action="store_true",
                    help="visible browser — also how the final recording should run")
    args = ap.parse_args()

    if not args.image.exists():
        print(f"no such image: {args.image}", file=sys.stderr)
        return 2

    verdict = []
    for engine in [e.strip() for e in args.engine.split(",") if e.strip()]:
        print(f"\n=== {engine} " + "=" * 50)
        r = probe(engine, args.image, args.headed)
        verdict.append(r)
        print(json.dumps({k: v for k, v in r.items()
                          if k not in ("social", "pages")}, indent=2))
        print(f"  source pages: {len(r.get('pages', []))}")
        for u in r.get("pages", [])[:8]:
            print(f"    {u[:120]}")
        if r["social"]:
            print(f"  social hits ({len(r['social'])}):")
            for u in r["social"][:10]:
                print(f"    {u[:120]}")
        if r["ok"]:
            print(f"\n  >>> {engine} WORKS — this is the backend for pom/search.py")
            break

    (Path(__file__).parent / "verdict.json").write_text(
        json.dumps(verdict, indent=2), encoding="utf-8")
    print("\nwrote spike/verdict.json")
    return 0 if any(v["ok"] for v in verdict) else 1


if __name__ == "__main__":
    raise SystemExit(main())

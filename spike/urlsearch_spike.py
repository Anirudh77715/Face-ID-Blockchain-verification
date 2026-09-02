"""Spike: can reverse image search run from a URL, with no key and no file upload?

The task's input is a photo the subject has publicly posted, so a public URL for it
already exists. Several engines accept that URL directly, which skips the upload flow
entirely - fewer moving parts, and a plain page load rather than a scripted form.

    py spike/urlsearch_spike.py --image-url https://...

Answers one question: which engines return real source pages this way.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pom.search import UA, BingScripted, is_social  # noqa: E402

RAW = Path(__file__).parent / "raw"

CHALLENGE = ("verify you are human", "one last step", "unusual traffic",
             "are you a robot", "smartcaptcha", "recaptcha")

ENDPOINTS = {
    # Bing's "search by image URL" entry point.
    "bing_url": lambda u: (
        f"https://www.bing.com/images/search?q=imgurl:{quote(u, safe='')}"
        f"&view=detailv2&iss=sbi&FORM=IRSBIQ"),
    # Google Lens accepts a URL directly.
    "lens_url": lambda u: f"https://lens.google.com/uploadbyurl?url={quote(u, safe='')}",
    # Yandex's imageview entry point.
    "yandex_url": lambda u: (
        f"https://yandex.com/images/search?rpt=imageview&url={quote(u, safe='')}"),
}


def extract(html: str) -> tuple[list[str], list[str]]:
    """Return (page urls, social page urls) using each engine's own shape."""
    text = html_lib.unescape(html)
    pages = {c.page_url for c in BingScripted._parse(html)}

    # Generic: anchors to third-party hosts, minus the engine's own chrome.
    for m in re.finditer(r'href="(https?://[^"]+)"', text):
        u = m.group(1)
        host = (urlparse(u).hostname or "").lower()
        if not any(e in host for e in ("bing.com", "google.com", "yandex.",
                                       "microsoft.com", "gstatic.com", "ytimg.com")):
            pages.add(u)

    # Yandex embeds results as JSON.
    pages |= set(re.findall(r'"url":"(https?://[^"]+)"', text))
    return sorted(pages), sorted({u for u in pages if is_social(u)})


def probe(name: str, image_url: str, headed: bool) -> dict:
    from playwright.sync_api import sync_playwright

    out = {"engine": name, "ok": False, "blocked": False, "error": None,
           "pages": 0, "social": [], "elapsed_s": 0.0}
    t0 = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        ctx = browser.new_context(locale="en-US", user_agent=UA,
                                  viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(ENDPOINTS[name](image_url), wait_until="domcontentloaded",
                      timeout=45_000)
            page.wait_for_timeout(7_000)
            html = page.content()

            RAW.mkdir(parents=True, exist_ok=True)
            (RAW / f"{name}-{int(t0)}.html").write_text(html, encoding="utf-8")

            out["blocked"] = any(c in html.lower() for c in CHALLENGE)
            pages, social = extract(html)
            out["pages"], out["social"] = len(pages), social[:15]
            out["ok"] = bool(social) and not out["blocked"]
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        finally:
            out["elapsed_s"] = round(time.time() - t0, 1)
            ctx.close()
            browser.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-url", required=True)
    ap.add_argument("--engines", default=",".join(ENDPOINTS))
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    results = []
    for name in [e.strip() for e in args.engines.split(",") if e.strip()]:
        print(f"\n=== {name} " + "=" * 46)
        r = probe(name, args.image_url, args.headed)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "social"}, indent=2))
        for u in r["social"][:8]:
            print(f"    SOCIAL {u[:100]}")

    print("\n" + "=" * 60)
    for r in results:
        state = ("WORKS" if r["ok"] else
                 "blocked" if r["blocked"] else
                 "error" if r["error"] else "no social hits")
        print(f"  {r['engine']:12} {state:15} pages={r['pages']:4} "
              f"social={len(r['social'])}")
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

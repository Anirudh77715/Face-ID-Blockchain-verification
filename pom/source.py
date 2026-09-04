"""Resolving the input image, from a file or from a URL.

The pipeline should not care where a photo came from. Requiring a local file made the
common case awkward: the input for this task is a photo the subject already posted, so it
already lives at a URL, and forcing a manual download before scanning was a step with no
purpose.

Either form works, and one URL can serve both jobs - the face to scan, and the public URL
the URL-based search backends need:

    py run.py --image me.jpg --image-url https://...   # local file, hosted copy
    py run.py --image https://...                      # one URL, used for both
    py run.py --image-url https://...                  # same thing, shorter

What is committed is always the SHA-256 of the bytes actually scanned, so a run from a URL
and a run from a downloaded copy of the same file produce the same identity - and a URL
whose content changes later cannot quietly rewrite what was attested.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

MAX_BYTES = 24 * 1024 * 1024
TIMEOUT = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class SourceError(Exception):
    pass


def is_url(spec: str) -> bool:
    parsed = urlparse(str(spec))
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@dataclass(frozen=True)
class ImageSource:
    data: bytes
    name: str
    origin: str          # "file" or "url"
    location: str        # the path or URL it came from


def from_file(path: Path) -> ImageSource:
    path = Path(path)
    if not path.exists():
        raise SourceError(f"no such image: {path}")
    if not path.is_file():
        raise SourceError(f"not a file: {path}")
    data = path.read_bytes()
    if not data:
        raise SourceError(f"image is empty: {path}")
    return ImageSource(data, path.name, "file", str(path))


def from_url(url: str) -> ImageSource:
    import requests

    try:
        with requests.get(url, timeout=TIMEOUT, stream=True,
                          headers={"User-Agent": UA}) as r:
            r.raise_for_status()

            ctype = r.headers.get("Content-Type", "")
            if ctype and not ctype.split(";")[0].strip().startswith("image/"):
                # A URL that answers with a login page or an HTML wrapper is the common
                # failure here, and it decodes to nothing useful much further downstream.
                raise SourceError(
                    f"that URL returned {ctype.split(';')[0].strip()!r}, not an image.\n"
                    "  Use a direct image link - the one you get from 'Copy image "
                    "address', not the page it sits on.")

            buf = bytearray()
            for chunk in r.iter_content(64 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise SourceError(f"image exceeds {MAX_BYTES // (1024*1024)} MB")
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"could not fetch {url}\n  {type(e).__name__}: {e}") from e

    if not buf:
        raise SourceError(f"{url} returned no data")

    name = Path(urlparse(url).path).name or "downloaded"
    return ImageSource(bytes(buf), name, "url", url)


def resolve(spec: str | Path) -> ImageSource:
    """Load an image from whichever kind of location this is."""
    text = str(spec)
    return from_url(text) if is_url(text) else from_file(Path(text))

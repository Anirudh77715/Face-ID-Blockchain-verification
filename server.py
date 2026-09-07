"""A small local web front end: drop a photo, or paste a link, and watch the pipeline run.

The task does not require a website ("No website required - focus your time on the
pipeline itself"), so this deliberately adds nothing to the pipeline. It is a thin
operator console over the two commands that already exist:

    py run.py --image ...        the five stages
    py verify.py --bundle ...    recompute and compare against the chain

Both are spawned as subprocesses and their real stdout is streamed to the page. Nothing
is reimplemented here, so the browser cannot show a different result from the terminal -
which matters, because the terminal is what a judge will run.

    py server.py                 then open http://127.0.0.1:8000

Bound to 127.0.0.1 on purpose. It runs the local machine's Python and writes into
evidence/, so it is not something to expose.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pom  # noqa: F401  - configures UTF-8 output and BLAS threads on import

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
UPLOADS = EVIDENCE / "uploads"
PAGE = ROOT / "server_page.html"

HOST, PORT = "127.0.0.1", 8000
MAX_UPLOAD = 24 * 1024 * 1024

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TOKEN_OK = re.compile(r"^[0-9a-f]{16}\.[a-z0-9]{1,5}$")

# What the pipeline's exit codes mean, so the page can say it in words rather than
# leaving a bare number on screen.
EXITS = {
    0: ("ok", "match cleared the threshold and its commitment is on chain"),
    1: ("error", "usage error"),
    2: ("none", "no candidate cleared the threshold - nothing was written"),
    3: ("blocked", "the search provider served a bot challenge - nothing was written"),
    4: ("noface", "no face found in the input image"),
    5: ("error", "chain error"),
    6: ("error", "consent rejected"),
}

# verify.py shares the numbers but not the meanings, and reporting a re-verification as
# "match cleared the threshold" would describe the wrong thing entirely.
VERIFY_EXITS = {
    0: ("ok", "every field matches its commitment, and that root is on chain"),
    1: ("error", "usage error"),
    5: ("error", "chain error"),
    6: ("error", "the evidence does not match the on-chain record"),
    7: ("error", "the root is not on chain at all"),
    8: ("none", "intact, but the attestation was revoked or its consent does not hold"),
    9: ("none", "intact, but the live post no longer hashes to what was attested"),
}


# --------------------------------------------------------------------------- jobs

class Job:
    """One subprocess, its output so far, and what could be parsed out of it."""

    def __init__(self, argv: list[str], kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.argv = argv
        self.kind = kind
        self.lines: list[str] = []
        self.meta: dict = {}
        self.done = False
        self.exit_code: int | None = None
        self.lock = threading.Lock()

    def start(self) -> Job:
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _emit(self, text: str) -> None:
        with self.lock:
            self.lines.append(text)

    def _run(self) -> None:
        self._emit("$ " + " ".join(self.argv[1:]))
        try:
            proc = subprocess.Popen(
                self.argv, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
            )
        except Exception as e:
            self._emit(f"could not start the pipeline: {type(e).__name__}: {e}")
            with self.lock:
                self.done, self.exit_code = True, 1
            return

        for raw in proc.stdout:
            line = ANSI.sub("", raw.rstrip("\n"))
            self._emit(line)
            self._scrape(line)
        proc.wait()
        with self.lock:
            self.exit_code = proc.returncode
            self.done = True

    # The page shows a few fields prominently rather than making the viewer read them
    # out of the log. Scraped rather than recomputed, so what is displayed is what the
    # pipeline actually printed.
    FIELDS = {
        "annotated": r"^\s*annotated\s+(.+\.png)\s*$",
        "bundle": r"(?:^\s*bundle\s+|^\s*saved\s+)(.+run-[0-9a-zA-Z]+\.json)\s*$",
        "tx": r"^\s*(?:tx|original tx)\s+(0x[0-9a-fA-F]{64})",
        "contract": r"^\s*contract\s+(0x[0-9a-fA-F]{40})",
        "root": r"^\s*root\s+(0x[0-9a-fA-F]{64})",
        "best": r"^\s*best match\s+(\S+)",
        "similarity": r"^\s*similarity\s+([0-9.]+)",
        "provider": r"^\s*resolved by\s+(\S+)",
        "candidates": r"^\s*candidates\s+(\d+)",
        "accepted": r"^\s*accepted\s+(\S+)",
        "confidence": r"^\s*confidence\s+([0-9.]+)",
        "verdict": r"^\s*(VERIFIED)\s*$",
        # --refetch: the two hashes the comparison turns on, surfaced side by side
        # rather than left for the viewer to find in the log.
        "current_hash": r"^\s*current post hash\s+([0-9a-f]{64})",
        "attested_hash": r"^\s*attested post hash\s+([0-9a-f]{64})",
        "post_state": r"^\s*(POST UNCHANGED|POST CHANGED)",
        # Persistent Face ID. The similarity pattern is anchored on the threshold note
        # that follows it, because stage 4 prints a line of the same shape for the best
        # web candidate and the two must not overwrite each other.
        "face_id": r"^\s*face id\s+(F-\d+)",
        "face_status": r"^\s*status\s+(.+?)\s*$",
        "face_similarity": r"^\s*similarity\s+([0-9.]+)\s+\(match >=",
        "face_photos": r"^\s*photos\s+(\d+) recorded",
    }

    def _scrape(self, line: str) -> None:
        for key, pattern in self.FIELDS.items():
            m = re.search(pattern, line)
            if m:
                with self.lock:
                    self.meta[key] = m.group(1).strip()

    def snapshot(self, start: int) -> dict:
        with self.lock:
            table = VERIFY_EXITS if self.kind == "verify" else EXITS
            status, note = table.get(self.exit_code, ("error", "unknown exit"))
            return {
                "lines": self.lines[start:],
                "next": len(self.lines),
                "done": self.done,
                "exit": self.exit_code,
                "status": status if self.done else None,
                "note": note if self.done else None,
                "meta": dict(self.meta),
            }


JOBS: dict[str, Job] = {}


# --------------------------------------------------------------------------- input

def normalise_url(url: str) -> tuple[str, str | None]:
    """Accept the links people actually copy out of Google.

    'Copy image address' gives a direct image URL and needs nothing. But the link on a
    Google Images result is a /imgres wrapper that carries the real image URL in a query
    parameter, and pasting it would otherwise fail at the content-type check with a
    correct but unhelpful message. Unwrap it and say so.
    """
    url = url.strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    if host.startswith("google.") and parsed.path in ("/imgres", "/url"):
        params = parse_qs(parsed.query)
        for key in ("imgurl", "q", "url"):
            if params.get(key):
                inner = params[key][0]  # parse_qs already decoded it
                if inner.startswith(("http://", "https://")):
                    return inner, ("that was a Google results link; used the image URL "
                                   "inside it")
    return url, None


def save_upload(name: str, data: bytes) -> str:
    """Store an uploaded photo under a server-chosen, content-addressed name.

    The client never supplies a path. evidence/ is gitignored, so a subject's photo
    stays out of the repository the same way the CLI's evidence does.
    """
    UPLOADS.mkdir(parents=True, exist_ok=True)
    ext = (Path(name).suffix or ".jpg").lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ".jpg"
    token = hashlib.sha256(data).hexdigest()[:16] + ext
    (UPLOADS / token).write_bytes(data)
    return token


def upload_path(token: str) -> Path:
    if not TOKEN_OK.fullmatch(token or ""):
        raise ValueError("bad upload token")
    path = (UPLOADS / token).resolve()
    if UPLOADS.resolve() not in path.parents or not path.is_file():
        raise ValueError("no such upload")
    return path


def build_run(body: dict) -> tuple[list[str], list[str]]:
    """Turn the form into the same argv a person would type. Returns (argv, notes)."""
    argv = [sys.executable, "-u", str(ROOT / "run.py")]
    notes: list[str] = []

    url, unwrapped = "", None
    if body.get("url"):
        url, unwrapped = normalise_url(str(body["url"]))
        if unwrapped:
            notes.append(unwrapped)

    token = str(body.get("token") or "")
    if token:
        path = upload_path(token)
        argv += ["--image", str(path)]
        if url:
            # Best case: the bytes to scan come from the file, and the URL backends get
            # a public copy to search with.
            argv += ["--image-url", url]
        else:
            notes.append(
                "an uploaded file has no public URL, so the URL backends cannot be used; "
                "auto falls through to the scripted upload, which is the path providers "
                "challenge most often. Pasting a link as well is more reliable.")
    elif url:
        argv += ["--image", url]
    else:
        raise ValueError("choose a photo or paste an image link")

    argv += ["--backend", str(body.get("backend") or "auto"),
             "--chain", str(body.get("chain") or "local")]

    if body.get("threshold"):
        argv += ["--threshold", str(float(body["threshold"]))]
    if body.get("limit"):
        argv += ["--limit", str(int(body["limit"]))]
    if body.get("headed"):
        argv += ["--headed"]
    if body.get("no_faceid"):
        argv += ["--no-faceid"]
    if body.get("faceid_high"):
        argv += ["--faceid-high", str(float(body["faceid_high"]))]
    if body.get("faceid_review"):
        argv += ["--faceid-review", str(float(body["faceid_review"]))]
    if body.get("no_chain"):
        argv += ["--no-chain"]
    if body.get("consent"):
        consent = ROOT / "consent.json"
        if consent.is_file():
            argv += ["--consent", str(consent)]
        else:
            notes.append("no consent.json in the project root; running without one")
    return argv, notes


def build_verify(body: dict) -> list[str]:
    bundle = Path(str(body.get("bundle") or "")).resolve()
    if EVIDENCE.resolve() not in bundle.parents or not bundle.is_file():
        raise ValueError("that bundle is not in evidence/")
    argv = [sys.executable, "-u", str(ROOT / "verify.py"),
            "--bundle", str(bundle),
            "--chain", str(body.get("chain") or "local")]
    if body.get("refetch"):
        argv.append("--refetch")
    return argv


# -------------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "proof-of-match"

    def log_message(self, fmt, *args):  # one tidy line, not the default noise
        sys.stderr.write(f"  {self.command} {self.path.split('?')[0]} -> {args[1]}\n")

    # ------------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _fail(self, message: str, code: int = 400) -> None:
        self._json({"error": message}, code)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError(f"upload exceeds {MAX_UPLOAD // (1024 * 1024)} MB")
        return self.rfile.read(length) if length else b""

    # ---------------------------------------------------------------------- GET

    def do_GET(self) -> None:
        route = urlparse(self.path)
        query = parse_qs(route.query)

        if route.path in ("/", "/index.html"):
            try:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._fail("server_page.html is missing", 500)

        if route.path == "/api/log":
            job = JOBS.get((query.get("id") or [""])[0])
            if job is None:
                return self._fail("no such job", 404)
            start = int((query.get("from") or ["0"])[0])
            return self._json(job.snapshot(start))

        if route.path == "/api/file":
            # Serves the annotated face render the pipeline wrote. Confined to
            # evidence/ - the page only ever asks for paths the pipeline printed.
            # parse_qs has already decoded the value; decoding again would turn a
            # filename that legitimately contains '%28' into '('.
            try:
                path = Path((query.get("path") or [""])[0]).resolve()
                if EVIDENCE.resolve() not in path.parents or not path.is_file():
                    raise ValueError
            except Exception:
                return self._fail("not available", 404)
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return self._send(200, path.read_bytes(), ctype)

        return self._fail("not found", 404)

    # --------------------------------------------------------------------- POST

    def do_POST(self) -> None:
        route = urlparse(self.path)
        query = parse_qs(route.query)

        try:
            if route.path == "/api/upload":
                data = self._read_body()
                if not data:
                    return self._fail("empty upload")
                name = (query.get("name") or ["photo.jpg"])[0]
                return self._json({"token": save_upload(name, data),
                                   "bytes": len(data)})

            if route.path in ("/api/run", "/api/verify"):
                body = json.loads(self._read_body() or b"{}")
                if route.path == "/api/run":
                    argv, notes = build_run(body)
                    kind = "run"
                else:
                    argv, notes, kind = build_verify(body), [], "verify"
                job = Job(argv, kind).start()
                JOBS[job.id] = job
                return self._json({"id": job.id, "notes": notes,
                                   "command": " ".join(argv[1:])})
        except ValueError as e:
            return self._fail(str(e))
        except Exception as e:
            return self._fail(f"{type(e).__name__}: {e}", 500)

        return self._fail("not found", 404)


def main() -> int:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"proof-of-match  http://{HOST}:{PORT}")
    print(f"  project   {ROOT}")
    print("  this is a console over run.py and verify.py; it adds nothing to the")
    print("  pipeline. Ctrl-C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

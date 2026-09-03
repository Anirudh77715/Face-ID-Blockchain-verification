"""A local, read-only viewer for evidence bundles.

    py viewer.py            # http://127.0.0.1:8000

Optional. The task requires no website, and nothing here is part of the pipeline - this
exists because a table of candidates with their scores, and a root turning red the moment
a bundle is edited, are easier to follow on a screen recording than terminal output.

Two rules it holds to:

  read-only     it never writes a bundle, never sends a transaction, never runs a search
  no second     verification calls pom.evidence and pom.chain, the same code verify.py
  implementation  uses. A viewer that reimplemented the check could agree with itself
                while disagreeing with the tool that matters.

Stdlib only, bound to localhost.
"""

from __future__ import annotations

import argparse
import json
import traceback
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pom import evidence
from pom.chain import Chain, ChainError

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"

# The page lives in its own file: a few hundred lines of HTML, CSS and JS embedded in a
# Python string is unreadable and uneditable. Read once at import; restart to pick up edits.
PAGE_FILE = ROOT / "viewer_page.html"


def page() -> bytes:
    if not PAGE_FILE.exists():
        return (b"<h1>viewer_page.html is missing</h1>"
                b"<p>It sits next to viewer.py.</p>")
    return PAGE_FILE.read_bytes()


def bundles() -> list[Path]:
    if not EVIDENCE.exists():
        return []
    return sorted(EVIDENCE.glob("run-*.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def bundle_by_id(run_id: str) -> Path | None:
    for path in bundles():
        if json.loads(path.read_text(encoding="utf-8")).get("run_id") == run_id:
            return path
    return None


def chain_status() -> dict:
    try:
        chain = Chain("local")
        return {"connected": True, "chain_id": chain.w3.eth.chain_id,
                "block": chain.w3.eth.block_number}
    except Exception:
        return {"connected": False}


def chain_state_for(bundle: dict) -> dict:
    """Live consent and revocation status.

    A bundle records what was true when it was written. Whether the attestation has since
    been withdrawn is only knowable by asking the chain, which is exactly why revocation
    belongs there rather than in the file.
    """
    attestation = bundle.get("attestation", {})
    out = {"written": bool(attestation.get("written")), "exists": False,
           "live": None, "has_consent": None, "revoked": None, "error": None}
    if not out["written"]:
        return out

    try:
        chain = Chain(attestation.get("network", "local"))
        root = bytes.fromhex(bundle["merkle"]["root"][2:])
        address = attestation.get("contract")
        out["exists"] = chain.exists(root, address=address)
        if out["exists"]:
            record = chain.get(root, address=address)
            out.update(live=chain.is_live(root, address=address),
                       has_consent=record["has_consent"],
                       revoked=record["revoked"],
                       revoked_at=record["revoked_at"],
                       submitter=record["submitter"])
    except ChainError as e:
        out["error"] = str(e)
    return out


def verify_bundle(bundle: dict) -> dict:
    """Delegates to the same modules verify.py uses. No second implementation."""
    diverged = evidence.diff_against_stored(bundle)
    recomputed = "0x" + evidence.root(bundle).hex()
    committed = bundle.get("merkle", {}).get("root")
    root_ok = recomputed == committed

    attestation = bundle.get("attestation", {})
    on_chain, note = False, ""
    if not attestation.get("written"):
        note = "This bundle was never written to a chain."
    else:
        try:
            chain = Chain(attestation.get("network", "local"))
            on_chain = chain.exists(bytes.fromhex(recomputed[2:]),
                                    address=attestation.get("contract"))
            if not on_chain:
                note = "The recomputed root is not present on chain."
        except ChainError as e:
            note = f"Could not reach the chain: {e}"

    return {
        "verdict": "VERIFIED" if (not diverged and root_ok and on_chain) else "TAMPERED",
        "diverged": diverged,
        "recomputed": recomputed,
        "committed": committed,
        "root_ok": root_ok,
        "on_chain": on_chain,
        "leaf_count": bundle.get("merkle", {}).get("leaf_count", 0),
        "note": note,
    }


MAX_BODY = 4 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "proof-of-match-viewer"

    def log_message(self, *_args):
        pass  # the pipeline's own output is the interesting stream

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_POST(self):  # noqa: N802
        """Recompute a root from a bundle the browser edited.

        Nothing is written: no file, no transaction. This exists so the tamper
        demonstration can be *interactive* - edit a field, watch the root change and the
        chain lookup fail - rather than something the viewer only describes.
        """
        path = unquote(urlparse(self.path).path)
        if path != "/api/simulate":
            return self._json({"error": "not found"}, 404)

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY:
                return self._json({"error": "bad content length"}, 400)
            bundle = json.loads(self.rfile.read(length).decode("utf-8"))

            recomputed = "0x" + evidence.root(bundle).hex()
            committed = bundle.get("merkle", {}).get("root")
            diverged = evidence.diff_against_stored(bundle)

            on_chain = False
            attestation = bundle.get("attestation", {})
            if attestation.get("written"):
                try:
                    chain = Chain(attestation.get("network", "local"))
                    on_chain = chain.exists(bytes.fromhex(recomputed[2:]),
                                            address=attestation.get("contract"))
                except ChainError:
                    pass

            self._json({
                "recomputed": recomputed,
                "committed": committed,
                "root_ok": recomputed == committed,
                "diverged": diverged,
                "on_chain": on_chain,
            })
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                self._send(200, page(), "text/html; charset=utf-8")

            elif path == "/api/runs":
                runs = []
                for p in bundles():
                    b = json.loads(p.read_text(encoding="utf-8"))
                    runs.append({
                        "id": b.get("run_id", p.stem),
                        "created_at": b.get("created_at", ""),
                        "candidates": len(b.get("candidates", [])),
                        "matched": b.get("decision", {}).get("matched", False),
                    })
                self._json({"runs": runs, "chain": chain_status()})

            elif path.startswith("/api/run/"):
                found = bundle_by_id(path.rsplit("/", 1)[-1])
                if not found:
                    return self._json({"error": "no such run"}, 404)
                self._json(json.loads(found.read_text(encoding="utf-8")))

            elif path.startswith("/api/chainstate/"):
                found = bundle_by_id(path.rsplit("/", 1)[-1])
                if not found:
                    return self._json({"error": "no such run"}, 404)
                self._json(chain_state_for(json.loads(
                    found.read_text(encoding="utf-8"))))

            elif path.startswith("/api/verify/"):
                found = bundle_by_id(path.rsplit("/", 1)[-1])
                if not found:
                    return self._json({"error": "no such run"}, 404)
                self._json(verify_bundle(json.loads(found.read_text(encoding="utf-8"))))

            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    # Localhost only. This serves evidence bundles, which name the pages a face was
    # matched to; that is not something to expose on a network by default.
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), partial(Handler))
    print(f"viewer   http://{args.host}:{args.port}")
    print(f"bundles  {len(bundles())} in {EVIDENCE}")
    print("read-only: it never writes a bundle, sends a transaction, or runs a search")
    print("ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>proof-of-match</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --line:#262b36; --text:#e6e9ef; --dim:#8b93a7;
    --ok:#3fb950; --bad:#f85149; --warn:#d29922; --accent:#58a6ff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font:15px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}
  header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;
         align-items:baseline;gap:14px;flex-wrap:wrap}
  h1{font-size:17px;margin:0;letter-spacing:.02em}
  .sub{color:var(--dim);font-size:13px}
  main{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 61px)}
  aside{border-right:1px solid var(--line);padding:14px;overflow:auto}
  section{padding:20px 24px;overflow:auto}
  .run{padding:9px 11px;border:1px solid var(--line);border-radius:7px;
       margin-bottom:8px;cursor:pointer;background:var(--panel)}
  .run:hover{border-color:var(--accent)}
  .run.on{border-color:var(--accent);background:#1b2230}
  .run b{display:block;font-size:13px}
  .run span{color:var(--dim);font-size:11.5px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
        padding:15px 17px;margin-bottom:15px}
  .card h2{margin:0 0 11px;font-size:12px;letter-spacing:.09em;text-transform:uppercase;
           color:var(--dim);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);
        vertical-align:top}
  th{color:var(--dim);font-weight:600;font-size:11.5px;letter-spacing:.05em;
     text-transform:uppercase}
  tr.rejected{opacity:.62}
  td.url{max-width:460px;overflow-wrap:anywhere}
  a{color:var(--accent)}
  .ok{color:var(--ok)} .bad{color:var(--bad)} .warn{color:var(--warn)}
  .dim{color:var(--dim)}
  .kv{display:grid;grid-template-columns:170px 1fr;gap:5px 14px;font-size:13px}
  .kv div:nth-child(odd){color:var(--dim)}
  .mono{overflow-wrap:anywhere}
  .pill{display:inline-block;padding:2px 8px;border-radius:11px;font-size:11.5px;
        border:1px solid var(--line)}
  .pill.ok{border-color:var(--ok)} .pill.bad{border-color:var(--bad)}
  .pill.warn{border-color:var(--warn)}
  button{background:#1f2531;color:var(--text);border:1px solid var(--line);
         border-radius:7px;padding:8px 15px;cursor:pointer;font:inherit;font-size:13px}
  button:hover{border-color:var(--accent)}
  .verdict{font-size:19px;font-weight:700;letter-spacing:.04em}
  .empty{color:var(--dim);padding:36px 0;text-align:center}
  /* Narrow windows: stack the sidebar above the detail, and let the key/value grid
     collapse to one column rather than squeezing values into a few characters. */
  @media (max-width:860px){
    main{grid-template-columns:1fr}
    aside{border-right:none;border-bottom:1px solid var(--line);max-height:190px}
    .kv{grid-template-columns:1fr;gap:2px 0}
    .kv div:nth-child(even){margin-bottom:7px}
    td.url{max-width:none}
    section{padding:14px}
  }
</style></head><body>
<header>
  <h1>proof-of-match</h1>
  <span class="sub">local evidence viewer &middot; read-only</span>
  <span class="sub" id="chain"></span>
  <button onclick="loadRuns(true)" style="margin-left:auto">Refresh</button>
</header>
<main>
  <aside id="runs"><div class="empty">loading&hellip;</div></aside>
  <section id="detail"><div class="empty">Select a run.</div></section>
</main>
<script>
const $ = s => document.querySelector(s);
let current = null;

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function get(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

let listSignature = null;

async function loadRuns(force){
  try{
    const {runs, chain} = await get('/api/runs');
    $('#chain').textContent = chain.connected
      ? `chain ${chain.chain_id} &middot; block ${chain.block}` : 'chain offline';
    if(!runs.length){
      $('#runs').innerHTML = '<div class="empty">No bundles yet.<br><br>'
        + '<span class="dim">Run the pipeline first.</span></div>';
      listSignature = null;
      return;
    }

    // Re-render only when the set of runs actually changed. Rebuilding on every poll
    // dropped the selection and flickered the list, which is unusable while recording
    // and unusable to click.
    const signature = runs.map(r => r.id).join(',');
    if(signature === listSignature && !force){ return; }
    const keep = current;
    listSignature = signature;

    $('#runs').innerHTML = runs.map(r => `
      <div class="run" data-id="${esc(r.id)}">
        <b>${esc(r.id.slice(0,12))}</b>
        <span>${esc(r.created_at)}</span>
        <span>${r.matched ? '' : '<span class="warn">no match</span> &middot; '}${r.candidates} candidates</span>
      </div>`).join('');
    document.querySelectorAll('.run').forEach(el =>
      el.onclick = () => show(el.dataset.id));

    // Keep whatever was open; only fall back to the newest on first load.
    const stillThere = keep && runs.some(r => r.id === keep);
    if(stillThere){
      document.querySelectorAll('.run').forEach(el =>
        el.classList.toggle('on', el.dataset.id === keep));
      current = keep;
    } else {
      show(runs[0].id);
    }
  }catch(e){ $('#runs').innerHTML = `<div class="empty bad">${esc(e.message)}</div>`; }
}

async function show(id){
  current = id;
  document.querySelectorAll('.run').forEach(el =>
    el.classList.toggle('on', el.dataset.id === id));
  $('#detail').innerHTML = '<div class="empty">loading&hellip;</div>';
  try{
    const b = await get('/api/run/' + encodeURIComponent(id));
    const q = b.query, d = b.decision, m = b.merkle, a = b.attestation || {};

    const rows = b.candidates.map((c,i) => `
      <tr class="${c.accepted ? '' : 'rejected'}">
        <td class="dim">${i}</td>
        <td>${c.similarity == null ? '<span class="dim">--</span>'
              : `<b class="${c.accepted?'ok':'bad'}">${c.similarity.toFixed(4)}</b>`}</td>
        <td>${c.accepted ? '<span class="pill ok">accepted</span>'
                         : `<span class="pill">${esc(c.status)}</span>`}</td>
        <td>${c.social ? '<span class="pill warn">social</span>' : ''}</td>
        <td class="url"><a href="${esc(c.page_url)}" target="_blank"
            rel="noopener noreferrer">${esc(c.page_url)}</a></td>
      </tr>`).join('');

    $('#detail').innerHTML = `
      <div class="card"><h2>Query face</h2><div class="kv">
        <div>source</div><div>${esc(q.source_name)}</div>
        <div>confidence</div><div>${q.detect_score}</div>
        <div>bbox</div><div>${q.bbox.join(', ')}</div>
        <div>detected at</div><div>${q.detect_scale}px wide</div>
        <div>image sha256</div><div class="mono dim">${esc(q.image_sha256)}</div>
        <div>embedding sha256</div><div class="mono dim">${esc(q.embedding_sha256)}</div>
      </div></div>

      <div class="card"><h2>Search</h2><div class="kv">
        <div>provider</div><div>${esc(b.search.provider)}${
          b.search.provider === 'replay'
            ? ' <span class="pill warn">not a live query</span>' : ''}</div>
        <div>queried at</div><div>${esc(b.search.queried_at)}</div>
        <div>returned</div><div>${b.search.candidates_returned} candidates</div>
        <div>raw sha256</div><div class="mono dim">${esc(b.search.raw_sha256)}</div>
      </div></div>

      <div class="card"><h2>Candidates &mdash; threshold ${b.threshold}</h2>
        <table><thead><tr><th></th><th>similarity</th><th>result</th><th></th>
        <th>page</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="dim" style="font-size:12.5px;margin:11px 0 0">
          Dimmed rows were returned by the search and rejected here after
          re-downloading and re-embedding the image.</p>
      </div>

      <div class="card"><h2>Commitment</h2><div class="kv">
        <div>matched</div><div>${d.matched ? '<span class="ok">yes</span>'
                                           : '<span class="warn">no</span>'}</div>
        <div>accepted</div><div>${d.accepted_count}</div>
        <div>best match</div><div class="url">${d.best_page_url
          ? `<a href="${esc(d.best_page_url)}" target="_blank"
               rel="noopener noreferrer">${esc(d.best_page_url)}</a>
             &nbsp;<b class="ok">${d.best_similarity}</b>` : '<span class="dim">--</span>'}</div>
        <div>merkle root</div><div class="mono">${esc(m.root)}</div>
        <div>leaves</div><div>${m.leaf_count}</div>
        <div>network</div><div>${esc(a.network || '--')}</div>
        <div>contract</div><div class="mono dim">${esc(a.contract || '--')}</div>
        <div>tx</div><div class="mono dim">${esc(a.tx_hash || '--')}</div>
      </div></div>

      <div class="card"><h2>Verification</h2>
        <button onclick="verify()">Verify against the chain</button>
        <div id="verdict" style="margin-top:13px"></div>
      </div>`;
  }catch(e){ $('#detail').innerHTML = `<div class="empty bad">${esc(e.message)}</div>`; }
}

async function verify(){
  $('#verdict').innerHTML = '<span class="dim">checking&hellip;</span>';
  try{
    const v = await get('/api/verify/' + encodeURIComponent(current));
    const good = v.verdict === 'VERIFIED';
    $('#verdict').innerHTML = `
      <div class="verdict ${good?'ok':'bad'}">${esc(v.verdict)}</div>
      <div class="kv" style="margin-top:10px">
        <div>fields</div><div>${v.diverged.length
          ? `<span class="bad">${v.diverged.map(esc).join(', ')} changed</span>`
          : `<span class="ok">all ${v.leaf_count} match</span>`}</div>
        <div>recomputed root</div><div class="mono">${esc(v.recomputed)}</div>
        <div>roots agree</div><div class="${v.root_ok?'ok':'bad'}">${v.root_ok}</div>
        <div>on chain</div><div class="${v.on_chain?'ok':'bad'}">${
          v.on_chain ? 'found' : 'not found'}</div>
      </div>
      ${v.note ? `<p class="dim" style="font-size:12.5px">${esc(v.note)}</p>` : ''}`;
  }catch(e){ $('#verdict').innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
}

loadRuns();
</script></body></html>
"""


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

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

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

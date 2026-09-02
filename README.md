# proof-of-match

Hacker House Goa 2026 · Task #3 · Face Identification & Blockchain Verification

Scan a face, find where that face appears online through a genuine reverse image search,
and commit the result to a blockchain as a record that can be checked afterwards — and
shown to fail when the evidence is altered.

The interesting half is the second one. Writing a hash to a chain demonstrates nothing on
its own; "tamper-evident" is a claim until something detects a tamper. So the deliverable
here is not the write, it is `verify.py`, which reads the commitment back and either
confirms the evidence or names the field that changed.

```
py run.py    --image me.jpg --chain local          # scan -> search -> attest
py verify.py --bundle evidence/run-*.json          # VERIFIED
<edit one byte of the bundle>
py verify.py --bundle evidence/run-*.json          # TAMPERED: candidate[1]
```

## Quickstart

Python 3.12 and Node 22. No wallet, no faucet, no API key.

```bash
pip install -r requirements.txt
python -m playwright install chromium
py scripts/fetch_models.py          # 39 MB of ONNX weights, SHA-256 verified

npm install                         # hardhat only
npx hardhat compile
npx hardhat node                    # leave running in another terminal

py deploy.py --chain local
py preflight.py                     # verify the whole setup before you rely on it
py run.py    --image path/to/face.jpg --chain local
py verify.py --bundle evidence/run-<id>.json --chain local
```

## The three stages

### 1. Face — OpenCV 5.0 YuNet + SFace

Detection and a 128-d embedding, both shipping inside `opencv-python` as ONNX. Chosen over
dlib/`face_recognition` and insightface because those need a compiler toolchain on Windows
and this needs none.

YuNet **returns zero faces on large images, silently, at any confidence threshold**. A
4753×3840 portrait detected nothing at 0.9, 0.6 or 0.3; the same image at 640px detected a
face at 0.924. Detection therefore walks a scale ladder (1024 → 800 → 640 → 480) rather
than trusting a single attempt, and the bounding box is mapped back to source coordinates.

### 2. Search — no key, and verified rather than trusted

**Nothing here needs an API key.** `--backend auto` is the default: it tries the free
backends in order and uses the first that returns results, so one engine being rate-limited
does not end a run.

The input for this task is a photo the subject has publicly posted, which means a public
URL for it already exists — so the upload flow is avoidable entirely. That matters more
than it sounds. Measured on the same machine and the same image:

| backend | key | candidates | accepted |
|---|---|---|---|
| `bing_url` | none | 38 | **11 / 12** |
| `serpapi` | required | 10 | 6 / 10 |
| `bing_scripted` | none | 21 | 10 / 12 |

`bing_url` was returning results while `bing_scripted` was being served
human-verification challenges on that same machine — it is a plain page load rather than a
scripted upload form, so there is less to trip over. Pass `--image-url` to enable it.


A reverse image search returns pages that are *visually similar*. That is not the same
claim as "this is the same face", and a pipeline that reports search hits as identity
matches is asserting something it never checked.

So every candidate image is re-downloaded, re-encoded with the same model, and scored
against the query embedding. Only candidates clearing the cosine threshold are accepted,
and **the score is recorded either way** — rejects included, so the bundle shows what was
considered and dismissed rather than only what survived.

Candidates carry a second image URL. Social platforms block hotlinking of their own CDN,
so for exactly the results this task cares about, the primary URL is the one that will not
fetch — every Instagram and Facebook hit scored `download_failed` and nothing could be
verified. The search engine's own copy is smaller but reachable, and a low-resolution face
still embeds. The bundle records the URL actually fetched, not the one tried first.

The raw search response is persisted and hashed into the commitment. That hash is what
makes "this was a genuine search, not a hardcoded result" checkable by someone who did not
watch it run.

### 3. Chain — a Merkle root, not a blob

Committed on chain: a Merkle root over the run's evidence, the candidate count, and a
matched flag. **Not committed: images, URLs, embeddings, or any identifier of the person
matched.** The ledger is public and permanent; a commitment proves the run happened and
has not been altered without publishing who it was about.

A single digest could only answer "did anything change?". A root also lets one field be
disclosed and proved alone:

```
py verify.py --bundle evidence/run-<id>.json --disclose 1
  proving     similarity 0.954176 accepted=True
  proof       5 sibling hashes
  the contract accepts this leaf
```

`AttestationRegistry.verifyInclusion` checks that on chain with sorted-pair hashing,
matching OpenZeppelin's `MerkleProof` (inlined, to keep the repo npm-light).

## Which blockchain

Both, from one code path in `pom/chain.py`:

| Target | Chain ID | Why |
|---|---|---|
| `--chain local` | 31337 | Hardhat's node. No wallet, no faucet, no key — anyone who clones this can reproduce a full run offline. The brief permits a local/simulated chain explicitly. |
| `--chain sepolia` | 84532 | Base Sepolia. One real public transaction, independently inspectable in a block explorer. |

Measured on the local chain: **374,249 gas** to deploy. Attestations cost **113,722 gas for
the first one on a fresh contract** and **96,622 thereafter** — the first write pays for a
cold storage slot when the roots array is initialised.

> Base Sepolia deployment address and transaction hash: _pending — see Known limitations._

## Calibration

The default threshold (0.363) is OpenCV's published SFace same-identity figure. That is a
claim about their evaluation set, not this one, so `scripts/calibrate.py` measures it here.

12 images across 8 people — 10 same-identity pairs, 56 different-identity pairs:

| | min | max |
|---|---|---|
| same identity | **0.7876** | 0.9562 |
| different identity | −0.1134 | **0.2768** |

**Separation margin +0.5108.** At 0.363: 0 false negatives of 10, 0 false positives of 56.
The threshold sits inside a wide empty gap rather than on a decision boundary.

This set is small and its positives all come from one identity, so treat the margin as
evidence the threshold is not obviously wrong — not as a benchmark result. Reproduce with
`py scripts/calibrate.py`; raw pairs are in `bench/calibration.json`.

## Exit codes

"Found nothing" is a real outcome and should not look like success.

| Code | Meaning |
|---|---|
| 0 | A match cleared the threshold; its commitment is on chain |
| 2 | No candidate cleared the threshold — **nothing written** |
| 3 | The search provider served a bot challenge — nothing written |
| 4 | No face in the input image |
| 6 | `verify.py`: the evidence no longer matches what was committed |
| 7 | `verify.py`: the root is not on chain at all |

Exit 2 matters. A run that finds nothing leaves no attestation behind — the record is for
matches, not for attempts.

## Configuration

Everything above runs with no credentials. Two optional keys unlock the rest:

```bash
cp .env.example .env        # .env is gitignored
```

| Variable | Needed for | Notes |
|---|---|---|
| `SERPAPI_KEY` | `--backend serpapi` | **Optional.** The free backends need no key. Needs `--image-url` too; this backend will not upload your photo anywhere on your behalf. |
| `PRIVATE_KEY` | `--chain sepolia` | **Burner wallet only** — testnet funds, nothing real |
| `BASE_SEPOLIA_RPC` | optional | Defaults to `https://sepolia.base.org` |

`.env` is loaded automatically; real environment variables take precedence, so CI can set
them properly without a stale file overriding. `py preflight.py` reports which are
configured — presence and length only, never the value.

## Tests

```bash
py -m pytest          # 171 tests
py preflight.py       # environment, models, chain, and repo state
```

Tests are tiered by what they need: most run with no network, no chain and no models.
`needs_models` and `needs_chain` skip cleanly when those are absent, so a partial checkout
still gives a useful signal. CI runs every tier, including on-chain inclusion proofs
against a live Hardhat node, then smoke-tests the documented quickstart and asserts that
`verify.py` **rejects** a bundle it has deliberately tampered with.

`preflight.py` exists because the failure modes here are quiet ones. A truncated model does
not raise, it reports "no face". A restarted node leaves a deployed address that is no
longer a contract. Identical evidence hashes to a root that is already attested, so a rerun
sends no transaction. Each looks like a broken pipeline rather than a setup problem, and
`--recording` checks all of them before a take that cannot be redone.

## Known limitations

**Searches rate-limit, and challenges are not bypassed.** Under repeated automated use a
provider serves a `Verify you are human` page. The pipeline detects that, exits 3, and
writes nothing. **No CAPTCHA solving or evasion is implemented and none will be.** `auto`
mitigates this by falling through to another backend rather than failing outright, and
`--image-url` unlocks `bing_url`, which in practice survives longest. If everything is
challenged, wait and retry — that is the honest remedy.

**Search coverage is skewed.** Reverse image search finds indexed, public, well-crawled
pages. A face with no public web presence returns nothing — correctly, but that is a
property of the index, not evidence about the person.

**Matching is near-duplicate-biased.** SFace compares faces, but candidates only exist if
an engine surfaced the page, and engines favour visually similar *images*. Expect this to
find reposts of a photo far more reliably than a different photo of the same person.

**The calibration set is small.** 12 images, 8 people, positives from a single identity.
Enough to show the threshold sits in a gap; not enough to quote a false-positive rate at
any precision.

**`--chain sepolia` is untested end to end.** The code path is identical to local and the
local path is fully exercised, but no public transaction has been made yet — it needs a
funded account. Until that is filled in above, treat Base Sepolia support as written but
unproven.

**The `replay` backend is not a search.** It re-parses a saved response so downstream
stages stay testable while the live search is cooling down. It performs no query. It
commits `provider=replay` into the Merkle leaves, so any bundle it produces is permanently
self-labelled and cannot be presented as a live result.

**Identical evidence collides by design.** The root *is* the identity of a run, so
re-running over the same inputs produces the same root and the contract refuses a duplicate.
This is reported as `already attested` with the original transaction recovered from the
event log, not as a failure - verification of that bundle still succeeds. For a fresh
transaction, redeploy or use a different image.

**Consent.** Run this on your own face, or on faces whose owners agreed. It is a
face-to-social-media pipeline writing to an immutable ledger; that is worth being
deliberate about, which is also why only hashes go on chain.

## Layout

```
run.py                  end-to-end pipeline
verify.py               re-verification, tamper detection, selective disclosure
deploy.py               contract deployment
pom/face.py             YuNet detect -> SFace 128-d embed, scale ladder
pom/search.py           adapter: bing_scripted | serpapi | replay
pom/match.py            re-download, re-embed, cosine vs threshold
pom/evidence.py         bundle assembly, leaf derivation, field-level diff
pom/merkle.py           keccak256 sorted-pair tree, root + inclusion proofs
pom/chain.py            web3: deploy, record, read back, verify inclusion
contracts/AttestationRegistry.sol
scripts/calibrate.py    threshold measurement
scripts/fetch_models.py ONNX weights, SHA-256 verified
preflight.py            setup and recording sanity checks
tests/                  171 tests, tiered by what they require
spike/FINDINGS.md       day-1 search viability study
```

## Notes

`pom/__init__.py` pins `OPENBLAS_NUM_THREADS=1` before numpy loads. Running YuNet across a
batch of candidates otherwise exhausts OpenBLAS's per-thread arenas and the process dies
with `Memory allocation still failed after 10 retries`, taking buffered stdout with it — no
traceback, no output. The work here is ONNX inference, which OpenCV threads itself, so the
pin costs nothing.

Searches use a fresh browser context deliberately. A persistent profile — added to look
less like a bot — made Bing serve a stripped layout carrying **zero** result objects,
measured against 22 for a fresh context on the same image.

#FaceIDInGoa

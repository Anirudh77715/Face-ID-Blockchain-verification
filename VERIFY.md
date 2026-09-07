# Verifying this against the task requirements

Each of the six requirements, the command that demonstrates it, and what you should see.
Real output, captured from a live run — not illustrative.

Setup once:

```bash
pip install -r requirements.txt
python -m playwright install chromium
py scripts/fetch_models.py
npm install && npx hardhat compile
npx hardhat node          # leave running in another terminal
py deploy.py --chain local
```

Fastest possible check — eleven assertions in one command:

```bash
py preflight.py --e2e
```

It exits 0 only if the pipeline runs, verification accepts untouched evidence, **rejects**
tampered evidence, the on-chain inclusion proof holds, the no-face guardrail fires, consent
is accepted for its own image and refused for another, and a revoked attestation reports
NOT RELIABLE. That covers requirements 1, 3 and 4. Requirement 2 needs a live search, below.

---

## 1. Detect and encode a face — any library or API

```bash
py run.py --image spike/control_small.jpg --chain local --offline
```

```
bbox        (383, 266, 326, 479)
confidence  0.9071   (faces in frame: 1)
detected at 1024px wide
embedding   128-d  sha256 6d51774e25f87ebb07dbf10ce60392cc
annotated   evidence/face-control_small.png
```

OpenCV 5 YuNet detects, SFace encodes to 128 dimensions. Open the annotated PNG to see the
box drawn on the face — detection proved visually, not just as a number.

Threshold is measured, not assumed: `py scripts/calibrate.py` reports a **+0.5108**
separation margin over 66 pairs.

And it recognises the person rather than the file — `py scripts/crosscheck.py` matches a
1904 portrait to a 1947 one (+0.6168, a 43-year gap) while rejecting seven other people,
with a +0.3959 gap between the two groups. Worth running if anyone asks whether this is
really face recognition or just duplicate-image detection.

## 2. A real matching social media post, via genuine reverse-image search

Needs the network. `--image-url` is a public URL of the same photo.

```bash
py run.py --image me.jpg --image-url "https://<public url>" --chain local
```

```
resolved by bing_url
candidates  33 (12 on social platforms)
raw sha256  1b5edeebe27af22c8e496f23fb50faea

   0.9738   True    True  ok   https://medium.com/@CharleTheScientist/...
   0.9620   True    True  ok   https://www.pinterest.com/pin/albert-einstei...
   0.9505   True    True  ok   https://www.pinterest.com/pin/beauty-is-in-t...
accepted    8/12
```

Three things make "genuine, not hardcoded" checkable rather than merely claimed:

- **The raw response is saved and hashed into the commitment** (`search.raw_sha256`). A
  hardcoded result cannot produce a matching hash over a real provider response.
- **Rejected candidates stay in the bundle with their scores.** A fabricated result set
  would not carry four rows the pipeline declined.
- **`--headed` shows the browser doing it.** Nothing is stubbed.

Candidates are re-downloaded and re-embedded before being accepted, so a search hit is
never reported as a face match on the engine's word alone.

## 3. Upload to a blockchain — tamper-evident and verifiable

```
root      0x7b1f692e97b81de9d0de5b59a0648a6e6d75001539a9e8200ca07449b3464d59
contract  0xCD8a1C3ba11CF5ECfa6267617243239504a98d90
tx        0xc8ccb4d35c1151d81e85e2903eaeb7d8f7810327cfb3c8b702a5f2282159819d
block     55   gas 142061
```

**Verifiable** is the word in the requirement, so read it back:

```bash
py verify.py --bundle evidence/run-<id>.json --chain local     # exit 0, VERIFIED
```

**Tamper-evident** is a claim until a tamper is caught. Edit one character of any candidate
URL in the bundle and re-run:

```
1 field(s) no longer match the commitment:
  x candidate[0]
ROOTS DIFFER
                                                                exit 6, TAMPERED
```

Re-fetch the discovered post, hash it again, and compare against the chain — the
"get the post again" form of re-verification, as distinct from checking the saved bundle:

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --refetch
```

```
current post hash   ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
attested post hash  ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
on chain            that record is proved present in root 0xbdd05eeb83da7429...
POST UNCHANGED                                                  exit 0, VERIFIED
```

A post that has changed or vanished since attestation exits 9, not 6 — a deleted post is
not a forged one.

Prove one field on chain without revealing the rest:

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --disclose 2
```

```
proving     similarity 0.973821 accepted=True
proof       5 sibling hashes
the contract accepts this leaf
```

Withdraw an attestation, and watch verification change its answer:

```bash
py revoke.py --bundle evidence/run-<id>.json --reason "false positive" --yes
py verify.py --bundle evidence/run-<id>.json --chain local     # exit 8, NOT RELIABLE
```

NOT RELIABLE is deliberately distinct from TAMPERED: intact evidence that has been
withdrawn is a different situation from evidence that was altered.

**Which blockchain:** Hardhat locally (chain 31337) and Base Sepolia (84532), one code path
in `pom/chain.py`. Local needs no wallet, faucet or key, so this is reproducible offline.

## 4. No website or hosting needed

Nothing is hosted, and nothing needs to be. Two optional local interfaces exist and the
pipeline is complete without either:

- `viewer.py` — read-only, localhost-only. Draws the Merkle tree and runs verification as
  visible steps.
- `server.py` — a localhost console for supplying a photo by file or by link. It spawns
  `run.py` and `verify.py` as subprocesses and streams their real output, so it cannot
  show a result the command line would not. It is bound to `127.0.0.1`.

Neither is a project website, neither is deployed, and every requirement above is met from
the command line alone.

## 5. Source on GitHub, with a README

`README.md` covers what it does, how to run it, which blockchain, and known limitations —
the four topics the brief names. The limitations section is honest about the rate limit, the
skew in search coverage, the small calibration set, and that consent proves a *key* signed,
not that the key belongs to the named person.

```bash
py -m pytest        # 294 tests: 291 pass, 3 skip without network
py -m ruff check .
```

## Beyond the requirements: Persistent Face ID

Not required by the task, and it changes none of the six answers above - the pipeline runs
identically with `--no-faceid`. It adds an anonymous, persistent Face ID so a second
photograph of the same person is recognised as the same face:

```bash
py run.py --image bench/faces/einstein-0.jpg --offline    # NEW FACE REGISTERED - F-001
py run.py --image bench/faces/einstein-x1.jpg --offline   # MATCH FOUND - F-001, 0.9386
py run.py --image bench/faces/curie-0.jpg --offline       # 0.2315 -> NO MATCH, F-002
```

The second run has a different image SHA-256 and a different embedding; the match is on
the face. Thresholds are calibrated separately from the 0.363 candidate threshold
(`py scripts/calibrate_faceid.py`), embeddings stay off-chain in `evidence/faceids.json`,
and the Face ID is committed as a Merkle leaf so altering it is caught like any other
field. A Face ID is a label for a face, never a claim about a person's identity.

## 6. An unedited screen recording, end to end

`DEMO.md` is the runbook. Steps 1–7 are the submission; 8–10 are optional.

Rehearse with `--offline` — never with a live search, because that is what consumes the
rate limit you need for the take. There are no resubmissions.

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

Nothing is hosted. `viewer.py` exists and is optional, read-only, and localhost-only — it
draws the Merkle tree and runs verification as visible steps, but the pipeline is complete
without it and never depends on it.

## 5. Source on GitHub, with a README

`README.md` covers what it does, how to run it, which blockchain, and known limitations —
the four topics the brief names. The limitations section is honest about the rate limit, the
skew in search coverage, the small calibration set, and that consent proves a *key* signed,
not that the key belongs to the named person.

```bash
py -m pytest        # 260 tests
py -m ruff check .
```

## 6. An unedited screen recording, end to end

`DEMO.md` is the runbook. Steps 1–7 are the submission; 8–10 are optional.

Rehearse with `--offline` — never with a live search, because that is what consumes the
rate limit you need for the take. There are no resubmissions.

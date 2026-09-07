# Recording runbook

One take, no resubmissions. The brief asks for a plain unedited screen recording of the
pipeline running end to end: face scan → social post found → blockchain upload/verification.

## The one thing that can go wrong

The live Bing search is the only non-deterministic step, and it rate-limits into a
`Verify you are human` challenge after a few automated runs. Everything downstream —
verify, tamper, disclosure — is deterministic and re-runnable against the bundle the live
run produces.

So:

- **Rehearse with `--offline`.** It exercises every stage with no network at all, so
  rehearsing cannot consume the rate limit you need for the take. Warm the cache with one
  online run first.
- **Do not do a practice `bing_scripted` run right before the real take.** That is exactly
  what triggers the rate limit. Practising live is how you lose the take.
- **Run the live search once.** Steps 4–7 all reuse its bundle, so a single successful
  search carries the whole recording.
- If you are challenged anyway: stop, wait a few hours, restart. Do not attempt to solve
  the challenge on camera — the README's claim is that this pipeline does not evade bot
  detection, and the recording should be consistent with that.

## Before you hit record

```bash
npm install && npx hardhat compile
npx hardhat node               # separate terminal
py deploy.py --chain local
py preflight.py --e2e --recording   # must exit 0; read every warning
```

`preflight --e2e --recording` is the whole checklist in one command: it runs the pipeline
and asserts all eleven outcomes, including that consent is refused for the wrong image and
that a revoked attestation reports NOT RELIABLE. It also: models present and
SHA-256 clean, chromium launching, contract compiled and deployed, tests passing, and a
warning if the contract already holds attestations - because re-running identical evidence
sends no transaction, which on camera looks like the chain write silently failing.

- [ ] Your photo is ready — one you have **publicly posted** (profile picture works),
      plus its public URL for `--image-url`. Confirm it is findable: search it once, hours
      before the take, then leave the engines alone.
- [ ] Consent signed: `py consent.py --image me.jpg --subject "Your Name"`.
- [ ] `py viewer.py` running in a third terminal if you want the visual half.
- [ ] Two terminals open, font size up, window large enough to read on playback.
- [ ] `git log --oneline` looks clean.
- [ ] `evidence/` cleared if you want a tidy run: `rm -rf evidence/`
- [ ] If using Base Sepolia: `PRIVATE_KEY` exported, account funded, `py deploy.py
      --chain sepolia` already done, address in README.

## The take

### 1 — What this is (~20s)

Show `README.md`. Say the one-line version: face scan, genuine reverse image search,
Merkle root on chain, and a verifier that can detect tampering.

### 2 — Start the chain

```bash
npx hardhat node
```

Second terminal:

```bash
py deploy.py --chain local
```

Point at the contract address and deploy gas.

### 3 — Consent (~30s)

```bash
py consent.py --image me.jpg --subject "Your Name"
```

Say what it does and why: the statement names the image by SHA-256, so consent for one
photo cannot authorise a scan of another, and only the hash reaches the chain. This is the
part that makes a face-search pipeline defensible rather than merely impressive, and almost
nobody else will have it.

### 4 — The pipeline, live

```bash
py run.py --image me.jpg --image-url "https://<your public photo url>"           --consent consent.json --chain local --headed
```

Pass `--image-url` - the public URL of that same photo. It selects the URL-based backend,
which needs no key, no upload, and in testing survived rate limiting far longer than the
upload flow. `auto` falls through to the upload backend by itself if that URL fails, so a
single blocked engine no longer ends the take.

`--headed` shows the browser doing the search. Let it be visible — that is the proof the
search is genuine rather than hardcoded.

Narrate as the stages print:

- **face** — bounding box, confidence, 128-d embedding, image SHA-256
- **search** — which backend resolved it, candidate count, and that the raw response is
  saved and hashed. If `auto` fell through, it prints what it tried
- **verify** — each candidate re-downloaded and re-embedded. Point at a row where
  `status` is `no_face` or the similarity is below threshold: *the search returned it,
  the pipeline rejected it.* This is the difference between a search hit and a face match.
- **evidence** — leaf count and the Merkle root
- **chain** — contract, tx hash, block, gas

Copy the bundle path it prints.

### 5 — Read it back

```bash
py verify.py --bundle evidence/run-<id>.json --chain local
```

All fields match, roots agree, root found on chain, **VERIFIED**. Say plainly: this is the
data coming back *off* the chain, not just going on.

### 5b — Fetch the post again and re-hash it

This is the shape the task's own example asks for, so film it.

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --refetch
```

```
current post hash   ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
attested post hash  ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
on chain            that record is proved present in root 0xbdd05eeb83da7429...
POST UNCHANGED
```

Two sentences worth saying out loud while it is on screen:

- The post was **downloaded again just now** — the cache is bypassed, so this is not a
  digest being compared against itself.
- The attested hash is not merely read out of the local file; the record containing it is
  **proved to be inside the root the contract holds**, by an inclusion proof the contract
  itself checks.

If a post 404s or a CDN blocks the hotlink, this reports **unreachable** and stays exit 0.
That is not a failure — it is the check refusing to call a missing post a forged one.

### 5c — Persistent Face ID (optional layer, films in about a minute)

Four steps, in this order. Start from a clean registry if you want F-001 to be the first
id on screen: `py faceids.py list` shows what is already there.

```bash
py run.py --image bench/faces/einstein-0.jpg --offline      # 1: new face
py run.py --image bench/faces/einstein-x1.jpg --offline     # 2: same person, new file
py run.py --image bench/faces/curie-0.jpg --offline         # 3: different person
py faceids.py list
```

What to point at on screen:

1. **NEW FACE REGISTERED - F-001.** The registry was empty, so there is no similarity to
   report.
2. **MATCH FOUND - F-001, similarity 0.9386.** Say the important part out loud: the image
   SHA-256 is *different* (`ce1d7bb0...` vs `d45ce90a...`) and the embedding is different.
   The match is on the face, not the file. Photos on record goes 1 -> 2.
3. **similarity 0.2315, below the 0.40 review floor -> F-002.** A different person gets a
   different id rather than being merged.
4. `faceids.py list` shows F-001 with 2 photographs and F-002 with 1.

Then show it does not weaken anything (this is step 6 on a bundle that carries a Face ID):

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --refetch
```

Edit `"face_id"` inside the bundle's `faceid` block and re-verify: **TAMPERED, naming
`faceid`, exit 6.** The Face ID is committed like every other field.

Two sentences worth saying, because a judge will otherwise ask:

- F-001 is an **anonymous label**. It is not a name, and nothing here claims to know who
  anyone is.
- The Face ID thresholds are **separate** from the 0.363 candidate threshold and were
  measured for this job - `py scripts/calibrate_faceid.py` prints the measurement.

### 6 — Break it (the point of the build)

Open the bundle in an editor, on camera. Change one character of a candidate URL. Save.

```bash
py verify.py --bundle evidence/run-<id>.json --chain local
```

**TAMPERED**, naming `candidate[N]`, roots differ, recomputed root not on chain, exit 6.
Undo the edit.

### 7 — Prove one field without revealing the rest

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --disclose 1
```

The contract accepts a small inclusion proof for a single candidate. Nothing else about the
run was sent on chain to check it — which is why no personal data has to live on a public
ledger.

### 8 — When it finds nothing

```bash
py run.py --image spike/noface.jpg --chain local        # exit 4, no face
```

Optionally, a real face with no online presence → exit 2, nothing written. Say it: a run
that finds nothing leaves no attestation behind.

### 9 — Withdraw it

```bash
py revoke.py --bundle evidence/run-<id>.json --reason "false positive" --yes
py verify.py --bundle evidence/run-<id>.json --chain local
```

**NOT RELIABLE**, exit 8 — deliberately not TAMPERED. The evidence is intact; the submitter
withdrew it. Chain history cannot be erased, so the record stands next to its withdrawal
and `isLive()` returns false for anyone who asks the contract directly.

### 10 — The viewer (optional, but it films well)

```bash
py viewer.py        # http://127.0.0.1:8000
```

Verification runs as four visible steps rather than one verdict. Click a Merkle leaf and
its path to the root lights up, with the siblings a proof would supply alongside - the
clearest way to show why disclosing one field reveals nothing else.

The tamper simulator lets you edit a candidate in the page and watch the root move away
from the one on chain, live. **Say out loud that those boxes forge the recorded evidence
and are not pipeline inputs** - they look like inputs, and a viewer who thinks you are
searching a new image will read a correct rejection as a broken pipeline. Nothing is
written either way.

### 11 — Close

`git log --oneline`, and the README's Known limitations section. Naming the rate limit and
the fact that CAPTCHAs are not bypassed is a strength, not an apology.

## Length

Eight to ten minutes. Steps 1-7 are the submission; 8-10 are worth including only if the
pace holds, and 9 and 10 can be dropped entirely without weakening the case. The brief asks
for face scan -> social post found -> blockchain upload/verification, and steps 4-6 are
that, so protect those. The brief says no editing or production is needed, so do
not add any — dead air while the search runs is fine and reads as honest.

## After

- Upload unlisted (YouTube / Drive / Loom) and check the link works signed out.
- Submit repo link + recording link: https://forms.gle/oZbQGuwiNeiIVcHWo8
- **No resubmissions.** Watch your own recording once, all the way through, before you
  submit the link.

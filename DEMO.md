# Recording runbook

One take, no resubmissions. The brief asks for a plain unedited screen recording of the
pipeline running end to end: face scan → social post found → blockchain upload/verification.

## The one thing that can go wrong

The live Bing search is the only non-deterministic step, and it rate-limits into a
`Verify you are human` challenge after a few automated runs. Everything downstream —
verify, tamper, disclosure — is deterministic and re-runnable against the bundle the live
run produces.

So:

- **Rehearse with `--backend replay`.** It exercises every stage without touching Bing.
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
py preflight.py --recording    # must exit 0; read every warning
```

`preflight --recording` is the whole checklist in one command: models present and
SHA-256 clean, chromium launching, contract compiled and deployed, tests passing, and a
warning if the contract already holds attestations - because re-running identical evidence
sends no transaction, which on camera looks like the chain write silently failing.

- [ ] Your photo is ready — one you have **publicly posted** (profile picture works).
      Confirm it is findable: search it once, hours before the take, then leave Bing alone.
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

### 3 — The pipeline, live

```bash
py run.py --image me.jpg --image-url "https://<your public photo url>" --chain local --headed
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

### 4 — Read it back

```bash
py verify.py --bundle evidence/run-<id>.json --chain local
```

All fields match, roots agree, root found on chain, **VERIFIED**. Say plainly: this is the
data coming back *off* the chain, not just going on.

### 5 — Break it (the point of the build)

Open the bundle in an editor, on camera. Change one character of a candidate URL. Save.

```bash
py verify.py --bundle evidence/run-<id>.json --chain local
```

**TAMPERED**, naming `candidate[N]`, roots differ, recomputed root not on chain, exit 6.
Undo the edit.

### 6 — Prove one field without revealing the rest

```bash
py verify.py --bundle evidence/run-<id>.json --chain local --disclose 1
```

The contract accepts a small inclusion proof for a single candidate. Nothing else about the
run was sent on chain to check it — which is why no personal data has to live on a public
ledger.

### 7 — When it finds nothing

```bash
py run.py --image spike/noface.jpg --chain local        # exit 4, no face
```

Optionally, a real face with no online presence → exit 2, nothing written. Say it: a run
that finds nothing leaves no attestation behind.

### 8 — Close

`git log --oneline`, and the README's Known limitations section. Naming the rate limit and
the fact that CAPTCHAs are not bypassed is a strength, not an apology.

## Length

Six to eight minutes is plenty. The brief says no editing or production is needed, so do
not add any — dead air while the search runs is fine and reads as honest.

## After

- Upload unlisted (YouTube / Drive / Loom) and check the link works signed out.
- Submit repo link + recording link: https://forms.gle/oZbQGuwiNeiIVcHWo8
- **No resubmissions.** Watch your own recording once, all the way through, before you
  submit the link.

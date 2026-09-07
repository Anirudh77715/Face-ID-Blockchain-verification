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

## Setup

**No wallet, no faucet, no API key, and no compiler.** Everything below is prebuilt
wheels and one npm package. Budget about **10 minutes**, most of it downloading.

### What you need first

| | Version | Check with |
|---|---|---|
| Python | **3.10 or newer** (3.12 is what this was built on) | `py --version` on Windows, `python3 --version` on macOS |
| Node.js | **20 or newer** (22 recommended) | `node --version` |
| Disk | ~700 MB | models 39 MB, Chromium ~150 MB, node_modules ~400 MB |

Nothing else. No CMake, no MSVC, no Xcode command line tools - the face models ship as
ONNX inside `opencv-python`, which is the whole reason this project avoids
dlib / `face_recognition`.

### macOS (Apple Silicon or Intel)

```bash
brew install python@3.12 node          # skip either if you already have it
git clone https://github.com/Anirudh77715/Face-ID-Blockchain-verification.git
cd Face-ID-Blockchain-verification

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
python scripts/fetch_models.py         # 39 MB of ONNX weights, SHA-256 verified

npm install                            # hardhat only
npx hardhat compile
```

**One macOS version check before you start.** OpenCV 5.0 ships prebuilt wheels for
`macosx_13_0_arm64` and `macosx_14_0_x86_64` - so **Apple Silicon needs macOS 13+, Intel
needs macOS 14+**. Below those, pip falls back to the source tarball and tries to compile
OpenCV, which takes the better part of an hour and usually fails. `sw_vers` tells you your
version. On an older macOS, use a machine that meets the floor rather than fighting the
build.

On macOS every command below written as `py` is `python`.

### Windows

Open **PowerShell** (not cmd.exe) and run:

```powershell
winget install Python.Python.3.12                    # skip if you have Python 3.10+
winget install OpenJS.NodeJS                         # skip if you have Node 20+

git clone https://github.com/Anirudh77715/Face-ID-Blockchain-verification.git
cd Face-ID-Blockchain-verification

py -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
py -m playwright install chromium
py scripts/fetch_models.py

npm install
npx hardhat compile
```

`py` is the Windows Python launcher; it ships with the official installer, which is why
every command in this README uses it rather than `python`.

**If activation fails with "running scripts is disabled on this system"**, that is
PowerShell's execution policy, not a problem with this project. Allow local scripts for
your own user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then run `.venv\Scripts\Activate.ps1` again. You will see `(.venv)` at the start of the
prompt when it has worked. If you would rather change nothing, `cmd.exe` needs no policy
change - activate with `.venv\Scripts\activate.bat` there instead.

**Close and reopen PowerShell after installing Python or Node**, or `py` and `npm` will
not be on PATH yet in the window you already had open.

### Then, on either platform

You need **two terminals**. The chain runs in the foreground and stays running.

Terminal 1 - the local blockchain, left alone:

```bash
npx hardhat node
```

Terminal 2 - everything else (activate the venv here too):

```bash
py deploy.py --chain local
py preflight.py                    # 13 checks; run this before you trust anything
py run.py --image path/to/face.jpg --chain local        # a path or an image URL
py verify.py --bundle evidence/run-<id>.json --chain local
py verify.py --bundle evidence/run-<id>.json --chain local --refetch
```

`preflight.py` is the fastest way to find out whether the setup is sound: it checks the
Python version, every import, both model files by SHA-256, that a face actually detects,
that Chromium launches, that the search parser works, and that the chain is reachable. If
it prints `all required checks passed`, everything else in this README will work.

The last line downloads the discovered post again, hashes it again, and compares that
against the hash the chain holds - the strongest form of the re-verification the task asks
for.

### Optional web console

If you would rather drop a photo in a browser than type a path:

```bash
py server.py                       # then open http://127.0.0.1:8000
```

It adds nothing to the pipeline - it spawns `run.py` and `verify.py` and streams their real
output - so the command line stays the source of truth. Open it at
`http://127.0.0.1:8000`, **not** by double-clicking `server_page.html`: from a `file://`
origin the browser blocks its own API calls and every button silently does nothing.

### Credentials - all optional

`cp .env.example .env` only if you want one of two things. Neither is needed for anything
in this README:

- `SERPAPI_KEY` - a fallback search backend, free tier 100/month. The no-key backends are
  the default and in practice the more reliable path.
- `PRIVATE_KEY` - only for `--chain sepolia`. **Use a burner funded from a faucet.**
  `--chain local` needs no wallet at all.

`.env` is gitignored. `preflight.py` reports whether a key is present and its length, never
its value.

### If something goes wrong

| Symptom | Cause |
|---|---|
| `cannot reach the local RPC` | `npx hardhat node` is not running, or is in a terminal you closed |
| pip starts *building* opencv | macOS below the version floor above, or a 32-bit Python |
| `missing model: models/yunet.onnx` | `scripts/fetch_models.py` has not been run |
| `no face detected` on a big photo | genuinely no face - detection already retries down a scale ladder |
| search exits 3 | the provider served a bot challenge. Wait, or pass `--image-url`. No CAPTCHA is bypassed, by design |
| verify says the contract holds no attestations | the chain was restarted, which wipes it. Re-run the pipeline |
| web console buttons do nothing | you opened the HTML file directly instead of `http://127.0.0.1:8000` |
| `running scripts is disabled on this system` | PowerShell execution policy - see the Windows section above |
| `py` or `npm` is not recognised | Python or Node was installed into a PowerShell window that is still open. Close it and open a new one |

### Reproducing without a network

```bash
py run.py --image spike/control_small.jpg --offline
```

Replays a saved search against cached images, so the whole pipeline runs unplugged. The
bundle records `provider=replay` in its Merkle leaves, so a replayed run is permanently
self-labelled and can never be presented as a live result.

## The three stages

### 1. Face — OpenCV 5.0 YuNet + SFace

Detection and a 128-d embedding, both shipping inside `opencv-python` as ONNX, so neither
needs a build step.

dlib/`face_recognition` still needs CMake and MSVC on Windows. **insightface no longer
does** — 1.0.1 ships a pure-Python wheel, and this repo's earlier claim that it needed a
compiler was out of date; it was checked and corrected rather than left standing. The
reason to stay on SFace is a different one: it is measurably good enough here. It matches a
1904 portrait to a 1947 one across a 43-year gap and rejects seven other people, with a
+0.396 margin between the two groups (`scripts/crosscheck.py`), and a +0.51 separation over
66 calibration pairs.

A 512-d ArcFace model would extend that further — heavier pose, lower resolution, larger
populations where near-misses accumulate — at the cost of ~300 MB of weights and a full
re-calibration. That trade is recorded under Known limitations rather than quietly taken.

The run also writes an annotated copy showing the detected box, which the viewer displays.
A pipeline reporting `bbox (383, 266, 326, 479)` has proved detection to itself; drawing the
box proves it to whoever is watching, and makes a mis-detection obvious rather than a
plausible-looking number. It is a rendering, not evidence - the image it renders is already
bound by `query.image_sha256`, so it adds no leaf.

YuNet **returns zero faces on large images, silently, at any confidence threshold**. A
4753×3840 portrait detected nothing at 0.9, 0.6 or 0.3; the same image at 640px detected a
face at 0.924. Detection therefore walks a scale ladder (1024 → 800 → 640 → 480) rather
than trusting a single attempt, and the bounding box is mapped back to source coordinates.

### Input — a file or a URL, any face

The photo can be either, and one URL can do both jobs at once:

```bash
py run.py --image me.jpg --image-url "https://..."   # local file, hosted copy
py run.py --image "https://..."                      # one URL, used for both
py run.py --image-url "https://..."                  # same thing, shorter
```

Requiring a local file made the common case awkward: the input for this task is a photo
the subject already posted, so it already lives at a URL. What is committed is always the
SHA-256 of the bytes actually scanned, so a run from a URL and a run from a downloaded copy
of the same file produce the same identity — and a URL whose content changes later cannot
quietly rewrite what was attested.

Nothing is specific to any one face. Three different people, three input forms, all live:

| subject | input form | backend | candidates | accepted |
|---|---|---|---|---|
| Einstein | local file | bing_url | 33 (12 social) | 8 / 12 |
| Marie Curie | `--image <url>` | yandex_url | 272 (71 social) | 11 / 12 |
| Nikola Tesla | `--image-url <url>` | bing_url | 36 (11 social) | 10 / 12 |

Pasting the page a photo sits on rather than the image itself is the usual mistake, so that
is refused with the fix rather than a decode error further down:

```
that URL returned 'text/html', not an image.
  Use a direct image link - the one you get from 'Copy image address', not the page it sits on.
```

### 2. Search — no key, and verified rather than trusted

**Nothing here needs an API key.** `--backend auto` is the default: it tries the free
backends in order and uses the first that returns results, so one engine being rate-limited
does not end a run.

The input for this task is a photo the subject has publicly posted, which means a public
URL for it already exists — so the upload flow is avoidable entirely. That matters more
than it sounds. Measured on the same machine and the same image:

| backend | key | candidates | social | accepted | similarity range |
|---|---|---|---|---|---|
| **`yandex_url`** | none | **327** | **64** | 8 / 12 | 0.91 – 0.98 |
| `bing_url` | none | 38 | 12 | 11 / 12 | 0.77 – 0.97 |
| `bing_scripted` | none | 21 | 7 | 10 / 12 | 0.77 – 0.97 |
| `serpapi` | required | 10 | 5 | 6 / 10 | 0.73 – 0.95 |

`bing_url` was returning results while `bing_scripted` was being served
human-verification challenges on that same machine — it is a plain page load rather than a
scripted upload form, so there is less to trip over.

`auto` tries `bing_url`, then `yandex_url`, then the upload flow. The ordering matters more
than the ranking: two Bing routes share an index and a failure mode, so a chain of them is
one provider wearing two hats. Yandex is a genuinely independent index, which makes it the
fallback that actually helps when Bing is the thing being challenged. Pass `--image-url` to
enable both.

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

### 3. Chain — consent, a Merkle root, and the ability to withdraw

Thinking about this as a product rather than a pipeline surfaces two things a face-matching
registry cannot honestly ship without.

**Consent.** A system that takes a face and finds where that person appears online is only
defensible if they agreed. Saying so in a README is not a mechanism. The subject signs a
statement naming the exact image by SHA-256 — so consent for one photo cannot authorise a
scan of another — and the hash of that signed record is committed on chain beside the
evidence root, and as a Merkle leaf. The document, the name and the signature stay local;
only a hash is public.

```bash
py consent.py --image me.jpg --subject "Your Name"
py run.py --image me.jpg --consent consent.json --chain local
```

The pipeline **refuses** a consent that does not cover the image it is scanning. A
mismatched consent is worse than none, because it looks like authorisation. An attestation
without consent is still permitted — the contract stores zero — but verification reports it
differently, because "we do not know if they agreed" is not the claim "they agreed".

**Revocation.** A match can be wrong, or a subject can withdraw. Chain history cannot be
erased, so the attestation is marked withdrawn instead:

```bash
py revoke.py --bundle evidence/run-<id>.json --reason "false positive"
```

`isLive()` goes false, `exists()` stays true, and `verify.py` reports **NOT RELIABLE**
(exit 8) rather than VERIFIED — a distinct outcome from TAMPERED, because intact evidence
that has been withdrawn is a different situation from evidence that was altered. Only the
original submitter may revoke; a registry where anyone can withdraw anyone's record is
worse than one with no revocation at all.

Public chains wait **3 confirmations**, because a just-mined block can reorg out and leave
a bundle citing a transaction that never happened.

### The commitment — a Merkle root, not a blob

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

### Two different questions

Checking the bundle proves the **evidence file** has not been altered since it was
committed. It does not prove the **post** is still what it was. `--refetch` answers the
second question by downloading the discovered post again, hashing it again, and comparing
that against the hash the chain holds:

```
py verify.py --bundle evidence/run-<id>.json --chain local --refetch

  current post hash   ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
                      SHA-256 of the 57640 bytes just downloaded
  attested post hash  ce63b6c6ccb841e8be28a19efb97085b3c2e380ec87823014313ab2d0ebb42cb
                      committed at run time as part of candidate[7]
  on chain            that record is proved present in root 0xbdd05eeb83da7429...
                      by an inclusion proof of 4 sibling hashes, checked
                      by the contract itself

  POST UNCHANGED - the live post still hashes to what is on chain
```

The cache is bypassed on purpose: `pom/cache.py` stores the very bytes that were hashed
during the run, so reading them back would compare a digest against itself and always
agree.

A mismatch here is **not** tampering, and is reported as its own outcome (exit 9). A post
can be deleted, edited by its author, or served re-encoded by a CDN that never touched the
pixels — all change the hash with nobody acting in bad faith. Exit 6 stays reserved for
evidence that no longer matches its own commitment.

## Persistent Face ID

An added layer, not part of the task's required pipeline, and it changes none of it. It
answers a question the rest of the project deliberately does not: **have we seen this face
before?**

```
py run.py --image photo.jpg --chain local        # Face ID runs as stage 1c
py faceids.py list                               # every Face ID
py faceids.py show F-001                         # its photographs and attestations
py faceids.py check --image other.jpg            # score a photo, record nothing
```

A Face ID is an anonymous label - `F-001` means "the face first seen in that run". **The
registry stores no names and infers none.** A hit is reported as *"matches Face ID F-001"*
or *"likely the same face"*, never as an identity. Nothing in this project verifies who
anyone is, so no output claims to.

### Three mechanisms, three questions

They are easy to conflate and the code keeps them apart:

| | answers | used for identity? |
|---|---|---|
| image SHA-256 | is this the same *file*? | **no** |
| face embedding | is this likely the same *face*? | yes, as evidence |
| Merkle root on chain | does the evidence still match its commitment? | **no** |

Two photographs of one person have different image hashes. That is expected, and the image
hash is never consulted to decide whether two photos show the same person.

### Thresholds

Persistent identity is a **separate calibration problem** from web-candidate matching, and
gets its own values. The candidate threshold (0.363) compares a returned image against the
query, where candidates are near-duplicates. A Face ID lookup runs against every face in
the registry, and a false merge collapses two people into one identifier.

| state | condition | what happens |
|---|---|---|
| MATCH | `similarity >= high` (0.66) | the photo joins the existing Face ID |
| REVIEW | `review <= similarity < high` | flagged, and a **new** Face ID is created |
| NO MATCH | `similarity < review` (0.40) | a new Face ID is created |

Configurable three ways - flag beats environment beats default:

```bash
py run.py --image a.jpg --faceid-high 0.72 --faceid-review 0.45
POM_FACEID_HIGH=0.72  POM_FACEID_REVIEW=0.45
```

Where the defaults came from - `py scripts/calibrate_faceid.py` over `bench/faces`:

```
images 12   people 8
SAME PERSON, different photograph (10 pairs)   0.7876 .. 0.9562
DIFFERENT PEOPLE (56 pairs)                    highest 0.2768
separation                                     +0.5108
```

**These are provisional defaults measured on a 12-image set, not authoritative values.**
See Known limitations.

### Storage

`evidence/faceids.json`, local and gitignored. Per Face ID: a created timestamp and one
entry per photograph holding the image SHA-256, the 128-d embedding, and a reference to the
attestation if the run reached a chain.

**No biometric data goes on chain.** The embedding stays in that file. What the chain sees
is the evidence Merkle root, which includes `query.embedding_sha256` and a `faceid` leaf
recording the Face ID and similarity - hashes of the representation, never the
representation.

### Multiple photographs per face

A Face ID accumulates embeddings rather than being judged forever by its first photograph.
A new photo is scored against **every** stored embedding and the face takes its **maximum**
- a face matches if the new photo resembles *any* photograph recorded for it. The mean was
rejected because it punishes a well-documented face: each extra sighting drags the average
down, so the more photographs a face has the harder it becomes to recognise, which is
backwards. The cost of the maximum is that one bad sighting can pull a stranger in, which
is what REVIEW is for.

Re-scanning an identical file adds no sighting - the same bytes carry no new information.

## Which blockchain

Both, from one code path in `pom/chain.py`:

| Target | Chain ID | Why |
|---|---|---|
| `--chain local` | 31337 | Hardhat's node. No wallet, no faucet, no key — anyone who clones this can reproduce a full run offline. The brief permits a local/simulated chain explicitly. |
| `--chain sepolia` | 84532 | Base Sepolia. One real public transaction, independently inspectable in a block explorer. |

Measured on the local chain by `py scripts/gas.py`, which deploys a throwaway registry so
the numbers are not skewed by whatever is already stored:

| operation | gas |
|---|---|
| deploy the registry | 557,787 |
| record, no consent (first on a contract) | 121,789 |
| record, no consent (subsequent) | 104,677 |
| record, with consent | 124,961 |
| revoke | 52,979 |

The first record costs more because it pays for the roots array's cold storage slot.
Consent adds 20,284 gas — one more word written.

These are regenerated rather than remembered. They went stale once already: the struct grew
two fields for consent and revocation, and the quoted costs silently became wrong.

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

## Is it recognising the person, or just the photo?

A reverse image search finds near-duplicates. If the face stage only ever agreed with the
search about near-duplicates, this would be an image-duplicate detector in a face
recognition costume. So the question gets asked directly:

```bash
py scripts/crosscheck.py
```

```
SAME PERSON, a different photograph
  1904, age 25 (43-year gap)     +0.6168  MATCH
  1921, age 42 (26-year gap)     +0.6471  MATCH
  einstein-x0 .. x3              +0.80 to +0.96  MATCH

DIFFERENT PEOPLE
  bohr, curie, tesla, turing,
  feynman, gandhi, hopper        +0.04 to +0.22  rejected

worst same-person   +0.6168
best different      +0.2210
gap                 +0.3959      missed 0/6, false matches 0/7
```

A 1904 portrait and a 1947 portrait share almost no pixels; anything matching images rather
than faces fails there. Identity survives the change of photograph, seven other people are
rejected, and the 0.363 threshold sits inside a 0.396-wide gap between the two groups.

This is also why `--image-url` matters. The search decides *which* photos get considered;
the encoder decides whether they are the same face. Give it a URL of a photo you posted and
it can find other pictures of you, not only reposts of that one file.

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
| 8 | `verify.py`: **NOT RELIABLE** — evidence intact, but revoked or consent does not hold |
| 9 | `verify.py --refetch`: evidence intact, but the live post no longer hashes to what was attested |

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

## Running offline

The whole pipeline runs with the network unplugged, once the cache is warm:

```bash
py run.py --image me.jpg --image-url "https://..." --chain local   # warm the cache
py run.py --image me.jpg --chain local --offline                   # no network at all
```

Offline reproduces the online run exactly - same 33 candidates, 12/12 cache hits, same
8/12 accepted, same best match at 0.9738 - verified with all traffic forced through a dead
proxy. The local chain needs no network either, so face, search replay, verification,
commitment and attestation all work on a plane.

**This is for rehearsal, reproducibility and bad venue wifi. It is not a submission mode.**
The task requires "a genuine search step, not a hardcoded/pre-picked result", and a replayed
search is by definition pre-picked. `--offline` forces the `replay` backend, which commits
`provider=replay` into the Merkle leaves, so any bundle produced this way is permanently
self-labelled and cannot be presented as a live run.

Candidate images are cached content-addressed under `evidence/cache/`. Beyond offline use
that means a rehearsal does not hit third-party image hosts five times, and a candidate
image that changes or 404s between runs cannot silently alter the evidence. A cold cache
offline reports `0/12 hits` and exits 2 rather than pretending.

## Viewer (optional)

```bash
py viewer.py        # http://127.0.0.1:8000
```

A local, read-only page over the evidence bundles. Three things it does that terminal
output cannot:

- **The pipeline as three stages**, so the shape of the thing is visible at a glance, with
  the chain stage turning red when an attestation has been withdrawn.
- **Verification as a sequence.** Each check ticks in turn — re-hash fields, rebuild the
  root, look it up on chain, confirm it is not revoked — because any one of them failing is
  a different kind of problem, and a single green tick hides that.
- **The Merkle tree, drawn.** Click any leaf and its path to the root lights up, with the
  siblings a proof would have to supply shown alongside. A root is an abstraction until you
  can see the leaves it was built from and watch one prove itself — which is also the
  clearest way to show why disclosing a single field reveals nothing else.
- **A tamper simulator.** Edit a candidate's URL or score in the page and watch the root
  move away from the one on chain, live. Nothing is written: the endpoint recomputes and
  returns, and the bundle on disk is untouched.
- **Filters and copyable hashes**, because a table of twelve candidates and a 66-character
  root are things you actually want to slice and paste.

Rejected candidates stay visible and dimmed, because "the search returned this and the
pipeline declined it" is the distinction the whole build rests on.

Not part of the pipeline, and the task requires no website. Two constraints it holds to:
it never writes a bundle, sends a transaction, or runs a search; and verification calls
`pom.evidence` and `pom.chain` - the same code `verify.py` uses - so it cannot agree with
itself while disagreeing with the tool that matters. Stdlib only, bound to localhost,
because bundles name the pages a face was matched to.

## Checking it against the task requirements

[VERIFY.md](VERIFY.md) walks each of the six requirements to a command and the output it
should produce, with real captured results rather than illustrative ones.

## Is it working?

One command answers it. Needs `npx hardhat node` running.

```bash
py preflight.py --e2e
```

It checks the environment, then actually runs the pipeline and asserts every outcome -
including the one that matters most, that `verify.py` **rejects** evidence it has just
tampered with. A build where only the happy path passes is not working.

```
end to end
  [pass] deploy                              0x71089Ba4...
  [pass] pipeline runs                       exit 0
  [pass] verify accepts untouched evidence   exit 0
  [pass] verify rejects tampered evidence    exit 6, names candidate[0]
  [pass] on-chain inclusion proof            contract accepts a single leaf
  [pass] guardrail: no face                  exit 4, nothing written
```

Exit 0 means every required check passed. Warnings never fail it.

## Tests

```bash
py -m pytest                 # 201 tests
py preflight.py              # setup only, no pipeline run
py preflight.py --recording  # stricter, before a take that cannot be redone
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

**Cross-photo matching is verified, but on a small set.** `scripts/crosscheck.py` shows
identity surviving a 43-year age gap with a 0.396 margin, over six same-person and seven
different-person comparisons. That is enough to show the pipeline recognises faces rather
than duplicate files; it is not a benchmark.

**A larger face model would match harder cases.** SFace is 128-d and lightweight. An
ArcFace model (insightface `buffalo_l`, 512-d) is measurably stronger when the two photos
differ in pose, lighting or age. It is not used here because the task's input is a photo
the subject posted — so the match is near-duplicate — and switching would mean 300 MB of
weights and re-deriving the threshold. If this were a product rather than a submission,
that is the first upgrade.

**Restarting the local chain invalidates existing bundles.** Hardhat wipes state on
restart, and because it deploys deterministically a redeploy lands at the *same* address -
so a bundle written before the restart points at a live contract that simply has no
records. `verify.py` distinguishes this from tampering by checking whether the contract
holds any attestations at all, and says so rather than reporting altered evidence. Re-run
the pipeline after restarting the node.

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

**Face ID thresholds are provisional.** 0.66 and 0.40 were measured on 12 images of 8
people, where the same-person pairs are one identity's photographs and the other seven
contribute a single photo each. That set shows a clean 0.51 separation, but it **cannot
support a false-match rate** and it is not diverse in age, lighting, pose or demographics.
Re-measure on your own data before relying on these numbers.

**Face ID false-positive and false-negative risks, concretely.** A false merge (two people
under one Face ID) is the damaging error and more photographs cannot undo it, because the
wrong embedding is now part of the face. Raising `high` makes it rarer at the cost of more
duplicate Face IDs, which are cheap. Expect false *negatives* - a new Face ID for someone
already registered - whenever a photo differs sharply in pose, age, lighting or occlusion
from everything on record; SFace is a 128-d model and this is where a 512-d ArcFace would
help. Identical twins, and near-duplicate crops of one photograph, are both beyond what any
embedding threshold can settle.

**A Face ID is not an identity.** It says two photographs likely show the same face. It
does not establish a name, and nothing in this project verifies real-world identity.

**Consent.** Run this on your own face, or on faces whose owners agreed. It is a
face-to-social-media pipeline writing to an immutable ledger; that is worth being
deliberate about, which is also why only hashes go on chain.

## Layout

```
run.py                  end-to-end pipeline
verify.py               re-verification, re-fetch, tamper detection, disclosure
server.py               optional local web console over run.py and verify.py
server_page.html        its interface
faceids.py              inspect the Face ID registry; check a photo against it
pom/faceid.py           persistent anonymous Face IDs, thresholds, registry
deploy.py               contract deployment
pom/source.py           resolve the input image from a path or an http(s) URL
pom/face.py             YuNet detect -> SFace 128-d embed, scale ladder
pom/search.py           adapter: auto | bing_url | yandex_url | bing_scripted |
                                 serpapi | replay
pom/match.py            re-download, re-embed, cosine vs threshold
pom/refetch.py          fetch the discovered post again and re-hash it
pom/cache.py            content-addressed image cache; enables --offline
pom/evidence.py         bundle assembly, leaf derivation, field-level diff
pom/merkle.py           keccak256 sorted-pair tree, root + inclusion proofs
pom/chain.py            web3: deploy, record, read back, verify inclusion
contracts/AttestationRegistry.sol
scripts/calibrate.py    threshold measurement
scripts/fetch_models.py ONNX weights, SHA-256 verified
consent.py              create a signed consent record
revoke.py               withdraw an attestation
preflight.py            setup and recording sanity checks
viewer.py               optional local read-only evidence viewer
viewer_page.html        its interface
pom/consent.py          consent signing and verification
scripts/calibrate_faceid.py  measure the Face ID thresholds
tests/                  326 tests, tiered by what they require
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

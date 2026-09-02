# Day-1 spike — can scripted reverse image search work with no paid API?

Date: 2026-09-03 · Control image: Wikipedia portrait of Albert Einstein (heavily indexed,
public domain), 1024px. Control chosen so a null result means "the plumbing is broken",
not "this face isn't online."

## Verdict

**Yes — once. It rate-limits into a CAPTCHA under repeated automated use.**

| Engine | Result |
|---|---|
| **Bing Visual Search** | **Worked on first run.** 20,700 results, 27 source page URLs incl. 6 Pinterest, IMDb, Shutterstock, MutualArt. |
| Bing, runs 2–4 | `Verify you are human` challenge. 0 source pages. |
| Google Lens | Uploaded, page loaded, 26 anchors, **0** source pages extracted. Results are rendered client-side; would need more work. |
| Yandex | Timed out waiting for `rpt=imageview` — upload never triggered the search. |
| TinEye | No `input[type=file]`; no file-chooser event fired either. Upload path not found. |

## What Bing returns (run 1, genuine)

Page URLs are embedded as HTML-escaped JSON under `"purl"` (source page) and `"murl"`
(image), not as anchor hrefs — that's why a naive `a[href]` scrape finds nothing.

```
https://www.pinterest.com/pin/300685712620160653/
https://www.pinterest.com/pin/245938829628170961/
https://www.imdb.com/name/nm0251868/mediaviewer/rm3086472961
https://www.ancestry.com/c/ancestry-blog/9-famous-people-who-married-their-cousins
https://www.mutualart.com/Artwork/Albert-Einstein--Princeton/E6BA0B8A48DE373F
...27 total
```

## Constraint accepted

**No CAPTCHA solving or bypass.** Not built, will not be built. When the pipeline hits a
challenge it must stop and say so — a submission that quietly evades bot detection is worse
than one that documents the limit.

## Consequence for the build

The demo recording is a *single* run, so scripted Bing is viable for it — run 1 proved that.
The risk is a judge cloning the repo and hitting a challenge on their first try.

So `pom/search.py` ships as an adapter with two backends:

- `bing_scripted` (default) — free, no key, headed, persistent browser profile, one search
  per run. On a CAPTCHA it raises `SearchBlocked` and the pipeline exits cleanly.
- `serpapi` (fallback) — free tier, 100 searches/month, $0. Needs a key but no payment.
  Gives a judge a deterministic path when the scripted route is challenged.

This goes in the README's known limitations verbatim. It is a real constraint honestly
handled, which reads better than pretending the scripted path is robust.

## Also settled today (bonus, was scheduled for Sep 4)

- OpenCV **5.0.0** ships `FaceDetectorYN` + `FaceRecognizerSF` — no dlib, no compilation.
- Detection **fails silently on large images**: the 4753×3840 original returned 0 faces at
  every threshold (0.9/0.6/0.3). Downscaled to 1024px wide → 1 face, score 0.924, 128-d
  embedding, L2 norm 13.3. **`face.py` must downscale before detect.** This would have been
  an ugly bug to hit live on camera.

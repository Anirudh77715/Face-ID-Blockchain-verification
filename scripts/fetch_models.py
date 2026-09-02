"""Download the two ONNX models the pipeline needs.

    py scripts/fetch_models.py

They are not committed (38 MB of weights do not belong in a git history) so this runs once
after cloning. Both come from OpenCV's own model zoo and are verified by SHA-256 after
download — a silently truncated model would otherwise surface as "no face detected", which
is a miserable thing to debug on camera.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import requests

MODELS = Path(__file__).resolve().parent.parent / "models"
BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"

FILES = {
    "yunet.onnx": (
        f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "sface.onnx": (
        f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)
    failed = False

    for name, (url, want) in FILES.items():
        path = MODELS / name

        if path.exists():
            have = hashlib.sha256(path.read_bytes()).hexdigest()
            if have == want:
                print(f"  ok       {name} ({path.stat().st_size / 1e6:.1f} MB)")
                continue
            print(f"  stale    {name} - re-downloading")

        print(f"  fetching {name} ...")
        try:
            r = requests.get(url, timeout=180)
            r.raise_for_status()
        except Exception as e:
            print(f"  FAILED   {name}: {type(e).__name__}: {e}", file=sys.stderr)
            failed = True
            continue

        have = hashlib.sha256(r.content).hexdigest()
        if have != want:
            print(f"  FAILED   {name}: sha256 mismatch\n"
                  f"    expected {want}\n    got      {have}", file=sys.stderr)
            failed = True
            continue

        path.write_bytes(r.content)
        print(f"  ok       {name} ({len(r.content) / 1e6:.1f} MB)")

    if failed:
        return 1
    print(f"\nmodels ready in {MODELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

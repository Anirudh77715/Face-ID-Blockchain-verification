"""Face detection and embedding — OpenCV YuNet (detect) + SFace (128-d embed).

Chosen over dlib/`face_recognition`, which still needs CMake and MSVC on Windows. These
ship inside `opencv-python` as ONNX and need neither.

insightface would also work - 1.0.1 ships a pure-Python wheel, so the compiler argument
against it is no longer true and was corrected here rather than left to rot. Its ArcFace
weights are 512-d and stronger on cross-pose matches; SFace stays because this task matches
near-duplicates of a photo the subject posted, the measured margin is +0.51, and swapping
would cost 300 MB and a re-calibration for no gain on the case at hand.

One non-obvious behaviour, found the hard way (see spike/FINDINGS.md): YuNet returns
**zero faces on large images**, silently, at any confidence threshold. A 4753x3840
portrait detected nothing at 0.9, 0.6 and 0.3; the same image at 640px wide detected a
face at 0.924. So detection is retried down a scale ladder rather than trusted once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# OpenCV's own SFace reference threshold for "same identity" under cosine distance.
# Kept as the default so the number in the README is a published one, not a hand-tuned
# value chosen after seeing the results.
COSINE_SAME_IDENTITY = 0.363

# Detection is scale-sensitive; try widest first, fall back. See module docstring.
SCALE_LADDER = (1024, 800, 640, 480)

MODELS = Path(__file__).resolve().parent.parent / "models"


class NoFaceFound(Exception):
    """Raised when no face clears the detector at any scale."""


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


# A 12 MB JPEG can decode to hundreds of megabytes. Candidate images come from the open
# web, so cap decoded area rather than trusting the byte cap alone.
MAX_PIXELS = 50_000_000


def imread_bytes(data: bytes) -> np.ndarray:
    """Decode from memory. Avoids cv2.imread, which fails on non-ASCII Windows paths."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a decodable image")
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise ValueError(f"image too large to process: {img.shape[1]}x{img.shape[0]}")
    return img


@dataclass(frozen=True)
class FaceScan:
    """Everything about one detected face that the evidence bundle needs."""

    bbox: tuple[int, int, int, int]  # x, y, w, h in ORIGINAL image coordinates
    score: float
    embedding: np.ndarray = field(repr=False)
    embedding_sha256: str
    image_sha256: str
    faces_found: int
    detect_scale: int
    detector_sha256: str
    recognizer_sha256: str

    @property
    def provenance(self) -> dict:
        """Model identity goes into the bundle — an embedding is meaningless without
        knowing which weights produced it."""
        return {
            "detector": "yunet_2023mar",
            "detector_sha256": self.detector_sha256,
            "recognizer": "sface_2021dec",
            "recognizer_sha256": self.recognizer_sha256,
            "embedding_dim": int(self.embedding.shape[-1]),
        }


class FaceEncoder:
    def __init__(self, models: Path = MODELS):
        self.yunet_path = models / "yunet.onnx"
        self.sface_path = models / "sface.onnx"
        for p in (self.yunet_path, self.sface_path):
            if not p.exists():
                raise FileNotFoundError(f"missing model: {p}  (run scripts/fetch_models.py)")
        self._det_sha = sha256_file(self.yunet_path)
        self._rec_sha = sha256_file(self.sface_path)
        self._rec = cv2.FaceRecognizerSF.create(str(self.sface_path), "")
        # Built once and reused. Constructing a detector per scale attempt means up to
        # four ONNX loads per image, which exhausts OpenBLAS's arena part-way through a
        # batch of candidates ("Memory allocation still failed after 10 retries").
        self._det = cv2.FaceDetectorYN.create(
            str(self.yunet_path), "", (320, 320), 0.9, 0.3, 5000
        )

    # -------------------------------------------------------------- detection

    def _detect_at(self, img: np.ndarray, conf: float):
        self._det.setScoreThreshold(conf)
        self._det.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self._det.detect(img)
        return faces

    def scan(self, data: bytes, conf: float = 0.9) -> FaceScan:
        """Detect the highest-confidence face and embed it."""
        original = imread_bytes(data)
        h0, w0 = original.shape[:2]

        for target_w in SCALE_LADDER:
            if w0 <= target_w:
                img, scale = original, 1.0
            else:
                scale = target_w / w0
                img = cv2.resize(original, (target_w, int(round(h0 * scale))),
                                 interpolation=cv2.INTER_AREA)

            faces = self._detect_at(img, conf)
            if faces is None or len(faces) == 0:
                continue

            best = max(faces, key=lambda f: float(f[-1]))
            embedding = self._rec.feature(self._rec.alignCrop(img, best))
            x, y, w, h = (float(v) for v in best[:4])

            return FaceScan(
                bbox=(int(x / scale), int(y / scale), int(w / scale), int(h / scale)),
                score=float(best[-1]),
                embedding=embedding,
                embedding_sha256=sha256_bytes(embedding.tobytes()),
                image_sha256=sha256_bytes(data),
                faces_found=len(faces),
                detect_scale=img.shape[1],
                detector_sha256=self._det_sha,
                recognizer_sha256=self._rec_sha,
            )

        raise NoFaceFound(
            f"no face at confidence {conf} across widths {SCALE_LADDER} "
            f"(source {w0}x{h0})"
        )

    def scan_path(self, path: Path, conf: float = 0.9) -> FaceScan:
        return self.scan(Path(path).read_bytes(), conf=conf)

    # ------------------------------------------------------------- comparison

    def annotate(self, data: bytes, scan: FaceScan, max_width: int = 640) -> bytes:
        """Draw the detected box on a copy of the image and return it as PNG bytes.

        A pipeline that reports `bbox (383, 266, 326, 479)` has proved detection to
        itself. Showing the box proves it to whoever is watching, and makes a
        mis-detection obvious instead of a plausible-looking number.

        Returns a copy - the source image is never modified.
        """
        img = imread_bytes(data)
        x, y, w, h = scan.bbox

        colour = (127, 208, 53)  # BGR: the same green the interface uses for accepted
        thickness = max(2, int(round(min(img.shape[:2]) / 300)))
        cv2.rectangle(img, (x, y), (x + w, y + h), colour, thickness)

        # A corner bracket reads as a detection overlay rather than a plain box.
        arm = max(8, int(min(w, h) * 0.18))
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1),
                               (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            cv2.line(img, (cx, cy), (cx + dx * arm, cy), colour, thickness * 2)
            cv2.line(img, (cx, cy), (cx, cy + dy * arm), colour, thickness * 2)

        label = f"{scan.score:.3f}"
        scale = max(0.45, min(w, h) / 320)
        cv2.putText(img, label, (x, max(18, y - 9)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, colour, max(1, thickness - 1), cv2.LINE_AA)

        if img.shape[1] > max_width:
            k = max_width / img.shape[1]
            img = cv2.resize(img, (max_width, int(round(img.shape[0] * k))),
                             interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise ValueError("could not encode the annotated image")
        return buf.tobytes()

    def cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity in SFace's space. Higher is more alike; >= 0.363 is
        OpenCV's published same-identity threshold."""
        return float(self._rec.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))

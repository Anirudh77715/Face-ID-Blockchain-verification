"""Face detection and embedding.

The regression that matters here is the silent one: YuNet returns zero faces on a large
image without raising, at any confidence. `test_detects_in_the_full_size_original` is the
guard against that ever coming back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pom.face import (
    COSINE_SAME_IDENTITY,
    MAX_PIXELS,
    SCALE_LADDER,
    FaceEncoder,
    NoFaceFound,
    imread_bytes,
    sha256_bytes,
)

pytestmark = pytest.mark.needs_models

ROOT = Path(__file__).resolve().parent.parent
FULL_SIZE = ROOT / "spike" / "control.jpg"


def blank(width=400, height=300) -> bytes:
    import cv2
    img = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    assert ok
    return buf.tobytes()


def test_detects_a_face(encoder, control_bytes):
    scan = encoder.scan(control_bytes)
    assert scan.score > 0.9
    assert scan.faces_found >= 1
    assert scan.embedding.shape[-1] == 128


@pytest.mark.skipif(not FULL_SIZE.exists(), reason="full-size control missing")
def test_detects_in_the_full_size_original(encoder):
    """4753x3840 detects nothing at any threshold without the scale ladder."""
    scan = encoder.scan(FULL_SIZE.read_bytes())
    assert scan.score > 0.85
    assert scan.detect_scale in SCALE_LADDER


@pytest.mark.skipif(not FULL_SIZE.exists(), reason="full-size control missing")
def test_bbox_is_reported_in_source_coordinates(encoder):
    data = FULL_SIZE.read_bytes()
    original = imread_bytes(data)
    h, w = original.shape[:2]
    x, y, bw, bh = encoder.scan(data).bbox
    assert 0 <= x < w and 0 <= y < h
    assert x + bw <= w * 1.02 and y + bh <= h * 1.02
    # A face detected at 1024px but reported unscaled would sit in the top-left corner.
    assert bw > w * 0.05, "bbox looks like it was left in detector coordinates"


def test_no_face_raises(encoder):
    with pytest.raises(NoFaceFound):
        encoder.scan(blank())


def test_no_face_message_names_the_scales_tried(encoder):
    with pytest.raises(NoFaceFound) as e:
        encoder.scan(blank())
    assert str(SCALE_LADDER[0]) in str(e.value)


def test_undecodable_bytes_raise(encoder):
    with pytest.raises(ValueError):
        encoder.scan(b"this is not an image")


def test_oversized_images_are_rejected():
    import cv2
    side = int(MAX_PIXELS ** 0.5) + 2000
    img = np.zeros((side, side, 3), np.uint8)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        pytest.skip("could not encode an oversized test image")
    with pytest.raises(ValueError, match="too large"):
        imread_bytes(buf.tobytes())


def test_embedding_is_deterministic(encoder, control_bytes):
    a = encoder.scan(control_bytes)
    b = encoder.scan(control_bytes)
    assert a.embedding_sha256 == b.embedding_sha256
    assert a.bbox == b.bbox


def test_identical_embeddings_are_maximally_similar(encoder, control_bytes):
    e = encoder.scan(control_bytes).embedding
    assert encoder.cosine(e, e) == pytest.approx(1.0, abs=1e-4)


def test_image_hash_is_of_the_source_bytes(encoder, control_bytes):
    assert encoder.scan(control_bytes).image_sha256 == sha256_bytes(control_bytes)


def test_provenance_pins_the_models(encoder, control_bytes):
    p = encoder.scan(control_bytes).provenance
    assert p["detector"] == "yunet_2023mar"
    assert p["recognizer"] == "sface_2021dec"
    assert p["embedding_dim"] == 128
    assert len(p["detector_sha256"]) == 64
    assert len(p["recognizer_sha256"]) == 64


def test_resizing_does_not_destroy_identity(encoder, control_bytes):
    """A rescaled copy of the same photo must stay well above threshold, or the
    verification step would reject genuine matches served at another size."""
    import cv2
    img = imread_bytes(control_bytes)
    small = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))
    ok, buf = cv2.imencode(".jpg", small)
    assert ok
    a = encoder.scan(control_bytes).embedding
    b = encoder.scan(buf.tobytes()).embedding
    assert encoder.cosine(a, b) > COSINE_SAME_IDENTITY + 0.3


def test_jpeg_recompression_does_not_destroy_identity(encoder, control_bytes):
    import cv2
    img = imread_bytes(control_bytes)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 40])
    assert ok
    a = encoder.scan(control_bytes).embedding
    b = encoder.scan(buf.tobytes()).embedding
    assert encoder.cosine(a, b) > COSINE_SAME_IDENTITY + 0.3


def test_missing_models_fail_loudly(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_models"):
        FaceEncoder(models=tmp_path)

"""Shared fixtures.

Tests are split by what they need:

  unit         no network, no chain, no models - always run
  needs_models requires scripts/fetch_models.py to have been run
  needs_chain  requires a node on 127.0.0.1:8545
  needs_net    reaches the open internet

CI runs the unit tier plus models; the chain tier runs whenever a node is up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "spike" / "reference-capture.raw"
CONTROL = ROOT / "spike" / "control_small.jpg"
MODELS = ROOT / "models"


def pytest_configure(config):
    for marker in ("needs_models", "needs_chain", "needs_net"):
        config.addinivalue_line("markers", f"{marker}: see tests/conftest.py")


def _models_present() -> bool:
    return (MODELS / "yunet.onnx").exists() and (MODELS / "sface.onnx").exists()


def _chain_up() -> bool:
    try:
        from web3 import Web3
        return Web3(Web3.HTTPProvider("http://127.0.0.1:8545",
                                      request_kwargs={"timeout": 3})).is_connected()
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    no_models = pytest.mark.skip(reason="run scripts/fetch_models.py first")
    no_chain = pytest.mark.skip(reason="no node on 127.0.0.1:8545")
    models_ok, chain_ok = _models_present(), _chain_up()
    for item in items:
        if "needs_models" in item.keywords and not models_ok:
            item.add_marker(no_models)
        if "needs_chain" in item.keywords and not chain_ok:
            item.add_marker(no_chain)


@pytest.fixture(scope="session")
def reference_html() -> str:
    if not REFERENCE.exists():
        pytest.skip("reference capture missing")
    return REFERENCE.read_bytes().decode("utf-8", errors="replace")


@pytest.fixture(scope="session")
def control_bytes() -> bytes:
    if not CONTROL.exists():
        pytest.skip("control image missing")
    return CONTROL.read_bytes()


@pytest.fixture(scope="session")
def encoder():
    from pom.face import FaceEncoder
    return FaceEncoder()


@pytest.fixture
def bundle(tmp_path) -> dict:
    """A complete, finalized bundle built from fixed values - no models, no network."""
    from pom import evidence

    b = {
        "version": 1,
        "run_id": "0" * 32,
        "created_at": "2026-09-03T00:00:00Z",
        "query": {
            "source_name": "fixture.jpg",
            "image_sha256": "a" * 64,
            "embedding_sha256": "b" * 64,
            "bbox": [10, 20, 30, 40],
            "detect_score": 0.95,
            "faces_found": 1,
            "detect_scale": 1024,
        },
        "models": {"detector": "yunet_2023mar", "detector_sha256": "c" * 64,
                   "recognizer": "sface_2021dec", "recognizer_sha256": "d" * 64,
                   "embedding_dim": 128},
        "search": {"provider": "replay", "queried_at": "2026-09-03T00:00:00Z",
                   "raw_sha256": "e" * 64, "raw_path": "x.raw",
                   "candidates_returned": 2},
        "threshold": 0.363,
        "candidates": [
            {"page_url": "https://x.com/a/1", "image_url": "https://i/1.jpg",
             "social": True, "status": "ok", "similarity": 0.91, "accepted": True,
             "image_sha256": "1" * 64, "note": ""},
            {"page_url": "https://example.com/b", "image_url": "https://i/2.jpg",
             "social": False, "status": "no_face", "similarity": None,
             "accepted": False, "image_sha256": "2" * 64, "note": ""},
        ],
        "decision": {"matched": True, "best_page_url": "https://x.com/a/1",
                     "best_similarity": 0.91, "accepted_count": 1},
    }
    return evidence.finalize(b)

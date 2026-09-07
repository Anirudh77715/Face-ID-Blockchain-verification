"""Evidence bundle: leaf derivation, commitment, and tamper localisation.

These run without models, network or a chain — the bundle fixture is fixed data — so they
are the tier that must always pass.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pom import evidence


def test_finalize_attaches_a_commitment(bundle):
    assert bundle["merkle"]["root"].startswith("0x")
    assert len(bundle["merkle"]["root"]) == 66
    assert bundle["merkle"]["leaf_count"] == len(bundle["merkle"]["leaves"])


def test_leaf_count_covers_every_candidate(bundle):
    names = [n for n, _ in evidence.leaves(bundle)]
    for i in range(len(bundle["candidates"])):
        assert f"candidate[{i}]" in names
    assert "decision" in names
    assert "threshold" in names
    assert "search.raw_sha256" in names


def test_untouched_bundle_verifies(bundle):
    assert evidence.diff_against_stored(bundle) == []
    assert "0x" + evidence.root(bundle).hex() == bundle["merkle"]["root"]


def test_root_is_recomputed_from_content_not_stored_leaves(bundle):
    """The stored leaf list must never be trusted as input, or forging it would forge
    the verification."""
    tampered = copy.deepcopy(bundle)
    tampered["query"]["image_sha256"] = "f" * 64
    # Rewrite the stored leaves to be self-consistent with the forged content.
    tampered = evidence.finalize({k: v for k, v in tampered.items() if k != "merkle"})
    # Self-consistent, so field-level diff is clean...
    assert evidence.diff_against_stored(tampered) == []
    # ...but the root is now different from the original commitment, which is what the
    # chain lookup catches.
    assert tampered["merkle"]["root"] != bundle["merkle"]["root"]


@pytest.mark.parametrize("path,value", [
    (("query", "image_sha256"), "f" * 64),
    (("query", "embedding_sha256"), "f" * 64),
    (("query", "bbox"), [1, 2, 3, 4]),
    (("query", "detect_score"), 0.1),
    (("search", "provider"), "somethingelse"),
    (("search", "raw_sha256"), "f" * 64),
    (("threshold"), 0.9),
])
def test_editing_any_committed_field_is_detected(bundle, path, value):
    t = copy.deepcopy(bundle)
    if isinstance(path, tuple):
        t[path[0]][path[1]] = value
    else:
        t[path] = value
    diverged = evidence.diff_against_stored(t)
    assert diverged, f"editing {path} went undetected"
    assert "0x" + evidence.root(t).hex() != bundle["merkle"]["root"]


def test_tamper_is_localised_to_the_field(bundle):
    t = copy.deepcopy(bundle)
    t["candidates"][1]["page_url"] = "https://evil.example/"
    assert evidence.diff_against_stored(t) == ["candidate[1]"]


def test_editing_similarity_is_detected(bundle):
    t = copy.deepcopy(bundle)
    t["candidates"][0]["similarity"] = 0.99
    assert evidence.diff_against_stored(t) == ["candidate[0]"]


def test_flipping_accepted_is_detected(bundle):
    t = copy.deepcopy(bundle)
    t["candidates"][1]["accepted"] = True
    assert evidence.diff_against_stored(t) == ["candidate[1]"]


def test_swapping_candidates_changes_the_root(bundle):
    """The case that can actually arise from editing a bundle: leaves are named by index,
    so exchanging two candidates renames both and changes the commitment."""
    t = copy.deepcopy(bundle)
    t["candidates"][0], t["candidates"][1] = t["candidates"][1], t["candidates"][0]
    assert "0x" + evidence.root(t).hex() != bundle["merkle"]["root"]
    assert set(evidence.diff_against_stored(t)) == {"candidate[0]", "candidate[1]"}


def test_appending_a_candidate_is_detected(bundle):
    t = copy.deepcopy(bundle)
    t["candidates"].append({
        "page_url": "https://x.com/added", "image_url": "", "social": True,
        "status": "ok", "similarity": 0.99, "accepted": True,
        "image_sha256": "9" * 64, "note": ""})
    diverged = evidence.diff_against_stored(t)
    assert any("candidate[2]" in d and "added" in d for d in diverged)


def test_removing_a_candidate_is_detected(bundle):
    t = copy.deepcopy(bundle)
    t["candidates"].pop()
    diverged = evidence.diff_against_stored(t)
    assert any("removed" in d for d in diverged)


def test_uncommitted_metadata_does_not_affect_the_root(bundle):
    """run_id and created_at are deliberately not leaves; changing them must not
    invalidate a commitment, or every bundle would be unverifiable after a copy."""
    t = copy.deepcopy(bundle)
    t["run_id"] = "9" * 32
    t["created_at"] = "2099-01-01T00:00:00Z"
    assert evidence.diff_against_stored(t) == []
    assert "0x" + evidence.root(t).hex() == bundle["merkle"]["root"]


def test_bundle_round_trips_through_json(bundle, tmp_path):
    path = evidence.save(bundle, tmp_path)
    reloaded = evidence.load(path)
    assert reloaded == bundle
    assert evidence.diff_against_stored(reloaded) == []


def test_saved_bundle_is_valid_json(bundle, tmp_path):
    path = evidence.save(bundle, tmp_path)
    json.loads(path.read_text(encoding="utf-8"))


def test_non_ascii_survives_the_commitment(bundle):
    """Result titles and URLs carry non-ASCII; canonical encoding must be stable for it."""
    t = copy.deepcopy(bundle)
    t["candidates"][0]["page_url"] = "https://example.com/ünïcodé-Ω-日本語"
    finalized = evidence.finalize({k: v for k, v in t.items() if k != "merkle"})
    assert evidence.diff_against_stored(finalized) == []
    assert evidence.root(finalized) == evidence.root(evidence.load(
        evidence.save(finalized, __import__("pathlib").Path(
            __import__("tempfile").mkdtemp()))))


# ------------------------------------------------- recognising a re-submitted photo

def _bundle_file(directory, name, image_sha, root, created, attested=False):
    """A minimal bundle on disk, only the fields the lookups read."""
    body = {
        "created_at": created,
        "query": {"source_name": name, "image_sha256": image_sha},
        "merkle": {"root": root},
        "attestation": {"written": attested},
    }
    path = directory / f"run-{Path(name).stem}-{root[2:10]}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_same_image_runs_matches_on_bytes_not_filename(tmp_path):
    """The point of the feature: a photo re-uploaded under a new name is still found."""
    _bundle_file(tmp_path, "control_small.jpg", "aaa", "0x" + "1" * 64,
                 "2026-01-01T00:00:00Z")
    _bundle_file(tmp_path, "holiday-photo.jpg", "aaa", "0x" + "2" * 64,
                 "2026-01-02T00:00:00Z")
    _bundle_file(tmp_path, "someone-else.jpg", "bbb", "0x" + "3" * 64,
                 "2026-01-03T00:00:00Z")

    found = evidence.same_image_runs("aaa", directory=tmp_path)
    assert [r["source_name"] for r in found] == ["control_small.jpg", "holiday-photo.jpg"]


def test_same_image_runs_is_ordered_oldest_first(tmp_path):
    _bundle_file(tmp_path, "b.jpg", "aaa", "0x" + "2" * 64, "2026-01-02T00:00:00Z")
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "1" * 64, "2026-01-01T00:00:00Z")
    found = evidence.same_image_runs("aaa", directory=tmp_path)
    assert [r["created_at"] for r in found] == [
        "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]


def test_same_image_runs_can_exclude_the_bundle_being_checked(tmp_path):
    mine = _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "1" * 64,
                        "2026-01-01T00:00:00Z")
    _bundle_file(tmp_path, "b.jpg", "aaa", "0x" + "2" * 64, "2026-01-02T00:00:00Z")
    found = evidence.same_image_runs("aaa", directory=tmp_path, exclude=mine)
    assert [r["source_name"] for r in found] == ["b.jpg"]


def test_same_image_runs_reports_whether_each_was_attested(tmp_path):
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "1" * 64, "2026-01-01T00:00:00Z",
                 attested=True)
    _bundle_file(tmp_path, "b.jpg", "aaa", "0x" + "2" * 64, "2026-01-02T00:00:00Z")
    found = evidence.same_image_runs("aaa", directory=tmp_path)
    assert [r["attested"] for r in found] == [True, False]


def test_a_live_rerun_of_the_same_photo_does_not_share_a_root(tmp_path):
    """Pins the reason the lookup keys on the image hash rather than the root.

    search.queried_at and search.raw_sha256 are leaves, so a second search commits to
    something different even though the input image is identical.
    """
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "1" * 64, "2026-01-01T00:00:00Z")
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "2" * 64, "2026-01-02T00:00:00Z")

    assert len(evidence.same_image_runs("aaa", directory=tmp_path)) == 2
    assert len(evidence.prior_runs("0x" + "1" * 64, directory=tmp_path)) == 1


def test_prior_runs_finds_bundles_sharing_a_root(tmp_path):
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "9" * 64, "2026-01-01T00:00:00Z")
    _bundle_file(tmp_path, "b.jpg", "aaa", "0x" + "9" * 64, "2026-01-02T00:00:00Z")
    found = evidence.prior_runs("0x" + "9" * 64, directory=tmp_path)
    assert [r["source_name"] for r in found] == ["a.jpg", "b.jpg"]


def test_a_corrupt_bundle_does_not_break_the_lookup(tmp_path):
    """A hand-edited bundle sitting in evidence/ must not take down an unrelated run."""
    _bundle_file(tmp_path, "a.jpg", "aaa", "0x" + "1" * 64, "2026-01-01T00:00:00Z")
    (tmp_path / "run-broken.json").write_text("{not json", encoding="utf-8")

    found = evidence.same_image_runs("aaa", directory=tmp_path)
    assert [r["source_name"] for r in found] == ["a.jpg"]

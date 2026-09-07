"""Persistent Face ID: registration, matching, persistence, and what it must not do.

The unit tier uses a stub similarity so it runs with no models. The `needs_models` tier
uses the real encoder over `bench/faces`, because "a different photo of the same person
matches, a different person does not" is the claim the feature actually makes, and only
real embeddings can test it.
"""

from __future__ import annotations

import json

import pytest

from pom import faceid as F

BENCH = None  # resolved lazily in the model-backed tests


# --------------------------------------------------------------------- helpers

def vec(*values) -> list[float]:
    return list(values)


def cosine_stub(a, b):
    """Similarity by construction: each embedding's first element is its identity, and
    matching is exact on it. Keeps threshold logic testable without models."""
    import numpy as np

    a1 = float(np.asarray(a).flatten()[0])
    b1 = float(np.asarray(b).flatten()[0])
    return 1.0 if a1 == b1 else 0.1


def registry(tmp_path, high=0.66, review=0.40) -> F.FaceRegistry:
    return F.FaceRegistry(path=tmp_path / "faceids.json",
                          thresholds=F.Thresholds(high=high, review=review))


# ------------------------------------------------------------------ new faces

def test_first_face_creates_f001(tmp_path):
    reg = registry(tmp_path)
    d = reg.observe(vec(1.0, 0.0), "img-a", cosine_stub)
    assert d.face_id == "F-001"
    assert d.created is True
    assert d.photo_count == 1


def test_a_different_person_gets_a_new_face_id(tmp_path):
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "img-a", cosine_stub)
    d = reg.observe(vec(2.0), "img-b", cosine_stub)
    assert d.face_id == "F-002"
    assert d.created is True
    assert d.status == F.NO_MATCH
    assert len(reg.faces) == 2


def test_ids_increment_and_do_not_reuse_a_deleted_number(tmp_path):
    """A retired label must never be handed to a different face: an evidence bundle
    citing F-002 would silently come to mean someone else."""
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "a", cosine_stub)
    reg.observe(vec(2.0), "b", cosine_stub)
    reg.faces = [f for f in reg.faces if f.face_id != "F-002"]
    reg.save()
    assert reg.next_id() == "F-003"


# --------------------------------------------------------------- same person

def test_same_face_different_photo_matches_and_does_not_duplicate(tmp_path):
    """The core claim: new image hash, new embedding, same Face ID."""
    reg = registry(tmp_path)
    first = reg.observe(vec(1.0), "image-hash-ABC123", cosine_stub)

    second = reg.observe(vec(1.0), "image-hash-XYZ789", cosine_stub)

    assert second.face_id == first.face_id == "F-001"
    assert second.created is False
    assert second.status == F.MATCH
    assert len(reg.faces) == 1, "a matching photo must not create a second Face ID"


def test_a_matching_photo_is_appended_as_another_sighting(tmp_path):
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "hash-a", cosine_stub)
    d = reg.observe(vec(1.0), "hash-b", cosine_stub)
    assert d.photo_count == 2
    assert [s.image_sha256 for s in reg.get("F-001").sightings] == ["hash-a", "hash-b"]


def test_the_image_hash_changes_while_the_face_id_does_not(tmp_path):
    reg = registry(tmp_path)
    a = reg.observe(vec(1.0), "ABC123", cosine_stub)
    b = reg.observe(vec(1.0), "XYZ789", cosine_stub)
    hashes = {s.image_sha256 for s in reg.get("F-001").sightings}
    assert hashes == {"ABC123", "XYZ789"}
    assert a.face_id == b.face_id


def test_rescanning_the_identical_file_adds_no_sighting(tmp_path):
    """Duplicate prevention: the same bytes carry no new information."""
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "same-hash", cosine_stub)
    d = reg.observe(vec(1.0), "same-hash", cosine_stub)
    assert d.photo_count == 1
    assert len(reg.get("F-001").sightings) == 1


# ------------------------------------------------------------------ thresholds

@pytest.mark.parametrize("score,expected", [
    (0.95, F.MATCH), (0.66, F.MATCH),
    (0.65, F.REVIEW), (0.40, F.REVIEW),
    (0.39, F.NO_MATCH), (0.0, F.NO_MATCH),
])
def test_the_three_states_have_the_documented_boundaries(score, expected):
    assert F.Thresholds(high=0.66, review=0.40).classify(score) == expected


def test_a_review_score_does_not_merge_two_identities(tmp_path):
    """Below `high` a new Face ID is created: an extra identifier is a far cheaper
    mistake than a wrong merge, which more photographs cannot undo."""
    reg = registry(tmp_path, high=0.9, review=0.05)
    reg.observe(vec(1.0), "a", cosine_stub)
    d = reg.observe(vec(2.0), "b", cosine_stub)  # stub scores 0.1 -> REVIEW

    assert d.status == F.REVIEW
    assert d.created is True
    assert d.face_id == "F-002"
    assert len(reg.faces) == 2


def test_thresholds_come_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("POM_FACEID_HIGH", "0.8")
    monkeypatch.setenv("POM_FACEID_REVIEW", "0.5")
    t = F.Thresholds.from_env()
    assert (t.high, t.review) == (0.8, 0.5)


def test_an_explicit_threshold_beats_the_environment(monkeypatch):
    monkeypatch.setenv("POM_FACEID_HIGH", "0.8")
    assert F.Thresholds.from_env(high=0.7).high == 0.7


def test_incoherent_thresholds_are_refused():
    with pytest.raises(F.FaceIdError):
        F.Thresholds(high=0.3, review=0.9)


def test_the_faceid_threshold_is_independent_of_the_candidate_threshold():
    """Guards the distinction: the web-candidate threshold must never be reused here."""
    from pom.face import COSINE_SAME_IDENTITY

    assert F.DEFAULT_HIGH_CONFIDENCE_THRESHOLD > COSINE_SAME_IDENTITY


# ----------------------------------------------------------------- persistence

def test_the_registry_survives_a_restart(tmp_path):
    path = tmp_path / "faceids.json"
    first = F.FaceRegistry(path=path)
    first.observe(vec(1.0), "hash-a", cosine_stub)

    reopened = F.FaceRegistry(path=path)  # a fresh process would do exactly this
    assert [f.face_id for f in reopened.faces] == ["F-001"]
    assert reopened.get("F-001").sightings[0].image_sha256 == "hash-a"

    d = reopened.observe(vec(1.0), "hash-b", cosine_stub)
    assert d.face_id == "F-001" and d.created is False


def test_a_corrupt_registry_is_reported_not_silently_emptied(tmp_path):
    path = tmp_path / "faceids.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(F.FaceIdError):
        F.FaceRegistry(path=path)


def test_an_absent_registry_starts_empty(tmp_path):
    assert F.FaceRegistry(path=tmp_path / "nothing.json").faces == []


def test_attestation_reference_is_attached_to_the_right_sighting(tmp_path):
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "hash-a", cosine_stub)
    reg.observe(vec(1.0), "hash-b", cosine_stub)

    assert reg.attach_attestation("F-001", "hash-b", {"tx_hash": "0xabc"})
    sightings = reg.get("F-001").sightings
    assert sightings[0].attestation is None
    assert sightings[1].attestation == {"tx_hash": "0xabc"}


def test_no_raw_image_is_stored_in_the_registry(tmp_path):
    """Biometric vectors stay local; image bytes are never copied into the registry."""
    reg = registry(tmp_path)
    reg.observe(vec(1.0, 2.0), "hash-a", cosine_stub)
    body = json.loads((tmp_path / "faceids.json").read_text(encoding="utf-8"))
    stored = body["faces"][0]["sightings"][0]
    assert set(stored) == {"image_sha256", "embedding", "added_at",
                           "source_name", "attestation"}


# ------------------------------------------------------------------ aggregation

def test_a_face_is_scored_by_its_best_sighting_not_its_average(tmp_path):
    """Documented aggregation: maximum. A well-photographed face must not become
    harder to recognise with every extra sighting."""
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "a", cosine_stub)
    reg.get("F-001").sightings.append(
        F.Sighting(image_sha256="b", embedding=vec(9.0), added_at="now"))

    ranked = reg.score(vec(1.0), cosine_stub)
    assert ranked == [("F-001", 1.0)]  # the mean would be 0.55


# ------------------------------------------------------- real embeddings

@pytest.mark.needs_models
def test_real_faces_same_person_matches_different_person_does_not(tmp_path):
    """The claim, on real embeddings: Einstein matches Einstein across photographs,
    and seven other people do not."""
    from pathlib import Path

    from pom.face import FaceEncoder

    faces = Path(__file__).resolve().parent.parent / "bench" / "faces"
    if not (faces / "einstein-0.jpg").exists():
        pytest.skip("bench/faces is not present")

    encoder = FaceEncoder()
    reg = registry(tmp_path)

    first = encoder.scan_path(faces / "einstein-0.jpg")
    created = reg.observe(first.embedding, first.image_sha256, encoder.cosine)
    assert created.created is True and created.face_id == "F-001"

    other = encoder.scan_path(faces / "einstein-x1.jpg")
    assert other.image_sha256 != first.image_sha256, "different file, different hash"
    same = reg.observe(other.embedding, other.image_sha256, encoder.cosine)
    assert same.status == F.MATCH
    assert same.face_id == "F-001"
    assert same.created is False
    assert len(reg.faces) == 1

    for name in ("curie-0.jpg", "tesla-0.jpg", "turing-0.jpg"):
        path = faces / name
        if not path.exists():
            continue
        scan = encoder.scan_path(path)
        decision = reg.observe(scan.embedding, scan.image_sha256, encoder.cosine)
        assert decision.created is True, f"{name} was wrongly merged into an existing id"
        assert decision.status != F.MATCH


# ---------------------------------------------------- repairing a split identity

def test_merge_folds_every_photograph_into_the_kept_face(tmp_path):
    """A threshold set too high splits one person across several ids. Lowering it
    afterwards does not repair a registry that already recorded the mistake."""
    reg = registry(tmp_path, high=0.9)          # too high: they will not be merged
    reg.observe(vec(1.0), "hash-a", cosine_stub)
    reg.observe(vec(2.0), "hash-b", cosine_stub)
    assert [f.face_id for f in reg.faces] == ["F-001", "F-002"]

    merged = reg.merge("F-001", "F-002")

    assert [f.face_id for f in reg.faces] == ["F-001"]
    assert merged.photo_count == 2
    assert {s.image_sha256 for s in merged.sightings} == {"hash-a", "hash-b"}


def test_merge_does_not_duplicate_a_shared_photograph(tmp_path):
    reg = registry(tmp_path, high=0.9)
    reg.observe(vec(1.0), "shared", cosine_stub)
    reg.observe(vec(2.0), "other", cosine_stub)
    reg.get("F-002").sightings.append(
        F.Sighting(image_sha256="shared", embedding=vec(1.0), added_at="now"))

    merged = reg.merge("F-001", "F-002")
    assert [s.image_sha256 for s in merged.sightings].count("shared") == 1


def test_a_merged_away_id_is_never_issued_again(tmp_path):
    """An evidence bundle citing F-002 must not silently come to mean another face."""
    reg = registry(tmp_path, high=0.9)
    reg.observe(vec(1.0), "a", cosine_stub)
    reg.observe(vec(2.0), "b", cosine_stub)
    reg.merge("F-001", "F-002")

    assert reg.next_id() == "F-003"
    d = reg.observe(vec(3.0), "c", cosine_stub)
    assert d.face_id == "F-003"


def test_the_high_water_mark_survives_a_restart(tmp_path):
    path = tmp_path / "faceids.json"
    reg = F.FaceRegistry(path=path, thresholds=F.Thresholds(high=0.9, review=0.05))
    reg.observe(vec(1.0), "a", cosine_stub)
    reg.observe(vec(2.0), "b", cosine_stub)
    reg.merge("F-001", "F-002")

    reopened = F.FaceRegistry(path=path)
    assert reopened.next_id() == "F-003"


def test_merging_an_unknown_or_identical_id_is_refused(tmp_path):
    reg = registry(tmp_path)
    reg.observe(vec(1.0), "a", cosine_stub)
    for keep, absorb in (("F-001", "F-404"), ("F-404", "F-001"), ("F-001", "F-001")):
        with pytest.raises(F.FaceIdError):
            reg.merge(keep, absorb)


# ------------------------------------------------ the defaults, after recalibration

def test_the_default_thresholds_admit_a_real_world_same_person_score():
    """0.66 split three photographs of one person that scored 0.6191-0.6417.

    The bench set is studio portraiture and scores 0.79+, which is why calibrating on
    it alone produced a threshold that failed on ordinary photographs.
    """
    t = F.Thresholds()
    assert t.classify(0.6191) == F.MATCH
    assert t.classify(0.6417) == F.MATCH


def test_the_default_thresholds_still_reject_the_worst_different_person_pair():
    """The highest different-person score observed on real photographs was 0.3326."""
    t = F.Thresholds()
    assert t.classify(0.3326) == F.NO_MATCH
    assert F.DEFAULT_REVIEW_THRESHOLD > 0.3326

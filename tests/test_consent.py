"""Consent records: signing, verification, and the bindings that make them mean something."""

from __future__ import annotations

import json

import pytest
from eth_account import Account

from pom import consent
from pom.consent import ConsentError, ConsentRecord

KEY = "0x" + "11" * 32
OTHER_KEY = "0x" + "22" * 32
IMAGE = "a" * 64


@pytest.fixture
def record() -> ConsentRecord:
    return consent.sign(IMAGE, "Test Subject", KEY)


def test_signing_produces_a_verifiable_record(record):
    report = consent.verify(record)
    assert report["signature_valid"]
    assert report["signer_matches"]
    assert report["ok"]


def test_signer_address_matches_the_key(record):
    assert record.signer_address == Account.from_key(KEY).address


def test_consent_covers_the_image_it_names(record):
    assert consent.verify(record, IMAGE)["image_matches"] is True


def test_consent_for_one_image_does_not_authorise_another(record):
    """The whole point of naming the image in the signed statement."""
    report = consent.verify(record, "b" * 64)
    assert report["image_matches"] is False
    assert not report["ok"]
    assert any("different image" in p for p in report["problems"])


def test_editing_the_subject_breaks_the_signature(record):
    forged = ConsentRecord(**{**record.as_dict(), "subject": "Someone Else"})
    report = consent.verify(forged)
    assert not report["ok"]


def test_editing_the_image_hash_breaks_the_signature(record):
    forged = ConsentRecord(**{**record.as_dict(), "image_sha256": "b" * 64})
    assert not consent.verify(forged)["ok"]


def test_editing_the_timestamp_breaks_the_signature(record):
    forged = ConsentRecord(**{**record.as_dict(), "signed_at": "2099-01-01T00:00:00Z"})
    assert not consent.verify(forged)["ok"]


def test_a_signature_from_another_key_is_rejected(record):
    other = consent.sign(IMAGE, "Test Subject", OTHER_KEY)
    forged = ConsentRecord(**{**record.as_dict(), "signature": other.signature})
    report = consent.verify(forged)
    assert not report["signer_matches"]
    assert not report["ok"]


def test_claiming_someone_elses_address_is_caught(record):
    """A record cannot borrow another party's identity: the signature recovers to the
    key that actually signed, and that is compared against the claim."""
    impostor = ConsentRecord(**{
        **record.as_dict(),
        "signer_address": Account.from_key(OTHER_KEY).address,
    })
    report = consent.verify(impostor)
    assert report["signature_valid"]
    assert not report["signer_matches"]
    assert not report["ok"]


def test_the_message_is_rebuilt_from_the_record(record):
    """Never stored separately, so a record cannot carry a signature over text that
    differs from its own fields."""
    assert record.image_sha256 in record.message
    assert record.subject in record.message
    assert record.signed_at in record.message


def test_hash_is_deterministic(record):
    assert record.hash_hex == ConsentRecord(**record.as_dict()).hash_hex


def test_hash_changes_with_any_field(record):
    changed = ConsentRecord(**{**record.as_dict(), "subject": "Other"})
    assert changed.hash_hex != record.hash_hex


def test_hash_is_a_bytes32_hex_string(record):
    assert record.hash_hex.startswith("0x")
    assert len(record.hash_hex) == 66


def test_round_trips_through_disk(record, tmp_path):
    path = consent.save(record, tmp_path / "consent.json")
    assert consent.load(path) == record
    assert consent.load(path).hash_hex == record.hash_hex


def test_saved_record_is_valid_json(record, tmp_path):
    path = consent.save(record, tmp_path / "consent.json")
    json.loads(path.read_text(encoding="utf-8"))


def test_loading_an_incomplete_record_fails_loudly(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"version": 1, "subject": "x"}), encoding="utf-8")
    with pytest.raises(ConsentError, match="missing fields"):
        consent.load(path)


def test_statement_mentions_what_is_published(record):
    """A consent statement that hides what happens is not consent."""
    text = record.statement.lower()
    assert "reverse image search" in text
    assert "blockchain" in text

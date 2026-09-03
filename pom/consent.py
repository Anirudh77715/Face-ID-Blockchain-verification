"""Subject consent, signed and bound into the attestation.

A pipeline that takes a face and finds where that person appears online is only defensible
when the person agreed to it. Saying so in a README is not a mechanism; this is.

The subject signs a statement that names the exact image being scanned - by its SHA-256,
so consent cannot be transplanted onto a different photo - and the hash of that signed
record is committed on chain alongside the evidence root. What goes on the public ledger
is a hash: the consent document, the subject's name, and the signature all stay local.

    py consent.py --image me.jpg --subject "Your Name"       # writes consent.json
    py run.py --image me.jpg --consent consent.json ...

Verification later reports whether an attestation carried consent, and whether the consent
record still matches the image that was actually scanned. An attestation without consent is
allowed - the contract stores zero - but it is reported differently, because "we do not
know if they agreed" is not the same claim as "they agreed".
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

STATEMENT = (
    "I consent to a reverse image search being performed on the image identified below, "
    "and to a hash of the result being recorded on a public blockchain. "
    "No image, URL or biometric template is published."
)


class ConsentError(Exception):
    pass


@dataclass(frozen=True)
class ConsentRecord:
    version: int
    statement: str
    subject: str
    image_sha256: str
    signed_at: str
    signer_address: str
    signature: str

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def message(self) -> str:
        """Exactly what was signed. Rebuilt from fields, never stored separately, so a
        record cannot carry a signature over text different from its own contents."""
        return build_message(self.statement, self.subject, self.image_sha256,
                             self.signed_at)

    @property
    def hash_hex(self) -> str:
        """keccak256 over the canonical record. This is what goes on chain."""
        return "0x" + consent_hash(self).hex()


def build_message(statement: str, subject: str, image_sha256: str, signed_at: str) -> str:
    return (f"{statement}\n\n"
            f"subject: {subject}\n"
            f"image_sha256: {image_sha256}\n"
            f"signed_at: {signed_at}")


def consent_hash(record: ConsentRecord) -> bytes:
    payload = json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return keccak(payload)


def sign(image_sha256: str, subject: str, private_key: str) -> ConsentRecord:
    account = Account.from_key(private_key)
    signed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    message = build_message(STATEMENT, subject, image_sha256, signed_at)
    signature = account.sign_message(encode_defunct(text=message))

    return ConsentRecord(
        version=1,
        statement=STATEMENT,
        subject=subject,
        image_sha256=image_sha256,
        signed_at=signed_at,
        signer_address=account.address,
        signature=signature.signature.hex()
        if signature.signature.hex().startswith("0x")
        else "0x" + signature.signature.hex(),
    )


def verify(record: ConsentRecord, image_sha256: str | None = None) -> dict:
    """Check the signature, and that it covers the image actually scanned.

    Returns a report rather than raising: a missing or mismatched consent is information
    a verifier should see, not an error that hides the rest of the result.
    """
    report = {"signature_valid": False, "signer_matches": False,
              "image_matches": None, "recovered": None, "problems": []}

    try:
        recovered = Account.recover_message(
            encode_defunct(text=record.message), signature=record.signature)
        report["recovered"] = recovered
        report["signature_valid"] = True
        report["signer_matches"] = (
            recovered.lower() == record.signer_address.lower())
        if not report["signer_matches"]:
            report["problems"].append(
                f"signature recovers to {recovered}, record claims "
                f"{record.signer_address}")
    except Exception as e:
        report["problems"].append(f"signature does not verify: {type(e).__name__}: {e}")

    if image_sha256 is not None:
        report["image_matches"] = record.image_sha256 == image_sha256
        if not report["image_matches"]:
            # The whole point of naming the image in the statement: consent for one photo
            # must not silently authorise a scan of another.
            report["problems"].append(
                "consent was signed for a different image "
                f"({record.image_sha256[:16]}... vs {image_sha256[:16]}...)")

    report["ok"] = (report["signature_valid"] and report["signer_matches"]
                    and report["image_matches"] is not False)
    return report


def load(path: Path) -> ConsentRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = set(ConsentRecord.__annotations__) - set(data)
    if missing:
        raise ConsentError(f"consent record is missing fields: {sorted(missing)}")
    return ConsentRecord(**{k: data[k] for k in ConsentRecord.__annotations__})


def save(record: ConsentRecord, path: Path) -> Path:
    Path(path).write_text(
        json.dumps(record.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return Path(path)

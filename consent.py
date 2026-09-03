"""Create a signed consent record for an image.

    py consent.py --image me.jpg --subject "Your Name"

Writes consent.json. Pass it to the pipeline with --consent, and the hash of the record is
committed on chain alongside the evidence root.

The statement names the image by SHA-256, so a consent signed for one photo cannot
authorise a scan of another - the pipeline checks that and refuses the mismatch.

What this proves and what it does not: the signature proves that whoever holds the signing
key agreed to the statement, and that the agreement covers this exact image. It does not
prove the key belongs to the named person. Binding a key to a real identity needs something
this pipeline has no business inventing - a wallet the subject already controls, or an
identity provider. The honest claim is "the holder of 0xabc... consented", and that is what
verification reports.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eth_account import Account

from pom import consent
from pom.face import sha256_bytes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, type=Path,
                    help="the image the subject is consenting to have scanned")
    ap.add_argument("--subject", required=True, help="who is consenting")
    ap.add_argument("--out", type=Path, default=Path("consent.json"))
    ap.add_argument("--key", help="signing key. Omitted, a fresh one is generated and "
                                  "only its address is kept - enough to verify the "
                                  "signature, and nothing worth stealing")
    args = ap.parse_args()

    if not args.image.exists():
        print(f"no such image: {args.image}", file=sys.stderr)
        return 1
    if args.out.exists():
        print(f"{args.out} already exists; move it aside first", file=sys.stderr)
        return 1

    image_sha256 = sha256_bytes(args.image.read_bytes())
    key = args.key or Account.create().key.hex()

    record = consent.sign(image_sha256, args.subject, key)
    consent.save(record, args.out)

    print("consent recorded")
    print(f"  subject       {record.subject}")
    print(f"  image         {args.image.name}")
    print(f"  image sha256  {record.image_sha256}")
    print(f"  signed at     {record.signed_at}")
    print(f"  signer        {record.signer_address}")
    print(f"  consent hash  {record.hash_hex}   <- this goes on chain")
    print(f"  saved         {args.out}")

    report = consent.verify(record, image_sha256)
    print(f"\n  self-check    {'signature verifies' if report['ok'] else report['problems']}")
    print("\nNext:")
    print(f"  py run.py --image {args.image} --consent {args.out} --chain local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

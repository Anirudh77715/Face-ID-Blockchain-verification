"""Persistent, anonymous Face IDs - "have we seen this face before?"

A Face ID is an internal label like `F-001`. It is deliberately anonymous: the registry
never stores or infers a person's name. `F-001` means "the face first seen in this run",
nothing more, and the correct way to report a hit is **"matches Face ID F-001"** or
**"likely the same face"** - never "identity confirmed as <name>". Nothing here
establishes anyone's real-world or legal identity, and there is no independent identity
verification anywhere in this project to support such a claim.

Three mechanisms exist in this codebase and they answer three different questions. They
must not be confused, and this module is only the second:

  image SHA-256      exact file integrity. Two photographs of one person have different
                     image hashes, and that is correct - it is a hash of bytes, not of a
                     face. It is never used to decide whether two photos show the same
                     person.
  face embedding     biometric similarity. This module. Compares a new 128-d SFace
                     vector against vectors seen before.
  Merkle root/chain  whether previously attested evidence still matches its commitment.
                     The chain plays no part in recognising anyone.

**Thresholds are a separate calibration problem from candidate matching.** The pipeline's
web-candidate threshold is OpenCV's published same-identity figure (0.363), measured for
"is this returned image the same face as the query?" where candidates are near-duplicates
of the query photo. Persistent identity is a harder question asked against every face in
the registry, and a false merge collapses two people into one identifier - so it gets its
own, higher thresholds.

The defaults were derived from two measurements, and the second one corrected the first.

`scripts/calibrate_faceid.py` over `bench/faces` (12 images, 8 people) gives same-person
0.7876-0.9562 and different-person at most 0.2768. That set is historical studio
portraiture: frontal, evenly lit, and unusually easy. Calibrating on it alone produced a
0.66 threshold that **failed on real photographs** - three uploads of one person scored
0.6191, 0.6400 and 0.6417 against each other, landed under 0.66, and were registered as
three separate identities. The threshold was right for the benchmark and wrong for the
job.

Re-measured against ordinary photographs (varied pose, lighting, crop and resolution):

    same person, lowest observed        0.6191
    different people, highest observed  0.3326

0.50 sits inside that gap with roughly 0.17 of headroom above the worst different-person
pair and 0.12 below the worst same-person pair. The review floor moved to 0.35 to sit
just above the observed different-person ceiling.

**Neither number is authoritative.** They come from tens of comparisons, not thousands,
and cannot support a quoted false-match rate. Lighting, pose, age gap and demographic
coverage all move them. Re-measure on your own data; both are configurable - see
`Thresholds`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "evidence" / "faceids.json"
REGISTRY_VERSION = 1

# Provisional defaults - see the module docstring for how they were measured and why
# they are not authoritative. Override per-run with --faceid-high / --faceid-review, or
# for a whole environment with POM_FACEID_HIGH / POM_FACEID_REVIEW.
DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 0.50
DEFAULT_REVIEW_THRESHOLD = 0.35

MATCH = "match"
REVIEW = "review"
NO_MATCH = "no_match"

Similarity = Callable[[object, object], float]


class FaceIdError(Exception):
    pass


@dataclass(frozen=True)
class Thresholds:
    """Where the three matching states begin and end.

    similarity >= high            -> MATCH        (likely the same face)
    review <= similarity < high   -> REVIEW       (a person should look)
    similarity < review           -> NO_MATCH     (a new Face ID is created)
    """

    high: float = DEFAULT_HIGH_CONFIDENCE_THRESHOLD
    review: float = DEFAULT_REVIEW_THRESHOLD

    def __post_init__(self) -> None:
        if not 0.0 <= self.review <= self.high <= 1.0:
            raise FaceIdError(
                f"thresholds must satisfy 0 <= review ({self.review}) <= "
                f"high ({self.high}) <= 1")

    @classmethod
    def from_env(cls, high: float | None = None,
                 review: float | None = None) -> Thresholds:
        """Explicit argument wins, then environment, then the measured default."""
        def pick(value, env_name, fallback):
            if value is not None:
                return float(value)
            raw = os.environ.get(env_name)
            return float(raw) if raw else fallback

        return cls(
            high=pick(high, "POM_FACEID_HIGH", DEFAULT_HIGH_CONFIDENCE_THRESHOLD),
            review=pick(review, "POM_FACEID_REVIEW", DEFAULT_REVIEW_THRESHOLD),
        )

    def classify(self, similarity: float | None) -> str:
        if similarity is None:
            return NO_MATCH
        if similarity >= self.high:
            return MATCH
        if similarity >= self.review:
            return REVIEW
        return NO_MATCH

    def as_dict(self) -> dict:
        return {"high_confidence": self.high, "review": self.review}


@dataclass
class Sighting:
    """One photograph that contributed an embedding to a Face ID."""

    image_sha256: str
    embedding: list[float]
    added_at: str
    source_name: str | None = None
    # Filled in once the run reaches the chain, so a Face ID can point at the evidence
    # that was attested for it. A reference, never a dependency: recognition works with
    # no chain at all.
    attestation: dict | None = None

    @property
    def embedding_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(self.embedding, separators=(",", ":")).encode()).hexdigest()


@dataclass
class FaceRecord:
    """One anonymous identity and every embedding recorded for it."""

    face_id: str
    created_at: str
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def photo_count(self) -> int:
        return len(self.sightings)

    def attestations(self) -> list[dict]:
        return [s.attestation for s in self.sightings if s.attestation]


@dataclass
class Decision:
    """The outcome of looking one embedding up against the registry."""

    status: str
    face_id: str | None
    similarity: float | None
    thresholds: Thresholds
    photo_count: int = 0
    created: bool = False
    ranked: list[tuple[str, float]] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return self.status == MATCH

    @property
    def headline(self) -> str:
        """Wording is deliberate: a similarity score is evidence about faces, not proof
        of who someone is."""
        if self.created:
            return f"NEW FACE REGISTERED - {self.face_id}"
        if self.status == MATCH:
            return f"MATCH FOUND - likely the same face as {self.face_id}"
        if self.status == REVIEW:
            return f"POSSIBLE MATCH - {self.face_id} - REVIEW REQUIRED"
        return "NO EXISTING FACE MATCH"

    def as_dict(self) -> dict:
        return {
            "face_id": self.face_id,
            "status": self.status,
            "similarity": (round(self.similarity, 6)
                           if self.similarity is not None else None),
            "created": self.created,
            "photo_count": self.photo_count,
            "thresholds": self.thresholds.as_dict(),
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def to_list(embedding) -> list[float]:
    """A 128-d SFace feature as plain floats, for JSON. Shape is flattened because the
    encoder hands back (1, 128) and the registry only ever needs the vector."""
    try:
        return [float(x) for x in embedding.flatten()]
    except AttributeError:
        return [float(x) for x in embedding]


class FaceRegistry:
    """A small JSON-backed store of anonymous faces.

    JSON rather than SQLite on purpose: the file is a few hundred KB at demo scale, it
    diffs and inspects in a text editor, and a reviewer can read exactly what is retained
    about a person without a database client. Swap it if the registry ever grows.

    Biometric data stays **off-chain and local**. `evidence/` is gitignored, so nothing
    here is committed to the repository, and no embedding is ever written to a
    blockchain - only a hash of one, inside the evidence Merkle root.
    """

    def __init__(self, path: Path = REGISTRY_PATH,
                 thresholds: Thresholds | None = None):
        self.path = Path(path)
        self.thresholds = thresholds or Thresholds()
        self.faces: list[FaceRecord] = []
        # The highest Face ID number ever issued, including ids since merged away. Kept
        # so a retired label is never handed to a different face - an old evidence bundle
        # citing F-005 must not come to mean someone else.
        self._high_water = 0
        self.load()

    # ------------------------------------------------------------- persistence

    def load(self) -> FaceRegistry:
        if not self.path.exists():
            self.faces = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise FaceIdError(f"face registry is unreadable: {self.path}\n  {e}") from e

        self._high_water = int(raw.get("high_water", 0))
        self.faces = [
            FaceRecord(
                face_id=f["face_id"],
                created_at=f["created_at"],
                sightings=[Sighting(**s) for s in f.get("sightings", [])],
            )
            for f in raw.get("faces", [])
        ]
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._high_water = self._highest_issued()
        body = {
            "version": REGISTRY_VERSION,
            "updated_at": _now(),
            "high_water": self._high_water,
            "faces": [
                {
                    "face_id": f.face_id,
                    "created_at": f.created_at,
                    "sightings": [asdict(s) for s in f.sightings],
                }
                for f in self.faces
            ],
        }
        # Written aside then moved: a half-written registry would lose every face
        # recorded so far, and this runs while a pipeline is mid-flight.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return self.path

    # ------------------------------------------------------------------ lookup

    def get(self, face_id: str) -> FaceRecord | None:
        return next((f for f in self.faces if f.face_id == face_id), None)

    def _highest_issued(self) -> int:
        numbers = [self._high_water]
        for f in self.faces:
            try:
                numbers.append(int(f.face_id.split("-")[1]))
            except (IndexError, ValueError):
                continue
        return max(numbers)

    def next_id(self) -> str:
        """One past the highest number ever issued, retired ids included."""
        return f"F-{self._highest_issued() + 1:03d}"

    def score(self, embedding, similarity: Similarity) -> list[tuple[str, float]]:
        """Every Face ID scored against this embedding, best first.

        **Aggregation is the maximum** over a face's stored embeddings: a face matches if
        the new photo resembles *any* photograph recorded for it. The mean was rejected
        because it punishes a well-populated Face ID - add a poorly-lit sighting and every
        later comparison is dragged down, so the better-documented a face is the harder it
        becomes to recognise, which is backwards.

        The cost of the maximum is that one bad sighting can pull a stranger in. That is
        why REVIEW exists, and why nothing is merged automatically below `high`.
        """
        import numpy as np

        scored = []
        query = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        for face in self.faces:
            best = None
            for sighting in face.sightings:
                stored = np.asarray(sighting.embedding,
                                    dtype=np.float32).reshape(1, -1)
                value = float(similarity(query, stored))
                best = value if best is None else max(best, value)
            if best is not None:
                scored.append((face.face_id, best))
        return sorted(scored, key=lambda pair: -pair[1])

    def identify(self, embedding, similarity: Similarity) -> Decision:
        """Look an embedding up. Reads only - nothing is created or modified."""
        ranked = self.score(embedding, similarity)
        if not ranked:
            return Decision(NO_MATCH, None, None, self.thresholds, ranked=[])

        face_id, best = ranked[0]
        status = self.thresholds.classify(best)
        record = self.get(face_id)
        return Decision(
            status=status,
            face_id=face_id if status != NO_MATCH else None,
            similarity=best,
            thresholds=self.thresholds,
            photo_count=record.photo_count if record else 0,
            ranked=ranked,
        )

    # ----------------------------------------------------------------- writing

    def observe(self, embedding, image_sha256: str, similarity: Similarity,
                source_name: str | None = None) -> Decision:
        """Identify, then record - the one call the pipeline makes.

        A MATCH appends this photograph to the existing Face ID, so a face accumulates
        embeddings rather than being judged forever by its first photograph. Anything
        below `high` creates a **new** Face ID: a REVIEW is by definition not certain
        enough to merge two identities, and an unnecessary Face ID is a far cheaper
        mistake than a wrong merge, which cannot be undone by looking at more photos.
        """
        decision = self.identify(embedding, similarity)
        vector = to_list(embedding)
        sighting = Sighting(image_sha256=image_sha256, embedding=vector,
                            added_at=_now(), source_name=source_name)

        if decision.status == MATCH and decision.face_id:
            record = self.get(decision.face_id)
            # Duplicate prevention: the same file scanned twice adds no information, so
            # it updates nothing rather than inflating the photo count.
            if any(s.image_sha256 == image_sha256 for s in record.sightings):
                decision.photo_count = record.photo_count
                return decision
            record.sightings.append(sighting)
            self.save()
            decision.photo_count = record.photo_count
            return decision

        record = FaceRecord(face_id=self.next_id(), created_at=_now(),
                            sightings=[sighting])
        self.faces.append(record)
        self.save()

        return Decision(
            status=decision.status,
            face_id=record.face_id,
            similarity=decision.similarity,
            thresholds=self.thresholds,
            photo_count=1,
            created=True,
            ranked=decision.ranked,
        )

    def merge(self, keep: str, absorb: str) -> FaceRecord:
        """Fold one Face ID into another, keeping every photograph.

        A threshold set too high splits one person across several Face IDs, and lowering
        it afterwards does not repair a registry that already recorded the mistake. This
        does.

        Merging stays manual on purpose. The matcher will not join two identities on its
        own below the match threshold, because a wrong merge cannot be undone by looking
        at more photographs - but a person who can see both sets of photographs can say
        so, and that is a different kind of evidence.
        """
        target, source = self.get(keep), self.get(absorb)
        if target is None:
            raise FaceIdError(f"no such face id: {keep}")
        if source is None:
            raise FaceIdError(f"no such face id: {absorb}")
        if keep == absorb:
            raise FaceIdError("cannot merge a face id into itself")

        known = {s.image_sha256 for s in target.sightings}
        target.sightings.extend(s for s in source.sightings
                                if s.image_sha256 not in known)
        target.sightings.sort(key=lambda s: s.added_at)
        self.faces = [f for f in self.faces if f.face_id != absorb]
        self.save()
        return target

    def attach_attestation(self, face_id: str, image_sha256: str,
                           attestation: dict) -> bool:
        """Point a sighting at the evidence that was attested for it."""
        record = self.get(face_id)
        if not record:
            return False
        for sighting in record.sightings:
            if sighting.image_sha256 == image_sha256:
                sighting.attestation = attestation
                self.save()
                return True
        return False

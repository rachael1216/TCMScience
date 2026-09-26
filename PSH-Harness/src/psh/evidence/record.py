"""EvidenceRecord: evidence has an origin, not just text.

A reviewer identified the structural limit of v0.1's evidence layer. Verification took
``sources: Mapping[str, str]`` — an identifier pointing at a blob of text — and nothing
established where that text came from:

    {"34449189": "A fabricated paragraph asserting the claim is true"}

I confirmed it: a fabricated abstract attached to a real PMID produced ``support=True``. So
what v0.1 verified was *claim ↔ supplied text*, not *claim ↔ trusted scientific evidence*.
Lexical support cannot tell the difference, and no improvement to the matching would fix it,
because the defect is in provenance rather than in matching.

An ``EvidenceRecord`` therefore carries how it was obtained: which capability retrieved it,
in which run, when, a hash of the content, and its retraction status. Text supplied by a
caller is representable — sometimes a user legitimately pastes an abstract — but it is
marked untrusted and cannot alone establish support.

Spans are located, not quoted. A model asked to identify supporting text returns offsets
that are checked against the source; a span that cannot be found in the source fails, which
closes the fabricated-span path found in v0.1's model verifier.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ..contracts import PolicyDenied, content_hash, new_id, utc_now
from ..labels import DataLabel, Sensitivity

__all__ = ["revalidate_trust", "SourceType", "RetractionStatus", "EvidenceRecord", "VerifiedSpan",
           "locate_span"]


class SourceType(str, Enum):
    PMID = "pmid"
    DOI = "doi"
    NCT = "nct"
    PMCID = "pmcid"
    DATASET = "dataset"
    LOCAL_ARTIFACT = "local_artifact"
    USER_SUPPLIED = "user_supplied"


class RetractionStatus(str, Enum):
    """Three states, because "not checked" is not the same as "not retracted"."""

    NOT_RETRACTED = "not_retracted"
    RETRACTED = "retracted"
    CONCERN = "expression_of_concern"
    UNVERIFIED = "unverified"


#: Capabilities whose retrieval is treated as trusted provenance. A record retrieved by
#: anything else is representable but cannot alone establish support.
TRUSTED_RETRIEVERS: frozenset[str] = frozenset({
    "sable.pubmed_search", "sable.pubmed_fetch", "sable.trial_search",
    "sable.check_retraction", "psh.crossref_fetch",
})


@dataclass(frozen=True, slots=True)
class VerifiedSpan:
    """A span located in the source by offset, with the source hash it came from.

    Offsets plus a source hash make a support verdict checkable after the fact: given the
    record, anyone can re-extract the same characters and confirm the verdict rested on text
    that actually exists.
    """

    start: int
    end: int
    source_hash: str
    text_hash: str

    def extract(self, source_text: str) -> str:
        return source_text[self.start:self.end]

    def verify_against(self, source_text: str) -> bool:
        """True if this span still resolves to the same text in the given source."""
        if not 0 <= self.start < self.end <= len(source_text):
            return False
        excerpt = source_text[self.start:self.end]
        return hashlib.sha256(excerpt.encode("utf-8")).hexdigest() == self.text_hash


def locate_span(source_text: str, candidate: str, *,
                source_hash: str = "") -> VerifiedSpan | None:
    """Find ``candidate`` in ``source_text`` and return its verified span.

    Exact match first, then a whitespace-normalised search, because a model asked to quote a
    sentence commonly re-wraps it. Anything looser is refused: fuzzy matching would let a
    paraphrase pass as a quotation, which is the failure this function exists to prevent.
    """
    if not candidate or not candidate.strip():
        return None

    index = source_text.find(candidate)
    if index >= 0:
        return _span(source_text, index, index + len(candidate), source_hash)

    # Whitespace-insensitive search: build a regex from the candidate's tokens.
    tokens = [re.escape(t) for t in candidate.split()]
    if not tokens:
        return None
    pattern = re.compile(r"\s+".join(tokens))
    match = pattern.search(source_text)
    if match:
        return _span(source_text, match.start(), match.end(), source_hash)
    return None


def _span(source_text: str, start: int, end: int, source_hash: str) -> VerifiedSpan:
    excerpt = source_text[start:end]
    return VerifiedSpan(
        start=start, end=end, source_hash=source_hash,
        text_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """A retrieved source with the provenance needed to trust it."""

    identifier: str
    source_type: SourceType
    content: str
    content_hash: str
    retrieved_by: str
    retrieval_run: str
    retrieved_at: float = field(default_factory=utc_now)
    title: str = ""
    source_uri: str = ""
    retraction: RetractionStatus = RetractionStatus.UNVERIFIED
    publication_status: str = ""
    quality: str = "undetermined"
    label: DataLabel = field(default_factory=lambda: DataLabel(Sensitivity.PUBLIC,
                                                              shareable=True))
    #: HMAC over the identifying fields, set by EvidenceSigner.sign(). Empty for a record
    #: nobody signed — which is every record a caller builds by hand.
    signature: str = ""
    #: Whether this record came through a registered retrieval capability. In v0.3 this was
    #: ``retrieved_by in TRUSTED_RETRIEVERS`` — a string comparison anyone could satisfy by
    #: typing the name. It is now set only by EvidenceSigner.sign(); from_text() defaults it
    #: to False regardless of the retriever name.
    trusted: bool = False
    evidence_id: str = field(default_factory=lambda: new_id("evr"))
    #: What this source licenses: the population enrolled, the outcomes measured, the design.
    #: Derived from the content at construction so every record carries its own boundary and
    #: a caller cannot forget to compute it.
    licensed_scope: Any = None

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise PolicyDenied("evidence requires a source identifier")
        if not self.retrieved_by or not self.retrieval_run:
            raise PolicyDenied(
                "evidence requires retrieval provenance: which capability retrieved it and "
                "in which run. Text without an origin cannot be trusted evidence — pass "
                "trusted=False to represent user-supplied text explicitly")
        expected = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise PolicyDenied(
                "content hash does not match the content; the record has been altered")
        if self.licensed_scope is None and self.content.strip():
            from .claims import LicensedScope

            object.__setattr__(self, "licensed_scope",
                               LicensedScope.from_text(self.content))

    @classmethod
    def from_text(cls, *, identifier: str, text: str, retrieved_by: str,
                  retrieval_run: str, source_type: SourceType | str = SourceType.PMID,
                  retracted: bool | None = None, trusted: bool | None = None,
                  **kw: Any) -> "EvidenceRecord":
        """Build a record, computing its hash and inferring trust from the retriever."""
        if trusted is None:
            # Never inferred from the name. A retriever name is a claim; a signature is
            # evidence. TRUSTED_RETRIEVERS is retained only as documentation of which
            # capabilities the kernel registers signers for.
            trusted = False
        retraction = {
            True: RetractionStatus.RETRACTED,
            False: RetractionStatus.NOT_RETRACTED,
            None: RetractionStatus.UNVERIFIED,
        }[retracted]
        return cls(
            identifier=identifier, source_type=SourceType(source_type), content=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            retrieved_by=retrieved_by, retrieval_run=retrieval_run,
            retraction=retraction, trusted=bool(trusted), **kw)

    # ------------------------------------------------------------------- checks
    @property
    def usable(self) -> tuple[bool, str]:
        """Whether this record may support a claim, and why not if it may not.

        The chain the review specifies, in order: the identifier exists, retrieval was
        through a trusted capability, the content is intact, and the source is not retracted.
        Each failure is distinct, because "retracted" and "unverified" call for different
        actions from the reader.
        """
        if not self.content.strip():
            return False, "the record carries no content to check a claim against"
        # Retraction is checked before provenance: it is the more specific finding, and a
        # reader needs to know a paper was retracted even when the copy in hand came from an
        # untrusted route.
        if self.retraction is RetractionStatus.RETRACTED:
            return False, (f"{self.identifier} is retracted; a retracted source cannot "
                           "support a claim")
        if self.retraction is RetractionStatus.CONCERN:
            return False, (f"{self.identifier} carries an expression of concern; treat its "
                           "findings as unreliable until resolved")
        if self.retraction is RetractionStatus.UNVERIFIED and self.trusted:
            return False, (f"retraction status of {self.identifier} has not been checked; "
                           "verify it before relying on this source")
        return True, "record is intact and not retracted"

    @property
    def provenance_caveat(self) -> str:
        """A caveat for records whose origin could not be independently established.

        Untrusted provenance *caps* a verdict rather than forcing it to UNKNOWN. Refusing
        every caller-supplied source outright would refuse every ordinary run — a physician
        pasting an abstract is the normal case, and an invariant that blocks normal work gets
        switched off, which closes nothing. So the claim is downgraded to PARTIAL and the
        reader is told the text was not independently retrieved, instead of being told the
        source does not support the claim when the real issue is that nothing checked where
        the text came from.
        """
        if self.trusted:
            return ""
        return (f"source text for {self.identifier} was supplied rather than retrieved "
                f"through a trusted capability, so its provenance is unverified; re-retrieve "
                f"it with a literature tool to raise this above partial support")

    def verify_integrity(self) -> bool:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest() == self.content_hash

    def locate(self, candidate: str) -> VerifiedSpan | None:
        return locate_span(self.content, candidate, source_hash=self.content_hash)

    def as_reference(self) -> dict[str, Any]:
        """A PHI-free, content-free summary suitable for events and audit."""
        return {"evidence_id": self.evidence_id, "identifier": self.identifier,
                "source_type": self.source_type.value, "retrieved_by": self.retrieved_by,
                "retrieval_run": self.retrieval_run,
                "content_hash": self.content_hash[:32],
                "retraction": self.retraction.value, "trusted": self.trusted}


def revalidate_trust(record: "EvidenceRecord", signer: Any) -> "EvidenceRecord":
    """Return ``record`` with ``trusted`` recomputed from its signature.

    A record is a frozen dataclass, so a tampered copy made with ``dataclasses.replace``
    keeps the OLD signature and the OLD ``trusted=True``. Anything that consumes evidence must
    therefore recompute trust from the signature rather than read the flag — this is the one
    place that does it.
    """
    from dataclasses import replace as _replace

    return _replace(record, trusted=bool(signer.verify(record)))

"""Data classification and information-flow labels. Frozen module.

This is the module that closes the first demonstrated hole in the predecessor design.
There, a PHI boundary was enforced on *tool* arguments only, so a local tool could read
a chart note into the transcript and the very next model call shipped the identifier to
a remote provider. Scanning strings at the tool boundary cannot catch that, because by
then the identifier is no longer in an argument — it is in the context.

The fix is to label the *data* rather than the call site, and to propagate labels through
derivation. A summary of a PHI note is PHI. A count derived from a PHI cohort is
PHI-derived until something explicitly declassifies it. Every egress point — tool, model,
delegation, final output — then asks one question of the value rather than re-deriving
sensitivity from scratch.

Design rules encoded here
-------------------------
1. ``DataLabel`` is a lattice: combining two labels yields the *more* restrictive one.
   Combination can never lower sensitivity, which is what makes propagation safe by
   default.
2. Declassification is explicit, recorded, and separate from combination. There is no
   code path that quietly lowers a label.
3. A ``Labeled`` value carries ``derived_from`` so provenance of the classification
   itself is inspectable — "why is this PHI?" is answerable.
4. Labels never store the sensitive text. As in the predecessor's PHI findings, the
   label records category and provenance, so an audit trail cannot become a second copy
   of the data it protects.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Sensitivity", "Destination", "DataLabel", "Labeled", "Declassification",
    "PUBLIC", "INTERNAL", "RESEARCH_DEIDENTIFIED", "SENSITIVE", "PHI", "SECRET",
    "combine", "label_of", "deep_label_of", "unwrap", "unwrap_deep", "walk_values",
]


class Sensitivity(IntEnum):
    """The classification lattice, ordered so policy can compare numerically.

    PUBLIC                - published literature, public database records.
    INTERNAL              - the user's own notes, drafts, project state.
    RESEARCH_DEIDENTIFIED - study data with identifiers removed under a documented
                            process. Deliberately ranked *below* SENSITIVE: it is the
                            normal working material of research and must not be so
                            restricted that users route around the system.
    SENSITIVE             - re-identifiable or contractually restricted data.
    PHI                   - protected health information; at least one identifier.
    SECRET                - credentials and keys. Ranked highest because a leaked key
                            compromises everything below it.
    """

    PUBLIC = 0
    INTERNAL = 1
    RESEARCH_DEIDENTIFIED = 2
    SENSITIVE = 3
    PHI = 4
    SECRET = 5

    @property
    def label(self) -> str:
        return self.name.lower()


class Destination(IntEnum):
    """Where a value is about to go. Every egress point names itself.

    LOCAL_COMPUTE  - in-process or local sandbox; no network.
    LOCAL_MODEL    - a model running on the user's own hardware.
    TRUSTED_REMOTE - a provider under an institutional agreement (BAA or equivalent).
    PUBLIC_REMOTE  - a public API or model provider with no such agreement.
    USER_OUTPUT    - returned to the human operator.
    PERSISTENT     - written to durable storage.
    """

    LOCAL_COMPUTE = 0
    LOCAL_MODEL = 1
    TRUSTED_REMOTE = 2
    PUBLIC_REMOTE = 3
    USER_OUTPUT = 4
    PERSISTENT = 5


#: Default policy: the highest sensitivity each destination may receive.
#: PHI may reach local compute, a local model, and the user who is treating the patient
#: — but never a public provider. This table is the single place that decision lives.
DEFAULT_CEILINGS: Mapping[Destination, Sensitivity] = {
    Destination.LOCAL_COMPUTE: Sensitivity.SECRET,
    Destination.LOCAL_MODEL: Sensitivity.PHI,
    Destination.TRUSTED_REMOTE: Sensitivity.SENSITIVE,
    Destination.PUBLIC_REMOTE: Sensitivity.RESEARCH_DEIDENTIFIED,
    Destination.USER_OUTPUT: Sensitivity.PHI,
    # PHI may be written to the user's own machine — that is the normal case for a
    # clinician's notes. A deployment keeping its index on shared infrastructure lowers this
    # through PolicySnapshot.max_data_label, which the PersistenceGateway enforces
    # separately; a global ceiling below PHI would make local clinical work impossible.
    Destination.PERSISTENT: Sensitivity.PHI,
}


@dataclass(frozen=True, slots=True)
class DataLabel:
    """The classification of one value.

    ``categories`` names *what kind* of sensitive content was detected (e.g. an MRN
    category) without ever storing the matched text. ``rationale`` is a short
    human-readable reason, also text-free with respect to the data itself.
    """

    sensitivity: Sensitivity = Sensitivity.PUBLIC
    categories: tuple[str, ...] = ()
    rationale: str = ""
    classifier: str = ""
    shareable: bool = False

    def __post_init__(self) -> None:
        if self.shareable and self.sensitivity >= Sensitivity.SENSITIVE:
            raise ValueError(
                "a value at SENSITIVE or above cannot be marked shareable without an "
                "explicit Declassification; see labels.declassify()")

    def permits(self, destination: Destination,
                ceilings: Mapping[Destination, Sensitivity] | None = None) -> bool:
        """True if a value with this label may reach ``destination``."""
        table = ceilings or DEFAULT_CEILINGS
        return self.sensitivity <= table.get(destination, Sensitivity.PUBLIC)

    def merged_with(self, other: "DataLabel") -> "DataLabel":
        """Return the join of two labels: never less restrictive than either input."""
        if other.sensitivity > self.sensitivity:
            higher, lower = other, self
        else:
            higher, lower = self, other
        cats = tuple(dict.fromkeys(self.categories + other.categories))
        return DataLabel(
            sensitivity=higher.sensitivity,
            categories=cats,
            rationale=higher.rationale or lower.rationale,
            classifier=higher.classifier or lower.classifier,
            shareable=self.shareable and other.shareable)

    def __str__(self) -> str:
        cats = f" [{', '.join(self.categories)}]" if self.categories else ""
        return f"{self.sensitivity.label}{cats}"


PUBLIC = DataLabel(Sensitivity.PUBLIC, rationale="no sensitive content detected",
                   shareable=True)
INTERNAL = DataLabel(Sensitivity.INTERNAL, rationale="user's own working material")
RESEARCH_DEIDENTIFIED = DataLabel(Sensitivity.RESEARCH_DEIDENTIFIED,
                                  rationale="de-identified study data")
SENSITIVE = DataLabel(Sensitivity.SENSITIVE, rationale="restricted or re-identifiable")
PHI = DataLabel(Sensitivity.PHI, rationale="protected health information")
SECRET = DataLabel(Sensitivity.SECRET, rationale="credential material")


@dataclass(frozen=True, slots=True)
class Declassification:
    """An explicit, recorded lowering of a label.

    Declassification is the only way sensitivity decreases. It is a separate type rather
    than a method argument so that every instance is a discrete auditable object with a
    principal attached: somebody chose to do this, and the record says who.
    """

    from_sensitivity: Sensitivity
    to_sensitivity: Sensitivity
    method: str
    principal: str
    rationale: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.to_sensitivity > self.from_sensitivity:
            raise ValueError("declassification cannot raise sensitivity; use merge instead")
        if not self.method or not self.principal or not self.rationale:
            raise ValueError("declassification requires method, principal and rationale")


@dataclass(frozen=True, slots=True)
class Labeled:
    """A value carrying its classification and the provenance of that classification.

    ``derived_from`` holds the ids of the labelled values this one was computed from.
    That is what makes taint tracking work: a summary produced from a PHI note records
    the note as an ancestor, so the summary cannot be treated as clean merely because
    the identifier did not survive verbatim into the text.
    """

    value: Any
    label: DataLabel = PUBLIC
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    derived_from: tuple[str, ...] = ()
    declassifications: tuple[Declassification, ...] = ()
    origin: str = ""

    @property
    def sensitivity(self) -> Sensitivity:
        return self.label.sensitivity

    def permits(self, destination: Destination,
                ceilings: Mapping[Destination, Sensitivity] | None = None) -> bool:
        return self.label.permits(destination, ceilings)

    def derive(self, new_value: Any, *, origin: str = "",
               extra: Iterable["Labeled"] = ()) -> "Labeled":
        """Return a new labelled value derived from this one (and optionally others).

        The derived label is the join of every contributing label, so sensitivity
        propagates forward automatically. This is the default path: code that transforms
        data gets taint tracking without having to think about it.
        """
        label = self.label
        ancestors = [self.id]
        for other in extra:
            label = label.merged_with(other.label)
            ancestors.append(other.id)
        return Labeled(value=new_value, label=label, derived_from=tuple(ancestors),
                       origin=origin or self.origin)

    def declassify(self, decl: Declassification) -> "Labeled":
        """Return a copy at a lower sensitivity, recording the justification."""
        if decl.from_sensitivity != self.label.sensitivity:
            raise ValueError(
                f"declassification declares from={decl.from_sensitivity.name} but the "
                f"value is {self.label.sensitivity.name}")
        lowered = replace(self.label, sensitivity=decl.to_sensitivity,
                          rationale=f"declassified: {decl.rationale}",
                          shareable=decl.to_sensitivity < Sensitivity.SENSITIVE)
        return replace(self, label=lowered,
                       declassifications=self.declassifications + (decl,))

    def __str__(self) -> str:
        return f"Labeled({self.label}, id={self.id})"


def combine(*labels: DataLabel) -> DataLabel:
    """Join any number of labels into the most restrictive result."""
    out = PUBLIC
    for label in labels:
        out = out.merged_with(label)
    return out


def label_of(value: Any) -> DataLabel:
    """Return the label of ``value`` if it carries one, else PUBLIC."""
    return value.label if isinstance(value, Labeled) else PUBLIC


def unwrap(value: Any) -> Any:
    """Return the underlying value, whether or not it is labelled."""
    return value.value if isinstance(value, Labeled) else value


# ------------------------------------------------------- deep container traversal

#: Attributes never followed when walking an arbitrary object graph. Without this a walk
#: over a dataclass holding a reference to a kernel would traverse the whole process.
_SKIP_ATTRS = frozenset({
    "__dict__", "__weakref__", "__class__", "__module__", "__doc__",
})

_MAX_DEPTH = 24
_MAX_NODES = 20_000


def walk_values(value: Any, *, _depth: int = 0, _seen: set[int] | None = None,
                _budget: list[int] | None = None) -> Iterable[Any]:
    """Yield every leaf value reachable from ``value``.

    Traverses mappings, sequences, sets, dataclasses and plain objects with a ``__dict__``,
    which between them cover the shapes real payloads take: JSON bodies, tool arguments,
    structured model output, MCP parameters and artifact metadata.

    Cycle-safe and bounded. An unbounded walk over an arbitrary object graph is a denial of
    service, and a classifier that hangs is a classifier that gets disabled — so depth and
    node count are capped. Hitting a cap is not silent: ``deep_label_of`` treats a truncated
    walk as a reason to escalate rather than to assume the remainder was clean.
    """
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_NODES]

    if budget[0] <= 0 or _depth > _MAX_DEPTH:
        yield _TRUNCATED
        return
    budget[0] -= 1

    if isinstance(value, Labeled):
        yield value
        yield from walk_values(value.value, _depth=_depth + 1, _seen=seen, _budget=budget)
        return

    if value is None or isinstance(value, (str, bytes, int, float, bool, Enum)):
        yield value
        return

    marker = id(value)
    if marker in seen:
        return
    seen.add(marker)

    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from walk_values(key, _depth=_depth + 1, _seen=seen, _budget=budget)
            yield from walk_values(item, _depth=_depth + 1, _seen=seen, _budget=budget)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from walk_values(item, _depth=_depth + 1, _seen=seen, _budget=budget)
        return

    if is_dataclass(value) and not isinstance(value, type):
        for field_ in fields(value):
            yield from walk_values(getattr(value, field_.name, None), _depth=_depth + 1,
                                   _seen=seen, _budget=budget)
        return

    # Pydantic models and similar: prefer a declared dump over __dict__ when available.
    for dump in ("model_dump", "dict"):
        method = getattr(value, dump, None)
        if callable(method):
            try:
                yield from walk_values(method(), _depth=_depth + 1, _seen=seen,
                                       _budget=budget)
                return
            except Exception:  # pragma: no cover - defensive
                break

    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, Mapping):
        for key, item in attrs.items():
            if key in _SKIP_ATTRS or str(key).startswith("__"):
                continue
            yield from walk_values(item, _depth=_depth + 1, _seen=seen, _budget=budget)
        return

    yield value


class _Truncated:
    """Sentinel marking a walk that hit its depth or node budget."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<truncated>"


_TRUNCATED = _Truncated()


def deep_label_of(value: Any) -> DataLabel:
    """Return the join of every label reachable inside ``value``.

    This is the container half of information flow. ``label_of`` answers "is *this object*
    labelled", which is the wrong question for a payload shaped like
    ``{"query": Labeled(PHI), "options": {...}}`` — the top-level dict is unlabelled while
    the sensitive content sits one level down. Tool arguments, JSON bodies and structured
    output all take that shape, so the top-level answer is nearly always the wrong one.

    A truncated walk escalates to SENSITIVE rather than reporting what it managed to see. An
    attacker who can nest deeply enough to exhaust the budget must not thereby obtain a
    PUBLIC verdict.
    """
    label = PUBLIC
    truncated = False
    for item in walk_values(value):
        if item is _TRUNCATED:
            truncated = True
            continue
        if isinstance(item, Labeled):
            label = label.merged_with(item.label)
    if truncated and label.sensitivity < Sensitivity.SENSITIVE:
        label = label.merged_with(DataLabel(
            Sensitivity.SENSITIVE, categories=("unbounded_structure",),
            rationale=("value nesting exceeded the traversal budget, so its contents could "
                       "not be fully inspected; escalated rather than assumed clean")))
    return label


def unwrap_deep(value: Any) -> Any:
    """Return ``value`` with every nested ``Labeled`` replaced by its underlying value.

    Used at the boundary where a payload is handed to a component that knows nothing about
    labels. The label travels separately, on the ``ExecutionResult``.
    """
    if isinstance(value, Labeled):
        return unwrap_deep(value.value)
    if isinstance(value, Mapping):
        return {unwrap_deep(k): unwrap_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap_deep(v) for v in value]
    if isinstance(value, tuple):
        return tuple(unwrap_deep(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return type(value)(unwrap_deep(v) for v in value)
    return value

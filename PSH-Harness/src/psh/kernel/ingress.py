"""The ingress boundary: every value entering the trusted path is classified here.

This module closes the most serious defect found in v0.1. The gateways were correct, but
they *trusted the caller* to have labelled the data. A reviewer demonstrated the gap:

    broker.call_model("Patient Alice Smith MRN 04851923", public_model, ...)
    # -> label PUBLIC -> allowed -> identifier delivered to the provider

and my own follow-up found a worse variant the review did not name — a caller-supplied
*wrong* label was believed just as readily:

    Labeled(value="...MRN 04851923", label=DataLabel(PUBLIC))   # -> allowed

That second case is why this module re-classifies rather than merely wrapping. A function
that turned raw values into `Labeled` would have closed the first hole and left the second
open, which is worse than either, because the system would then look governed.

The rule implemented here:

    effective label = join(caller-supplied label, freshly computed label)

A caller can *raise* a label — asserting that something is more sensitive than it looks is
always safe and always honoured. A caller cannot lower one. Lowering requires an explicit,
attributable ``Declassification``, which is a different operation with a different audit
record.
"""

from __future__ import annotations

import threading

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..contracts import PolicyDenied
from ..labels import (
    DataLabel, Labeled, Sensitivity, deep_label_of,
)

__all__ = ["IngressGateway", "IngressDecision", "ensure_labeled"]


@dataclass(frozen=True, slots=True)
class IngressDecision:
    """What ingress did to a value, for audit.

    ``escalated`` records the case that matters most: the caller understated the
    sensitivity and ingress corrected it. A system where that count is persistently
    non-zero has callers who are not classifying, which is worth knowing before it becomes
    a leak somewhere ingress does not cover.
    """

    original: Sensitivity
    effective: Sensitivity
    escalated: bool
    reclassified: bool
    origin: str = ""

    @property
    def summary(self) -> str:
        arrow = f"{self.original.name} -> {self.effective.name}"
        return f"{arrow}{' (escalated)' if self.escalated else ''}"


class IngressGateway:
    """Mandatory classification for everything crossing into the trusted path.

    Every gateway — model, tool, delegation, persistence, release — calls ``ensure`` first.
    That is the whole design: one place where "is this classified?" is answered, so the
    answer cannot differ between call sites.
    """

    name = "ingress"

    def __init__(self, classifier: Any, *, audit: Callable[..., Any] | None = None,
                 strict: bool = False) -> None:
        self.classifier = classifier
        self._audit = audit
        #: In strict mode a raw (unlabelled) value is refused outright rather than
        #: classified. Useful for a deployment that wants callers to classify explicitly;
        #: off by default because refusing is not safer than classifying correctly, and a
        #: hard refusal here would push callers toward bypassing ingress entirely.
        self.strict = strict
        self.decisions: list[IngressDecision] = []
        self.escalations = 0
        self.checks = 0
        self._count_lock = threading.Lock()

    def ensure(self, value: Any, *, origin: str = "", require_labeled: bool | None = None,
               ) -> Labeled:
        """Return ``value`` as a ``Labeled`` carrying its effective label.

        Idempotent in the sense that matters: calling ``ensure`` twice yields the same
        label, because the join of a label with itself is itself. It is *not* idempotent in
        the weaker sense of "already Labeled means skip" — that shortcut is precisely the
        bug this module exists to prevent.
        """
        with self._count_lock:
            self.checks += 1
        strict = self.strict if require_labeled is None else require_labeled
        if not hasattr(self, "_authorised_declassifications"):
            self._authorised_declassifications: dict[str, Any] = {}

        if not isinstance(value, Labeled) and strict:
            raise PolicyDenied(
                "ingress is in strict mode and received an unlabelled value; classify it "
                "with kernel.classify() before passing it to the broker")

        supplied = value.label if isinstance(value, Labeled) else None

        # Always compute a fresh label from the UNDERLYING value. Classifying the wrapper
        # would short-circuit: Classifier.classify() returns an already-Labeled value
        # unchanged, which is correct for idempotence but would mean a caller-supplied label
        # is never re-checked — exactly the hole this gateway closes.
        from ..labels import unwrap_deep

        inner = unwrap_deep(value)
        computed = self.classifier.classify(inner, origin=origin).label
        computed = computed.merged_with(deep_label_of(value))

        effective = computed.merged_with(supplied) if supplied is not None else computed

        # Declassification is the one lawful way a label goes DOWN. It is honoured only when
        # every declassification on the value was issued by this kernel (its id is in the
        # registry) — the record itself is data a caller can construct, so its presence
        # proves nothing. Unauthorised records are stripped from consideration and counted.
        declass = tuple(getattr(value, "declassifications", ()) or ())
        if declass:
            authorised = [d for d in declass if d.id in self._authorised_declassifications]
            forged = len(declass) - len(authorised)
            if forged:
                self.forged_declassifications = getattr(self, "forged_declassifications", 0) + forged
                if self._audit is not None:
                    self._audit("declassification_forged", origin=origin, count=forged)
            if authorised and effective.sensitivity > Sensitivity.PUBLIC:
                floor = min(d.to_sensitivity for d in authorised)
                applicable = [d for d in authorised if d.from_sensitivity >= computed.sensitivity]
                if applicable and floor < effective.sensitivity:
                    from ..labels import DataLabel

                    effective = DataLabel(floor, categories=(), shareable=effective.shareable,
                                          rationale=f"declassified ({len(applicable)} authorised record(s))",
                                          classifier=effective.classifier)

        escalated = supplied is not None and effective.sensitivity > supplied.sensitivity
        decision = IngressDecision(
            original=supplied.sensitivity if supplied else Sensitivity.PUBLIC,
            effective=effective.sensitivity, escalated=escalated,
            reclassified=supplied is None, origin=origin)
        self.decisions.append(decision)
        if escalated:
            self.escalations += 1
        if self._audit is not None:
            self._audit("ingress_classified", origin=origin,
                        sensitivity=effective.sensitivity.name, escalated=escalated,
                        reclassified=decision.reclassified,
                        categories=list(effective.categories)[:8])

        if isinstance(value, Labeled):
            # Preserve identity and derivation history; only the label may change, and only
            # upward.
            from dataclasses import replace as _replace
            return _replace(value, label=effective)
        return Labeled(value=value, label=effective, origin=origin)

    def stats(self) -> dict[str, Any]:
        return {"checks": self.checks, "escalations": self.escalations,
                "reclassified": sum(1 for d in self.decisions if d.reclassified),
                "strict": self.strict}


def _authorise(self, record: Any) -> None:
    """Register a kernel-issued declassification so ingress will honour it."""
    if not hasattr(self, "_authorised_declassifications"):
        self._authorised_declassifications = {}
    self._authorised_declassifications[record.id] = record


IngressGateway.authorise_declassification = _authorise  # type: ignore[attr-defined]


def ensure_labeled(gateway: IngressGateway, value: Any, *, origin: str = "") -> Labeled:
    """Module-level convenience wrapper, for call sites holding only the gateway."""
    return gateway.ensure(value, origin=origin)

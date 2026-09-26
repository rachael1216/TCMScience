"""ExecutionResult: nothing crosses a trusted boundary as a raw value.

A reviewer's finding: ``broker.call_tool`` returned the component's plain Python value, so

    PHI input -> tool -> plain dict -> label gone -> next model call sees PUBLIC

Taint survived derivation through ``Labeled.derive`` but not through *execution*, which is
the path every real workflow takes. The fix is that execution returns a labelled result
whose label defaults to the join of its inputs, so a component cannot launder a label by
the ordinary act of returning something.

The default is deliberately conservative: **output inherits input sensitivity.** A tool that
genuinely de-identifies its input must say so through an explicit declassification, which is
attributable, rather than by returning a value the system optimistically treats as clean.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, Sequence, TypeVar

from ..contracts import ArtifactRef, new_id
from ..labels import DataLabel, Labeled, Sensitivity, combine, deep_label_of

__all__ = ["ExecutionResult", "ModelUsage", "ModelCallResult"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Standardised usage accounting for one model call.

    Every provider adapter must produce this. A reviewer showed that without it the budget
    governor counted *calls* while token and cost ceilings stayed at zero — a ceiling not
    fed by real usage is not a ceiling.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def estimate(cls, prompt: str, completion: str, *, usd_per_1k_input: float = 0.0,
                 usd_per_1k_output: float = 0.0) -> "ModelUsage":
        """Conservative local estimate for providers that report nothing.

        Four characters per token underestimates for code and overestimates for prose. It is
        used only when a provider is silent, and ``estimated=True`` records that the number
        is inferred — an estimated ceiling is still a ceiling, but the audit trail should not
        pretend it was measured.
        """
        in_tokens = max(1, len(prompt) // 4)
        out_tokens = max(1, len(completion) // 4)
        return cls(input_tokens=in_tokens, output_tokens=out_tokens,
                   cost_usd=(in_tokens / 1000 * usd_per_1k_input
                             + out_tokens / 1000 * usd_per_1k_output),
                   estimated=True)


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """One model call's output, usage and provenance."""

    content: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    model_id: str = ""
    provider: str = ""
    finish_reason: str = "stop"
    request_id: str = ""
    latency_s: float = 0.0
    label: DataLabel = field(default_factory=DataLabel)

    @property
    def tokens(self) -> int:
        return self.usage.total_tokens


@dataclass(frozen=True, slots=True)
class ExecutionResult(Generic[T]):
    """The labelled outcome of any execution: tool, harness, or model.

    ``label`` is not optional and does not default to PUBLIC. It is computed by the broker
    as the join of the input labels and whatever the output itself classifies as, so the
    conservative direction is the automatic one.
    """

    value: T
    label: DataLabel
    component_id: str = ""
    run_id: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    usage: ModelUsage | None = None
    warnings: tuple[str, ...] = ()
    status: str = "ok"
    id: str = field(default_factory=lambda: new_id("exec"))
    at: float = field(default_factory=time.time)

    @property
    def sensitivity(self) -> Sensitivity:
        return self.label.sensitivity

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_labeled(self) -> Labeled:
        """Return the value as a ``Labeled``, for handing on to another stage."""
        return Labeled(value=self.value, label=self.label, origin=self.component_id)

    @classmethod
    def from_component(cls, value: Any, *, inputs: Sequence[Any], component_id: str,
                       classifier: Any = None, run_id: str = "",
                       **kw: Any) -> "ExecutionResult":
        """Build a result whose label is the join of its inputs and its own content.

        The join is the invariant. A component receiving PHI and returning a summary
        produces a PHI-labelled result even when the summary contains no identifier — the
        same rule that governs ``Labeled.derive``, now applied across the execution
        boundary where it was previously lost.
        """
        from ..contracts import DegradedResult

        status = kw.pop("status", "ok")
        warnings = tuple(kw.pop("warnings", ()) or ())
        if isinstance(value, DegradedResult):
            # The component ran with a documented shortfall. The value is still a value
            # and is labelled like one; the shortfall travels with it as a caveat instead
            # of disappearing into the component's own log.
            warnings += (value.reason or "the component reported a degraded result",)
            status = "degraded"
            value = value.value
        input_label = combine(*[deep_label_of(item) for item in inputs]) if inputs \
            else DataLabel()
        own_label = deep_label_of(value)
        if classifier is not None:
            own_label = own_label.merged_with(classifier.classify(value).label)
        return cls(value=value, label=input_label.merged_with(own_label),
                   component_id=component_id, run_id=run_id, status=status,
                   warnings=warnings, **kw)

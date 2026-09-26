"""Versioned, JSON-serializable scientific annotations on PSH's execution IR.

Declarations describe intended work, not verified results or authority grants.
Evidence provenance and claim support must still pass the runtime release gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Mapping

from ..labels import Sensitivity
from ..runtime.plan import Criterion, InputBinding, Plan, PlanTask, RetryPolicy, TestSpec
from .statistics import StatisticalDesign


class ClaimType(str, Enum):
    CLASSICAL = "classical_attribution"
    TRADITIONAL = "traditional_use"
    MECHANISTIC = "mechanism"
    ASSOCIATION = "association"
    CLINICAL = "clinical_efficacy"
    SAFETY = "safety_signal"


class SideEffect(str, Enum):
    PURE = "pure"
    IDEMPOTENT = "idempotent"
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    COMPENSATABLE = "compensatable"
    NON_REPEATABLE = "non_repeatable"


class Effect(str, Enum):
    LOCAL_READ = "local_read"
    LOCAL_COMPUTE = "local_compute"
    LOCAL_MODEL = "local_model"
    TRUSTED_REMOTE = "trusted_remote"
    PUBLIC_REMOTE = "public_remote"
    PERSIST = "persist"
    USER_OUTPUT = "user_output"


#: Empirical designs: observations of the world. Kept as its own name so a
#: caller asking "is this an empirical study?" does not have to subtract the
#: predictive set by hand — the question `_SUPPORTS` has to get right.
EVIDENCE_DESIGNS = frozenset({
    "classical_text", "expert_consensus", "in_vitro", "animal", "case_report",
    "observational", "randomized_trial", "systematic_review",
})

#: Computational designs: the output of a model, not an observation.
#:
#: Before these were nameable, a network-pharmacology program could not be
#: expressed in this IR at all — the nearest available design was ``in_vitro``,
#: which would have recorded a simulation as a bench experiment. That is the
#: exact confusion this vocabulary exists to prevent, and it is why the set is
#: named separately rather than merged: the distinction is only enforceable once
#: the IR can tell a prediction from a measurement.
PREDICTIVE_DESIGNS = frozenset({
    "in_silico", "network_prediction", "docking", "molecular_dynamics",
    "target_prediction", "pathway_enrichment",
})

#: Every design an ``EvidenceSpec`` may declare.
DESIGNS = EVIDENCE_DESIGNS | PREDICTIVE_DESIGNS


def _array(value: Any) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected an array")
    return tuple(value)


def _known_fields(value: Mapping[str, Any], cls: type) -> None:
    unknown = set(value) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"unknown {cls.__name__} fields: {sorted(unknown)}")


@dataclass(frozen=True)
class EvidenceSpec:
    design: str
    population: str
    intervention: str
    outcome: str
    provenance: tuple[str, ...]
    # Reviews inherit the designs of the underlying studies, not a higher scope.
    underlying_designs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.design not in DESIGNS:
            raise ValueError(f"unknown evidence design: {self.design}")
        if not all(isinstance(x, str) and x.strip() for x in
                   (self.population, self.intervention, self.outcome)):
            raise ValueError("evidence requires population, intervention and outcome")
        if not isinstance(self.provenance, tuple) or not self.provenance or not all(
                isinstance(x, str) and x.strip() for x in self.provenance):
            raise ValueError("evidence requires nonempty provenance references")
        if not isinstance(self.underlying_designs, tuple):
            raise ValueError("underlying_designs must be a tuple")
        if self.design == "systematic_review":
            if not self.underlying_designs or any(
                    d not in DESIGNS - {"systematic_review"}
                    for d in self.underlying_designs):
                raise ValueError("a review must declare its underlying study designs")
        elif self.underlying_designs:
            raise ValueError("underlying_designs only applies to systematic reviews")

    def to_dict(self) -> dict[str, Any]:
        return dict(design=self.design, population=self.population,
                    intervention=self.intervention, outcome=self.outcome,
                    provenance=list(self.provenance),
                    underlying_designs=list(self.underlying_designs))


@dataclass(frozen=True)
class ClaimSpec:
    kind: ClaimType
    population: str
    intervention: str
    outcome: str
    evidence_from: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ClaimType):
            raise ValueError("claim kind must be a ClaimType")
        if not all(isinstance(x, str) and x.strip() for x in
                   (self.population, self.intervention, self.outcome)):
            raise ValueError("claim requires population, intervention and outcome")
        if not isinstance(self.evidence_from, tuple) or not self.evidence_from or not all(
                isinstance(x, str) and x.strip() for x in self.evidence_from):
            raise ValueError("a claim must name its evidence-producing tasks")

    def to_dict(self) -> dict[str, Any]:
        return dict(kind=self.kind.value, population=self.population,
                    intervention=self.intervention, outcome=self.outcome,
                    evidence_from=list(self.evidence_from))


@dataclass(frozen=True)
class ProtocolBinding:
    record_id: str
    fingerprint: str

    def __post_init__(self):
        if (not isinstance(self.record_id, str) or not self.record_id
                or self.record_id != self.record_id.strip()):
            raise ValueError("protocol binding requires a canonical opaque record ID")
        if not isinstance(self.fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("protocol binding requires a lowercase SHA-256 fingerprint")

    def to_dict(self):
        return dict(record_id=self.record_id, fingerprint=self.fingerprint)


@dataclass(frozen=True)
class TaskContract:
    # This is the declared input/data floor; PlanTask.max_label remains a ceiling.
    sensitivity: Sensitivity = Sensitivity.RESEARCH_DEIDENTIFIED
    effects: tuple[Effect, ...] = ()
    side_effect: SideEffect = SideEffect.NON_REPEATABLE
    evidence: EvidenceSpec | None = None
    claim: ClaimSpec | None = None
    statistics: StatisticalDesign | None = None
    protocol_binding: ProtocolBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sensitivity, Sensitivity):
            raise ValueError("sensitivity must be a Sensitivity")
        if not isinstance(self.side_effect, SideEffect):
            raise ValueError("side_effect must be a SideEffect")
        if not isinstance(self.effects, tuple) or not all(
                isinstance(e, Effect) for e in self.effects):
            raise ValueError("effects must be a tuple of Effect values")
        if self.evidence is not None and not isinstance(self.evidence, EvidenceSpec):
            raise ValueError("evidence must be an EvidenceSpec")
        if self.claim is not None and not isinstance(self.claim, ClaimSpec):
            raise ValueError("claim must be a ClaimSpec")
        if self.statistics is not None and not isinstance(self.statistics, StatisticalDesign):
            raise ValueError("statistics must be a StatisticalDesign")
        if self.protocol_binding is not None and not isinstance(self.protocol_binding, ProtocolBinding):
            raise ValueError("protocol_binding must be a ProtocolBinding")

    def to_dict(self) -> dict[str, Any]:
        result = dict(sensitivity=self.sensitivity.name,
                    effects=[e.value for e in self.effects],
                    side_effect=self.side_effect.value,
                    evidence=self.evidence.to_dict() if self.evidence else None,
                    claim=self.claim.to_dict() if self.claim else None)
        # Preserve the wire representation and fingerprints of legacy contracts.
        if self.statistics is not None:
            result["statistics"] = self.statistics.to_dict()
        if self.protocol_binding is not None:
            result["protocol_binding"] = self.protocol_binding.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskContract:
        values = dict(data)
        if values.get("statistics") is not None:
            values["statistics"] = StatisticalDesign.from_dict(values["statistics"])
        if values.get("protocol_binding") is not None:
            values["protocol_binding"] = ProtocolBinding(**values["protocol_binding"])
        values["sensitivity"] = Sensitivity[values.get("sensitivity", "RESEARCH_DEIDENTIFIED")]
        values["effects"] = tuple(Effect(e) for e in _array(values.get("effects", ())))
        values["side_effect"] = SideEffect(values.get("side_effect", "non_repeatable"))
        if values.get("evidence") is not None:
            ev = dict(values["evidence"])
            ev["provenance"] = _array(ev.get("provenance", ()))
            ev["underlying_designs"] = _array(ev.get("underlying_designs", ()))
            values["evidence"] = EvidenceSpec(**ev)
        if values.get("claim") is not None:
            claim = dict(values["claim"])
            claim["kind"] = ClaimType(claim["kind"])
            claim["evidence_from"] = _array(claim.get("evidence_from", ()))
            values["claim"] = ClaimSpec(**claim)
        return cls(**values)


def digest(value: Any) -> str:
    """No repr/default=str: unsupported values cannot produce reliable cache keys."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ScientificProgram:
    plan: Plan
    contracts: Mapping[str, TaskContract] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported ScientificProgram schema_version")
        if not isinstance(self.plan, Plan):
            raise ValueError("program requires a Plan")
        if set(self.contracts) != {t.task_id for t in self.plan.tasks}:
            raise ValueError("contracts must cover exactly the plan's task IDs")
        if not all(isinstance(c, TaskContract) for c in self.contracts.values()):
            raise ValueError("contracts must contain TaskContract values")

    def to_dict(self) -> dict[str, Any]:
        return dict(schema_version=self.schema_version, plan=self.plan.to_dict(),
                    contracts={k: v.to_dict() for k, v in self.contracts.items()})

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScientificProgram:
        if set(data) != {"schema_version", "plan", "contracts"}:
            raise ValueError("expected schema_version, plan and contracts")
        # The legacy Plan reader remains permissive for compatibility. The new
        # scientific frontend rejects misspellings instead of silently losing constraints.
        _known_fields(data["plan"], Plan)
        for task in _array(data["plan"].get("tasks", ())):
            _known_fields(task, PlanTask)
            if "retry" in task:
                _known_fields(task["retry"], RetryPolicy)
            for key in ("dependencies", "capability_requirements", "destinations"):
                if key in task:
                    _array(task[key])
            for binding in _array(task.get("inputs", ())):
                _known_fields(binding, InputBinding)
            for check in _array(task.get("acceptance_tests", ())):
                _known_fields(check, TestSpec)
        for criterion in _array(data["plan"].get("completion_criteria", ())):
            _known_fields(criterion, Criterion)
        return cls(plan=Plan.from_dict(data["plan"]),
                   contracts={k: TaskContract.from_dict(v)
                              for k, v in data["contracts"].items()},
                   schema_version=data["schema_version"])

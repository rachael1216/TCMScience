"""Pure scientific value objects. No kernel, database or workflow dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")


def _texts(value):
    if not isinstance(value, tuple) or not value:
        raise ValueError("expected a nonempty tuple of text")
    for item in value:
        _text(item)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Hypothesis:
    proposition: str
    population: str
    predictions: tuple[str, ...]
    falsifiers: tuple[str, ...]
    alternatives: tuple[str, ...]

    def __post_init__(self):
        _text(self.proposition)
        _text(self.population)
        for value in (self.predictions, self.falsifiers, self.alternatives):
            _texts(value)


@dataclass(frozen=True)
class Protocol:
    primary_endpoint: str
    secondary_endpoints: tuple[str, ...]
    exclusion_criteria: str
    statistical_test: str
    sample_size_assumptions: str
    covariates: tuple[str, ...]
    subgroup_plan: str
    stopping_criteria: str

    def __post_init__(self):
        for name in ("primary_endpoint", "exclusion_criteria", "statistical_test",
                     "sample_size_assumptions", "subgroup_plan", "stopping_criteria"):
            _text(getattr(self, name))
        for value in (self.secondary_endpoints, self.covariates):
            if not isinstance(value, tuple):
                raise ValueError("protocol sequences must be tuples")
            for item in value:
                _text(item)

    @property
    def fingerprint(self):
        return _hash(asdict(self))


@dataclass(frozen=True)
class Observation:
    summary: str
    artifact_refs: tuple[str, ...]
    outcome: str  # observed | negative | inconclusive

    def __post_init__(self):
        _text(self.summary)
        _texts(self.artifact_refs)
        if self.outcome not in {"observed", "negative", "inconclusive"}:
            raise ValueError("unknown observation outcome")


@dataclass(frozen=True)
class Deviation:
    reason: str
    actual_protocol: Protocol

    def __post_init__(self):
        _text(self.reason)
        if not isinstance(self.actual_protocol, Protocol):
            raise ValueError("actual_protocol must be a Protocol")


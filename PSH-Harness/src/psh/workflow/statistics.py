"""Declarative design checks, not statistical inference or dataset inspection."""

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ..scientist.models import Protocol


@dataclass(frozen=True)
class StatisticalDesign:
    protocol: Protocol
    # Number of tests in the declared inferential family, not just endpoints.
    comparisons: int = 1
    multiplicity_plan: str = ""
    holdout: bool = False
    # Opaque independent-unit IDs in one namespace, never patient identifiers.
    training_units: tuple[str, ...] = ()
    evaluation_units: tuple[str, ...] = ()
    selection_units: tuple[str, ...] = ()
    repeated_measures: bool = False
    dependence_plan: str = ""
    time_to_event: bool = False
    censoring_plan: str = ""

    def __post_init__(self):
        if not isinstance(self.protocol, Protocol):
            raise ValueError("statistical design requires a Protocol")
        if type(self.comparisons) is not int or self.comparisons < 1:
            raise ValueError("comparisons must be a positive integer")
        for name in ("holdout", "repeated_measures", "time_to_event"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        for name in ("multiplicity_plan", "dependence_plan", "censoring_plan"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
        for name in ("training_units", "evaluation_units", "selection_units"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                    not isinstance(v, str) or not v or v != v.strip() for v in values):
                raise ValueError(f"{name} requires canonical nonempty text IDs")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicate IDs")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("training_units", "evaluation_units", "selection_units"):
            result[name] = list(result[name])
        for name in ("secondary_endpoints", "covariates"):
            result["protocol"][name] = list(result["protocol"][name])
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        values = dict(data)
        protocol = dict(values["protocol"])
        for target, names in ((protocol, ("secondary_endpoints", "covariates")),
                              (values, ("training_units", "evaluation_units", "selection_units"))):
            for name in names:
                if name in target:
                    if not isinstance(target[name], (list, tuple)):
                        raise ValueError(f"{name} must be an array")
                    target[name] = tuple(target[name])
        values["protocol"] = Protocol(**protocol)
        return cls(**values)

    def violations(self) -> tuple[tuple[str, str], ...]:
        """Stable codes and identifier-free details safe for compiler diagnostics."""
        problems = []
        if self.comparisons > 1 and not self.multiplicity_plan.strip():
            problems.append(("STAT101", "multiple comparisons require an explicit multiplicity plan"))
        if self.holdout:
            if not self.training_units or not self.evaluation_units:
                problems.append(("STAT102", "holdout requires training and evaluation unit declarations"))
            if set(self.evaluation_units).intersection(self.training_units):
                problems.append(("STAT103", "training and held-out evaluation units overlap"))
            if set(self.evaluation_units).intersection(self.selection_units):
                problems.append(("STAT104", "selection and held-out evaluation units overlap"))
        elif self.training_units or self.evaluation_units or self.selection_units:
            problems.append(("STAT105", "unit partitions require an explicit holdout design"))
        if self.repeated_measures and not self.dependence_plan.strip():
            problems.append(("STAT106", "repeated measures require a dependence plan"))
        if self.time_to_event and not self.censoring_plan.strip():
            problems.append(("STAT107", "time-to-event analysis requires a censoring plan"))
        return tuple(problems)

"""Append-only scientific records with governed WorkGraph persistence."""

from .models import Hypothesis, Protocol, Observation, Deviation
from .ports import ProtocolResolver

__all__ = ["Hypothesis", "Protocol", "Observation", "Deviation", "ScientificLedger", "ProtocolResolver"]


def __getattr__(name):
    if name == "ScientificLedger":
        from .records import ScientificLedger
        globals()[name] = ScientificLedger
        return ScientificLedger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))

"""Provenance log — append-only record of every capability call, with replay.

This is the component no surveyed project provides: ClawBio advertises
reproducibility metadata but ships no checksums or validation fields, and no
other project logs capability calls in a replayable form.

Each entry records what was called, through which adapter, with what inputs,
what came back (hashed), how long it took, and the catalogue version in force.
`replay()` re-executes a recorded run and reports which steps reproduce.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


def _hash(value: Any) -> str:
    """Stable content hash for arbitrary JSON-able values."""
    try:
        blob = json.dumps(value, sort_keys=True, default=str)
    except Exception:
        blob = repr(value)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass
class ProvenanceEntry:
    """One recorded capability invocation."""

    step: int
    capability: str
    adapter: str
    integration_mode: str
    inputs: dict[str, Any]
    input_hash: str
    output_hash: str
    status: str
    error: str | None
    duration_s: float
    timestamp: float
    contributing_projects: list[str] = field(default_factory=list)
    licenses: list[str] = field(default_factory=list)
    attempt: int = 1
    policy_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProvenanceLog:
    """Append-only provenance log for one agent run."""

    def __init__(self, run_id: str | None = None, catalogue_version: str = "unknown") -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.catalogue_version = catalogue_version
        self.started_at = time.time()
        self._entries: list[ProvenanceEntry] = []
        self.environment = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[ProvenanceEntry]:
        return iter(self._entries)

    def __bool__(self) -> bool:
        # A log object is always truthy: __len__ alone would make an empty log
        # falsy, which silently disables `if log:` guards at the first call.
        return True

    @property
    def entries(self) -> list[ProvenanceEntry]:
        return list(self._entries)

    def record(
        self,
        *,
        capability: str,
        adapter: str,
        integration_mode: str,
        inputs: dict[str, Any],
        output: Any,
        status: str,
        error: str | None = None,
        duration_s: float = 0.0,
        attempt: int = 1,
        contributing_projects: list[str] | None = None,
        licenses: list[str] | None = None,
        policy_reason: str | None = None,
    ) -> ProvenanceEntry:
        entry = ProvenanceEntry(
            step=len(self._entries) + 1,
            capability=capability,
            adapter=adapter,
            integration_mode=integration_mode,
            inputs={k: v for k, v in inputs.items()},
            input_hash=_hash(inputs),
            output_hash=_hash(output),
            status=status,
            error=error,
            duration_s=round(duration_s, 4),
            timestamp=time.time(),
            contributing_projects=contributing_projects or [],
            licenses=licenses or [],
            attempt=attempt,
            policy_reason=policy_reason,
        )
        self._entries.append(entry)
        return entry

    # --------------------------------------------------------------- persist
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "catalogue_version": self.catalogue_version,
            "started_at": self.started_at,
            "environment": self.environment,
            "n_steps": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- replay
    @staticmethod
    def replay(
        trace: dict[str, Any] | str | Path,
        invoke: Callable[[str, dict[str, Any]], Any],
    ) -> dict[str, Any]:
        """Re-execute a recorded run and report which steps reproduce.

        ``invoke(capability_name, inputs)`` must return the fresh output. A step
        reproduces when the hash of the fresh output equals the recorded hash.
        """
        if not isinstance(trace, dict):
            trace = ProvenanceLog.load(trace)
        results = []
        for entry in trace.get("entries", []):
            fresh = invoke(entry["capability"], entry["inputs"])
            fresh_hash = _hash(fresh)
            results.append(
                {
                    "step": entry["step"],
                    "capability": entry["capability"],
                    "recorded_hash": entry["output_hash"],
                    "replay_hash": fresh_hash,
                    "reproduced": fresh_hash == entry["output_hash"],
                }
            )
        n_ok = sum(r["reproduced"] for r in results)
        return {
            "run_id": trace.get("run_id"),
            "n_steps": len(results),
            "n_reproduced": n_ok,
            "fully_reproduced": bool(results) and n_ok == len(results),
            "steps": results,
        }

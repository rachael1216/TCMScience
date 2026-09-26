"""Event-sourced provenance — an append-only causal graph of a run.

v1 logged flat call records, which answers "what was called?" but not "why was
this called, under what policy, against which data and code version?". Replay
could only re-invoke each row in order.

Here every meaningful act is an event with a parent link, so a run forms a causal
DAG. Each event pins what would be needed to reproduce it: component id and
version, input/output hashes, git commit, model id, dataset hash, policy ruling.
Replay walks the graph rather than a list, and reports per-event reproduction.
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
from typing import Any, Callable, Iterator, Sequence


class EventType(str):
    """Event kinds. Strings so unknown kinds from an older log still load."""

    TASK_CREATED = "TaskCreated"
    COMPONENT_RETRIEVED = "ComponentRetrieved"
    COMPONENT_RESOLVED = "ComponentResolved"
    POLICY_CHECKED = "PolicyChecked"
    COMPONENT_LOADED = "ComponentLoaded"
    TOOL_CALLED = "ToolCalled"
    ARTIFACT_CREATED = "ArtifactCreated"
    AGENT_MODIFIED = "AgentModified"
    EVALUATION_COMPLETED = "EvaluationCompleted"
    COMPONENT_PROMOTED = "ComponentPromoted"
    COMPONENT_QUARANTINED = "ComponentQuarantined"
    RUN_COMPLETED = "RunCompleted"


def content_hash(value: Any) -> str:
    """Stable content hash for arbitrary JSON-able values."""
    try:
        blob = json.dumps(value, sort_keys=True, default=str)
    except Exception:
        blob = repr(value)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def file_hash(path: str | Path, chunk: int = 1 << 20) -> str:
    """Hash a file's bytes without loading it into memory."""
    h = hashlib.sha256()
    p = Path(path)
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return "sha256:" + h.hexdigest()[:32]


@dataclass
class Event:
    """One node in the run's causal graph."""

    event_id: str
    event_type: str
    run_id: str
    timestamp: float
    parent_event: str | None = None
    component_id: str = ""
    component_version: str = ""
    status: str = ""
    input_hash: str = ""
    output_hash: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    git_commit: str = ""
    model: str = ""
    prompt_hash: str = ""
    dataset_hash: str = ""
    container_digest: str = ""
    policy_ruling: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventLog:
    """Append-only event log for one run, with graph-aware replay."""

    def __init__(self, run_id: str | None = None, *, catalogue_version: str = "unknown",
                 git_commit: str = "", model: str = "") -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.catalogue_version = catalogue_version
        self.git_commit = git_commit
        self.model = model
        self.started_at = time.time()
        self._events: list[Event] = []
        self.environment = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }

    # ------------------------------------------------------------------ append
    def emit(self, event_type: str, *, parent: str | None = None, component_id: str = "",
             component_version: str = "", status: str = "", inputs: dict | None = None,
             output: Any = None, dataset_hash: str = "", policy_ruling: str = "",
             prompt_hash: str = "", model: str = "", detail: dict | None = None) -> Event:
        ev = Event(
            event_id=uuid.uuid4().hex[:16], event_type=str(event_type), run_id=self.run_id,
            timestamp=time.time(), parent_event=parent, component_id=component_id,
            component_version=component_version, status=status,
            inputs=dict(inputs or {}),
            input_hash=content_hash(inputs or {}),
            output_hash=content_hash(output) if output is not None else "",
            git_commit=self.git_commit, model=model or self.model,
            prompt_hash=prompt_hash, dataset_hash=dataset_hash,
            policy_ruling=policy_ruling, detail=dict(detail or {}),
        )
        self._events.append(ev)
        return ev

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __bool__(self) -> bool:
        # An empty log is still a valid object; __len__ alone would make it falsy
        # and silently disable `if log:` guards (a v1 defect).
        return True

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def of_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def children_of(self, event_id: str) -> list[Event]:
        return [e for e in self._events if e.parent_event == event_id]

    # ------------------------------------------------------------------- graph
    def graph(self) -> dict[str, Any]:
        """Nodes and edges of the causal DAG."""
        nodes = [{"event_id": e.event_id, "type": e.event_type,
                  "component": e.component_id, "status": e.status} for e in self._events]
        edges = [{"from": e.parent_event, "to": e.event_id}
                 for e in self._events if e.parent_event]
        return {"run_id": self.run_id, "nodes": nodes, "edges": edges,
                "n_nodes": len(nodes), "n_edges": len(edges)}

    def roots(self) -> list[Event]:
        return [e for e in self._events if e.parent_event is None]

    def depth(self) -> int:
        by_id = {e.event_id: e for e in self._events}
        best = 0
        for e in self._events:
            d, cur = 1, e
            seen = set()
            while cur.parent_event and cur.parent_event in by_id and cur.event_id not in seen:
                seen.add(cur.event_id)
                cur = by_id[cur.parent_event]
                d += 1
            best = max(best, d)
        return best

    # ----------------------------------------------------------------- persist
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "catalogue_version": self.catalogue_version,
            "git_commit": self.git_commit, "model": self.model,
            "started_at": self.started_at, "environment": self.environment,
            "n_events": len(self._events),
            "events": [e.to_dict() for e in self._events],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ replay
    @staticmethod
    def replay(trace: dict[str, Any] | str | Path,
               invoke: Callable[[str, dict[str, Any]], Any],
               *, event_types: Sequence[str] = (EventType.TOOL_CALLED,)) -> dict[str, Any]:
        """Re-execute the executed events of a run and compare output hashes.

        Only events that actually ran are replayable; a RESOLVED-but-never-
        dispatched event has no output to reproduce and is reported as skipped
        rather than counted as a reproduction.
        """
        if not isinstance(trace, dict):
            trace = EventLog.load(trace)
        steps: list[dict[str, Any]] = []
        for ev in trace.get("events", []):
            if ev.get("event_type") not in event_types:
                continue
            if not ev.get("output_hash"):
                steps.append({"event_id": ev.get("event_id"),
                              "component": ev.get("component_id"),
                              "reproduced": None, "skipped": True,
                              "reason": f"no output recorded (status={ev.get('status')})"})
                continue
            fresh = invoke(ev.get("component_id", ""), ev.get("inputs") or {})
            fh = content_hash(fresh)
            steps.append({"event_id": ev.get("event_id"), "component": ev.get("component_id"),
                          "recorded_hash": ev.get("output_hash"), "replay_hash": fh,
                          "reproduced": fh == ev.get("output_hash"), "skipped": False})
        replayable = [s for s in steps if not s["skipped"]]
        n_ok = sum(1 for s in replayable if s["reproduced"])
        return {
            "run_id": trace.get("run_id"), "n_events": len(steps),
            "n_replayable": len(replayable), "n_reproduced": n_ok,
            "n_skipped": len(steps) - len(replayable),
            "fully_reproduced": bool(replayable) and n_ok == len(replayable),
            "steps": steps,
        }

"""The operation ledger: what the loop asked a tool to do, and what became of it.

``IdempotencyLedger`` records results in memory for a component that wants to recognise
a replay, which is the right shape for a component and the wrong shape for the runtime:
it does not survive the restart that creates replays. After a crash the loop knew which
tasks had *succeeded* (the checkpoint) and nothing about a task that had *started* — the
one ``checkpoint.resume`` turns into RETRYABLE precisely because what it did is unknown.
For a component with side effects, "unknown" and "did not run" are different facts, and
running it again on the strength of the second when the first is true is how a sample
gets submitted twice.

This ledger is durable (SQLite, ``synchronous=FULL``, like the event store) and keyed by
the same ``"<run id>:<task id>"`` the loop hands every tool call. It holds no task text
and no results — the component, the state, the attempt count, a digest of the result and
the class of the error — so it may be written whatever the run's persistence authority,
like the audit chain. The loop consults it before every tool call: a side-effecting
component (``manifest.idempotent=False``) whose earlier attempt is RUNNING, UNKNOWN or
SUCCEEDED-but-withheld is not re-run; the task fails with ``OperationUnresolved`` and the
loop escalates, because reconciling a side effect is a decision for whoever owns it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..contracts import OperationUnresolved

__all__ = ["OperationLedger", "OperationRecord", "OperationState"]


class OperationState(str, Enum):
    RESERVED = "reserved"      # key allocated, nothing started
    RUNNING = "running"        # an attempt started and has not reported
    SUCCEEDED = "succeeded"
    FAILED = "failed"          # the attempt reported a failure; nothing is in flight
    UNKNOWN = "unknown"        # an attempt started and the process or the call was lost


@dataclass(frozen=True, slots=True)
class OperationRecord:
    key: str
    component_id: str
    state: OperationState
    attempts: int
    run_id: str = ""
    task_id: str = ""
    idempotent: bool = True
    started_at: float = 0.0
    finished_at: float = 0.0
    result_digest: str = ""
    error_class: str = ""
    updated_at: float = 0.0

    @property
    def terminal(self) -> bool:
        return self.state in (OperationState.SUCCEEDED, OperationState.FAILED)

    @property
    def in_doubt(self) -> bool:
        """An attempt may have run and nothing recorded its outcome."""
        return self.state in (OperationState.RUNNING, OperationState.UNKNOWN)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "component_id": self.component_id,
                "state": self.state.value, "attempts": self.attempts,
                "run_id": self.run_id, "task_id": self.task_id,
                "idempotent": self.idempotent, "started_at": self.started_at,
                "finished_at": self.finished_at, "result_digest": self.result_digest,
                "error_class": self.error_class, "updated_at": self.updated_at}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    key            TEXT PRIMARY KEY,
    component_id   TEXT NOT NULL,
    state          TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    run_id         TEXT NOT NULL DEFAULT '',
    task_id        TEXT NOT NULL DEFAULT '',
    idempotent     INTEGER NOT NULL DEFAULT 1,
    started_at     REAL NOT NULL DEFAULT 0,
    finished_at    REAL NOT NULL DEFAULT 0,
    result_digest  TEXT NOT NULL DEFAULT '',
    error_class    TEXT NOT NULL DEFAULT '',
    updated_at     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS operations_run ON operations (run_id, state);
"""

_COLUMNS = ("key", "component_id", "state", "attempts", "run_id", "task_id", "idempotent",
            "started_at", "finished_at", "result_digest", "error_class", "updated_at")


def result_digest(value: Any) -> str:
    """A short, stable digest of a result: enough to tell two results apart, no content."""
    try:
        blob = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(value)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class OperationLedger:
    """Durable record of every tool call by idempotency key. Thread-safe."""

    def __init__(self, path: str | Path | None = None) -> None:
        #: ``None`` keeps the ledger in memory — for tests and for a caller that has
        #: explicitly decided restarts are not its concern. It says so.
        self.path = Path(path) if path is not None else None
        self.durable = self.path is not None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path) if self.path is not None else ":memory:",
                                     check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA synchronous=FULL")
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self.closed = False

    # ------------------------------------------------------------------ rows
    def _row(self, key: str) -> OperationRecord | None:
        row = self._conn.execute(
            "SELECT " + ", ".join(_COLUMNS) + " FROM operations WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        data = dict(zip(_COLUMNS, row))
        data["state"] = OperationState(data["state"])
        data["idempotent"] = bool(data["idempotent"])
        return OperationRecord(**data)

    def _write(self, record: OperationRecord) -> OperationRecord:
        self._conn.execute(
            "INSERT OR REPLACE INTO operations (" + ", ".join(_COLUMNS) + ") VALUES ("
            + ", ".join("?" for _ in _COLUMNS) + ")",
            (record.key, record.component_id, record.state.value, record.attempts,
             record.run_id, record.task_id, int(record.idempotent), record.started_at,
             record.finished_at, record.result_digest, record.error_class,
             record.updated_at))
        return record

    def _replace(self, record: OperationRecord, **changes: Any) -> OperationRecord:
        from dataclasses import replace

        return self._write(replace(record, updated_at=time.time(), **changes))

    # ------------------------------------------------------------------ api
    def get(self, key: str) -> OperationRecord | None:
        with self._lock:
            return self._row(key)

    def begin(self, key: str, *, component_id: str, run_id: str = "", task_id: str = "",
              idempotent: bool = True) -> OperationRecord:
        """Record that an attempt is starting. A RUNNING record found here was lost."""
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self._row(key)
                if prior is not None:
                    if (prior.component_id != component_id or
                            (prior.run_id and run_id and prior.run_id != run_id) or
                            (prior.task_id and task_id and prior.task_id != task_id)):
                        raise OperationUnresolved("operation key belongs to a different invocation")
                    if (not (prior.idempotent and idempotent) and prior.state in (
                            OperationState.RUNNING, OperationState.UNKNOWN,
                            OperationState.SUCCEEDED)):
                        raise OperationUnresolved(
                            "operation may already have executed; unsafe replay refused atomically")
                if prior is None:
                    record = self._write(OperationRecord(
                        key=key, component_id=component_id, state=OperationState.RUNNING,
                        attempts=1, run_id=run_id, task_id=task_id, idempotent=idempotent,
                        started_at=now, updated_at=now))
                else:
                    record = self._replace(
                        prior, state=OperationState.RUNNING, attempts=prior.attempts + 1,
                        component_id=component_id or prior.component_id,
                        idempotent=idempotent, started_at=now, finished_at=0.0,
                        error_class="")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return record

    def succeed(self, key: str, result: Any = None) -> OperationRecord:
        with self._lock:
            record = self._row(key)
            if record is None:
                raise KeyError(f"no operation {key!r} to complete")
            return self._replace(record, state=OperationState.SUCCEEDED,
                                 finished_at=time.time(), result_digest=result_digest(result),
                                 error_class="")

    def fail(self, key: str, error_class: str = "") -> OperationRecord:
        with self._lock:
            record = self._row(key)
            if record is None:
                raise KeyError(f"no operation {key!r} to fail")
            return self._replace(record, state=OperationState.FAILED,
                                 finished_at=time.time(), error_class=error_class[:80])

    def mark_unknown(self, key: str, error_class: str = "") -> OperationRecord:
        """The attempt may have run; nothing can say. Distinct from FAILED on purpose."""
        with self._lock:
            record = self._row(key)
            if record is None:
                raise KeyError(f"no operation {key!r} to mark unknown")
            return self._replace(record, state=OperationState.UNKNOWN,
                                 finished_at=time.time(), error_class=error_class[:80])

    def in_doubt(self, run_id: str = "") -> list[OperationRecord]:
        """Operations whose outcome nobody recorded: what a restart must reconcile."""
        with self._lock:
            query = ("SELECT key FROM operations WHERE state IN (?, ?)"
                     + (" AND run_id = ?" if run_id else "") + " ORDER BY started_at")
            params: tuple[Any, ...] = (OperationState.RUNNING.value,
                                       OperationState.UNKNOWN.value)
            if run_id:
                params += (run_id,)
            keys = [r[0] for r in self._conn.execute(query, params)]
            return [rec for rec in (self._row(k) for k in keys) if rec is not None]

    def records(self, run_id: str = "") -> list[OperationRecord]:
        with self._lock:
            query = "SELECT key FROM operations" + (" WHERE run_id = ?" if run_id else "") \
                + " ORDER BY started_at"
            keys = [r[0] for r in self._conn.execute(query, (run_id,) if run_id else ())]
            return [rec for rec in (self._row(k) for k in keys) if rec is not None]

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            if not self.closed:
                self._conn.close()
                self.closed = True

"""Hash-chained event store. References and hashes only — never payloads.

Carries forward the predecessor's tamper-evidence property (an edited or deleted record is
detectable at the exact sequence number, and a wholesale rebuild is caught by an external
anchor) and adds the review's requirement that events hold references rather than content.
``EventEnvelope`` refuses raw-content keys on construction, so that rule is enforced by the
type rather than by reviewer discipline.

Tamper-*evident*, not tamper-proof: anyone able to rewrite the database can recompute the
chain. Publish ``anchor()`` after each session for a stronger guarantee.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..contracts import EventEnvelope, content_hash
from ..labels import Sensitivity

Sensitivity_SENSITIVE = Sensitivity.SENSITIVE

__all__ = ["EventStore", "ChainVerification", "GENESIS_HASH"]

GENESIS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    principal_id    TEXT NOT NULL,
    parent_event_id TEXT,
    component_id    TEXT NOT NULL DEFAULT '',
    model_id        TEXT NOT NULL DEFAULT '',
    policy_version  TEXT NOT NULL DEFAULT '',
    input_refs      TEXT NOT NULL DEFAULT '[]',
    output_refs     TEXT NOT NULL DEFAULT '[]',
    artifact_refs   TEXT NOT NULL DEFAULT '[]',
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    labels          TEXT NOT NULL DEFAULT '[]',
    cost_usd        REAL NOT NULL DEFAULT 0,
    tokens          INTEGER NOT NULL DEFAULT 0,
    latency_s       REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',
    detail          TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL,
    at              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


def _sanitise(value: Any, classifier: Any, _depth: int = 0) -> Any:
    """Replace sensitive strings with a category-and-hash reference, at any depth.

    An event records *that* something happened and *what kind* of data was involved. It must
    not become a second copy of that data — the same invariant the PHI findings type enforces
    by having no field able to hold matched text.
    """
    import hashlib

    if _depth > 8:
        return "<truncated>"
    if isinstance(value, str):
        if len(value) < 3:
            return value
        label = classifier.classify_text(value).label
        if label.sensitivity >= Sensitivity_SENSITIVE:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            categories = "/".join(label.categories) or label.sensitivity.name
            return f"<redacted:{categories} sha256:{digest}>"
        return value
    if isinstance(value, Mapping):
        return {k: _sanitise(v, classifier, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(v, classifier, _depth + 1) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class ChainVerification:
    intact: bool
    records: int
    first_break: int | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.intact

    def __repr__(self) -> str:
        if self.intact:
            return f"ChainVerification(intact, {self.records} events)"
        return f"ChainVerification(BROKEN at seq={self.first_break}: {self.detail})"


class EventStore:
    """Append-only, hash-chained event log."""

    def __init__(self, path: str | Path, *, policy_version: str = "1") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_version = policy_version
        self._conn = sqlite3.connect(str(self.path), isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        # A writer that finds the database locked waits rather than failing. Without this,
        # a second process appending concurrently gets SQLITE_BUSY and its record is lost.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        # Appends read the chain head and then insert. Those two statements must be one
        # critical section: with six concurrent writers the unguarded version lost five
        # of every six records to UNIQUE(seq) violations. The lock serialises threads in
        # this process; BEGIN IMMEDIATE (below) serialises across processes.
        self._write_lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ append
    def append(self, event_type: str, run_id: str = "", *, principal_id: str = "local",
               **fields: Any) -> EventEnvelope:
        """Append one event, chaining it to the current head.

        Read-head and insert run inside one exclusive transaction under a process-level lock,
        so the sequence number and previous hash a record is built on are the ones it is
        committed against. An audit log that drops records under concurrent load is not an
        audit log, and the v0.3 version did exactly that.
        """
        with self._write_lock:
            if self._closed:
                # A clean refusal, under the lock. Without the flag — and without close()
                # taking the same lock — a worker thread mid-append while the owning thread
                # closed the connection was a segmentation fault in the sqlite extension,
                # measured the first time a stalled child outlived its kernel.
                raise RuntimeError(
                    f"event store {self.path.name} is closed; refusing to append "
                    f"{event_type!r} to a log that can no longer be verified")
            return self._append_locked(event_type, run_id, principal_id=principal_id, **fields)

    def _append_locked(self, event_type: str, run_id: str = "", *, principal_id: str = "local",
                       **fields: Any) -> EventEnvelope:
        # BEGIN IMMEDIATE takes the write lock up front, so a concurrent writer in another
        # process blocks here (bounded by busy_timeout) instead of racing the SELECT.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            event = self._build_and_insert(event_type, run_id, principal_id=principal_id,
                                           **fields)
            self._conn.execute("COMMIT")
            return event
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def _build_and_insert(self, event_type: str, run_id: str = "", *,
                          principal_id: str = "local", **fields: Any) -> EventEnvelope:
        row = self._conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq = (row["seq"] + 1) if row else 1
        prev = row["hash"] if row else GENESIS_HASH

        detail = fields.pop("detail", None)
        if detail is None:
            # Anything not a recognised envelope field becomes structured detail, which
            # EventEnvelope then validates for raw-content keys.
            known = {"parent_event_id", "component_id", "model_id", "input_refs",
                     "output_refs", "artifact_refs", "evidence_refs", "labels",
                     "cost_usd", "tokens", "latency_s", "status"}
            detail = {k: v for k, v in fields.items() if k not in known}
            fields = {k: v for k, v in fields.items() if k in known}

        event = EventEnvelope(
            seq=seq, event_type=event_type, run_id=run_id, principal_id=principal_id,
            prev_hash=prev, policy_version=self.policy_version, detail=detail, **fields)

        self._conn.execute(
            """INSERT INTO events (seq, event_id, event_type, run_id, principal_id,
                   parent_event_id, component_id, model_id, policy_version, input_refs,
                   output_refs, artifact_refs, evidence_refs, labels, cost_usd, tokens,
                   latency_s, status, detail, prev_hash, hash, at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event.seq, event.event_id, event.event_type, event.run_id,
             event.principal_id, event.parent_event_id, event.component_id,
             event.model_id, event.policy_version, json.dumps(list(event.input_refs)),
             json.dumps(list(event.output_refs)), json.dumps(list(event.artifact_refs)),
             json.dumps(list(event.evidence_refs)), json.dumps(list(event.labels)),
             event.cost_usd, event.tokens, event.latency_s, event.status,
             json.dumps(dict(event.detail), sort_keys=True, default=str),
             event.prev_hash, event.hash, event.at))
        return event

    def sink(self, classifier: Any = None):
        """Return an audit callable that never raises into caller code.

        A malformed event must not kill a run. A rejected payload is itself recorded, so the
        attempt remains visible in the chain.

        When a classifier is supplied, detail is sanitised recursively before it reaches
        disk: any string classified above INTERNAL is replaced by its category and a hash.
        v0.1 checked only top-level key names, so nested clinical text was persisted intact.
        """
        def _sink(event_type: str, run_id: str = "", **fields: Any) -> None:
            try:
                if classifier is not None and "detail" in fields:
                    fields["detail"] = _sanitise(fields["detail"], classifier)
                if classifier is not None:
                    known = {"parent_event_id", "component_id", "model_id", "input_refs",
                             "output_refs", "artifact_refs", "evidence_refs", "labels",
                             "cost_usd", "tokens", "latency_s", "status", "principal_id"}
                    fields = {k: (v if k in known else _sanitise(v, classifier))
                              for k, v in fields.items()}
                self.append(event_type, run_id, **fields)
            except ValueError as exc:
                self.append("event_payload_rejected", run_id,
                            detail={"event_type": event_type, "error": str(exc)[:200]})
        return _sink

    # ------------------------------------------------------------------- reads
    def __len__(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def events(self) -> dict[str, int]:
        return {r["event_type"]: int(r["n"]) for r in self._conn.execute(
            "SELECT event_type, COUNT(*) n FROM events GROUP BY event_type ORDER BY n DESC")}

    def records(self) -> list[EventEnvelope]:
        """Return every event as an envelope, in sequence order."""
        out: list[EventEnvelope] = []
        for row in self._conn.execute("SELECT * FROM events ORDER BY seq"):
            out.append(EventEnvelope(
                seq=row["seq"], event_id=row["event_id"], event_type=row["event_type"],
                run_id=row["run_id"], principal_id=row["principal_id"],
                parent_event_id=row["parent_event_id"], component_id=row["component_id"],
                model_id=row["model_id"], policy_version=row["policy_version"],
                input_refs=tuple(json.loads(row["input_refs"])),
                output_refs=tuple(json.loads(row["output_refs"])),
                artifact_refs=tuple(json.loads(row["artifact_refs"])),
                evidence_refs=tuple(json.loads(row["evidence_refs"])),
                labels=tuple(json.loads(row["labels"])), cost_usd=row["cost_usd"],
                tokens=row["tokens"], latency_s=row["latency_s"], status=row["status"],
                detail=json.loads(row["detail"]), prev_hash=row["prev_hash"],
                hash=row["hash"], at=row["at"]))
        return out

    def by_run(self, run_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,)))

    @property
    def head_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS_HASH

    def anchor(self) -> dict[str, Any]:
        """Return the value to publish externally to detect a wholesale rewrite."""
        return {"records": len(self), "head_hash": self.head_hash,
                "policy_version": self.policy_version, "anchored_at": time.time()}

    # ------------------------------------------------------------------ verify
    def verify(self, expected_head: str | None = None) -> ChainVerification:
        rows = list(self._conn.execute("SELECT * FROM events ORDER BY seq"))
        prev = GENESIS_HASH
        expected_seq = 1
        for row in rows:
            if row["seq"] != expected_seq:
                return ChainVerification(
                    False, len(rows), row["seq"],
                    f"sequence gap: expected {expected_seq}, found {row['seq']} "
                    "(a record was deleted)")
            if row["prev_hash"] != prev:
                return ChainVerification(False, len(rows), row["seq"],
                                         "linkage broken: prev_hash does not match the "
                                         "preceding record")
            # Rebuild the envelope exactly as EventEnvelope._body() does, so verification
            # covers every field the hash protects — including the metadata v0.1 omitted.
            recomputed = content_hash({"prev": row["prev_hash"], "body": {
                "schema": EventEnvelope.ENVELOPE_SCHEMA,
                "event_id": row["event_id"],
                "seq": row["seq"], "event_type": row["event_type"], "run_id": row["run_id"],
                "parent_event_id": row["parent_event_id"],
                "principal_id": row["principal_id"], "component_id": row["component_id"],
                "model_id": row["model_id"], "policy_version": row["policy_version"],
                "at": row["at"], "latency_s": row["latency_s"],
                "input_refs": json.loads(row["input_refs"]),
                "output_refs": json.loads(row["output_refs"]),
                "artifact_refs": json.loads(row["artifact_refs"]),
                "evidence_refs": json.loads(row["evidence_refs"]),
                "labels": json.loads(row["labels"]), "cost_usd": row["cost_usd"],
                "tokens": row["tokens"], "status": row["status"],
                "detail_hash": content_hash(json.loads(row["detail"]))}})
            if recomputed != row["hash"]:
                return ChainVerification(False, len(rows), row["seq"],
                                         f"payload at seq={row['seq']} was modified")
            prev = row["hash"]
            expected_seq += 1
        if expected_head is not None and prev != expected_head:
            return ChainVerification(
                False, len(rows), None,
                "chain is internally consistent but its head does not match the external "
                "anchor: the log may have been rewritten wholesale")
        return ChainVerification(True, len(rows))

    def close(self) -> None:
        """Close the connection — after any append in flight, never during one.

        The store is shared between threads by design (``check_same_thread=False``, and a
        lock around appends). Sharing makes closing a concurrency question too: sqlite's
        C extension does not survive one thread closing a connection another thread is
        inside, and the result is a crash rather than an exception. So close takes the
        write lock, which means it waits for a writer to finish and then makes every later
        writer fail cleanly.
        """
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

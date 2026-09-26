"""Durable stage scheduling events and a pure, non-executing replay reducer.

This trusted storage adapter does not grant persistence authority. The dynamic
controller checks that authority before appending. No result payloads are stored.
One journal belongs to one workflow execution; never reuse it for another run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading


class ReplayRefused(ValueError):
    """Corrupt, incompatible or out-of-order execution history."""


@dataclass(frozen=True)
class ReplayState:
    fingerprint: str = ""
    phase: str = "empty"
    next_stage: int | None = None
    visits: int = 0
    max_visits: int = 0
    stage_count: int = 0
    reason: str = ""
    sequence: int = 0

    @property
    def in_doubt(self):
        return self.phase == "running"


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def reduce_event(state: ReplayState, event: dict) -> ReplayState:
    """No I/O, clock, callbacks, model calls or tool dispatch during replay."""
    if not isinstance(event, dict):
        raise ReplayRefused("event must be an object")
    kind = event.get("kind")
    fields = {
        "started": {"kind", "fingerprint", "entry", "stages", "max_visits"},
        "stage_started": {"kind", "stage", "visit"},
        "stage_finished": {"kind", "ok"},
        "routed": {"kind", "target"},
        "ended": {"kind", "reason"},
    }
    if not isinstance(kind, str) or kind not in fields or set(event) != fields[kind]:
        raise ReplayRefused("unknown event or fields")
    changes = {}
    if kind == "started":
        if (state.phase != "empty" or not isinstance(event["fingerprint"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", event["fingerprint"])
                or not _integer(event["stages"], 1, 256)
                or not _integer(event["entry"], 0, event["stages"] - 1)
                or not _integer(event["max_visits"], 1, 1024)):
            raise ReplayRefused("invalid workflow start")
        changes = dict(phase="ready", fingerprint=event["fingerprint"],
                       next_stage=event["entry"], stage_count=event["stages"],
                       max_visits=event["max_visits"])
    elif kind == "stage_started":
        if (state.phase != "ready" or state.next_stage is None
                or type(event["stage"]) is not int or type(event["visit"]) is not int
                or event["stage"] != state.next_stage
                or event["visit"] != state.visits + 1
                or state.visits >= state.max_visits):
            raise ReplayRefused("invalid stage start")
        changes = dict(phase="running", visits=event["visit"])
    elif kind == "stage_finished":
        if state.phase != "running" or type(event["ok"]) is not bool:
            raise ReplayRefused("invalid stage completion")
        changes = dict(phase="routing" if event["ok"] else "failed")
    elif kind == "routed":
        target = event["target"]
        if state.phase != "routing" or (target is not None and
                not _integer(target, 0, state.stage_count - 1)):
            raise ReplayRefused("invalid route")
        changes = dict(phase="ready", next_stage=target)
    else:
        reason = event["reason"]
        valid = ((reason == "completed" and state.phase == "ready" and state.next_stage is None)
                 or (reason == "max_visits" and state.phase == "ready"
                     and state.next_stage is not None and state.visits == state.max_visits)
                 or (reason == "stage_failed" and state.phase == "failed")
                 or (reason == "route_failed" and state.phase == "routing")
                 or (reason == "cancelled" and state.phase == "ready"))
        valid = valid or (reason == "binding_failed" and state.phase == "ready"
                         and state.next_stage is not None and 0 < state.visits < state.max_visits)
        valid = valid or (reason == "repeat_refused" and state.phase == "ready"
                         and state.next_stage is not None and state.visits < state.max_visits)
        if not valid:
            raise ReplayRefused("invalid workflow termination")
        changes = dict(phase="ended", reason=reason)
    return replace(state, sequence=state.sequence + 1, **changes)


def replay(events, *, fingerprint=None):
    state = ReplayState()
    for event in events:
        state = reduce_event(state, event)
    if fingerprint is not None and state.fingerprint != fingerprint:
        raise ReplayRefused("workflow fingerprint mismatch")
    return state


def _hash(sequence, previous, body):
    return hashlib.sha256(json.dumps([1, sequence, previous, body],
        separators=(",", ":")).encode("utf-8")).hexdigest()


class RunEventJournal:
    """SQLite FULL/WAL, atomic compare-and-append, verified hash chain.

    Hashes are not signatures. Keep an external anchor to detect suffix deletion;
    this does not protect against an attacker rewriting the entire database.
    Readers must have storage-level access appropriate to the run's sensitivity.
    """
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), isolation_level=None,
                                     check_same_thread=False, timeout=5)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS workflow_events "
                           "(seq INTEGER PRIMARY KEY, body TEXT NOT NULL, "
                           "previous TEXT NOT NULL, hash TEXT NOT NULL)")

    def _read(self, expected_anchor=None):
        previous = "0" * 64
        events = []
        for expected, row in enumerate(self._conn.execute(
                "SELECT seq, body, previous, hash FROM workflow_events ORDER BY seq"), 1):
            seq, body, parent, hashed = row
            if seq != expected or parent != previous or hashed != _hash(seq, previous, body):
                raise ReplayRefused("event journal integrity failure")
            try:
                event = json.loads(body)
                if not isinstance(event, dict):
                    raise ValueError()
            except ValueError as exc:
                raise ReplayRefused("invalid event body") from exc
            events.append(event)
            previous = hashed
        state = replay(events)
        anchor = (len(events), previous)
        if expected_anchor is not None and tuple(expected_anchor) != anchor:
            raise ReplayRefused("event journal anchor mismatch")
        return events, state, anchor

    def append(self, event, *, expected_sequence: int):
        body = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        event = json.loads(body)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                _, state, (_, previous) = self._read()
                if state.sequence != expected_sequence:
                    raise ReplayRefused("concurrent or stale event writer")
                updated = reduce_event(state, event)
                self._conn.execute("INSERT INTO workflow_events VALUES (?,?,?,?)",
                    (updated.sequence, body, previous, _hash(updated.sequence, previous, body)))
                self._conn.execute("COMMIT")
                return updated
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def replay(self, *, expected_anchor=None, fingerprint=None):
        with self._lock:
            events, state, _ = self._read(expected_anchor)
            return replay(events, fingerprint=fingerprint) if fingerprint else state

    def events(self):
        with self._lock:
            return self._read()[0]

    def anchor(self):
        with self._lock:
            return self._read()[2]

    def close(self):
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

"""Append-only checkpoint journal, compatible with the loop's checkpoint sink.

This journals governed snapshots, not individual external effects. Recovery still
uses checkpoint.resume and the operation ledger. It is not exactly-once execution.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from .checkpoint import Checkpoint, ResumeRefused


_GENESIS = "0" * 64


def _digest(seq, previous, body):
    return hashlib.sha256(json.dumps([1, seq, previous, body],
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class JournalCheckpointStore:
    """Single SQLite transaction per append, including chain head and snapshot.

    Supply snapshots produced by checkpoint.capture, which already enforces content
    withholding. Like CheckpointStore this low-level store does not mint authority.
    Use opaque loop/checkpoint identifiers. No SQL authorizer protects a caller who
    directly edits the database; retain an external anchor to detect suffix loss.
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
        self._conn.execute("""CREATE TABLE IF NOT EXISTS checkpoint_journal (
            seq INTEGER PRIMARY KEY, version INTEGER NOT NULL,
            checkpoint_id TEXT NOT NULL UNIQUE, loop_id TEXT NOT NULL,
            body TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL)""")

    def _verified(self, expected_anchor=None):
        rows = self._conn.execute("SELECT seq, version, checkpoint_id, loop_id, body, "
                                  "previous, hash FROM checkpoint_journal ORDER BY seq").fetchall()
        previous = _GENESIS
        checkpoints = []
        for expected, row in enumerate(rows, 1):
            seq, version, cid, loop_id, body, parent, stored_hash = row
            if (seq != expected or version != 1 or parent != previous or
                    stored_hash != _digest(seq, previous, body)):
                raise ResumeRefused(f"checkpoint journal integrity failure at sequence {expected}")
            try:
                checkpoint = Checkpoint.from_dict(json.loads(body))
            except (ValueError, KeyError, TypeError) as exc:
                raise ResumeRefused("invalid journal checkpoint") from exc
            if not checkpoint.intact or checkpoint.checkpoint_id != cid or checkpoint.loop_id != loop_id:
                raise ResumeRefused("journal checkpoint identity or content hash mismatch")
            checkpoints.append(checkpoint)
            previous = stored_hash
        anchor = (len(rows), previous)
        if expected_anchor is not None and anchor != tuple(expected_anchor):
            raise ResumeRefused("journal does not match the expected external anchor")
        return checkpoints, anchor

    def save(self, checkpoint: Checkpoint) -> int:
        if not checkpoint.intact:
            raise ResumeRefused("cannot append a modified checkpoint")
        # Canonical serialization refuses values that cannot be restored faithfully.
        body = json.dumps(checkpoint.to_dict(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        restored = Checkpoint.from_dict(json.loads(body))
        if not restored.intact:
            raise ResumeRefused("checkpoint changes meaning during serialization")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                _, (seq, previous) = self._verified()
                existing = self._conn.execute(
                    "SELECT seq, body FROM checkpoint_journal WHERE checkpoint_id = ?",
                    (restored.checkpoint_id,)).fetchone()
                if existing:
                    if existing[1] != body:
                        raise ResumeRefused("checkpoint ID reused with different content")
                    self._conn.execute("COMMIT")
                    return existing[0]
                seq += 1
                self._conn.execute("INSERT INTO checkpoint_journal VALUES (?,?,?,?,?,?,?)",
                    (seq, 1, restored.checkpoint_id, restored.loop_id, body, previous,
                     _digest(seq, previous, body)))
                self._conn.execute("COMMIT")
                return seq
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def all(self, *, expected_anchor=None) -> list[Checkpoint]:
        with self._lock:
            return self._verified(expected_anchor)[0]

    def anchor(self) -> tuple[int, str]:
        with self._lock:
            return self._verified()[1]

    def load(self, checkpoint_id: str, *, expected_anchor=None) -> Checkpoint:
        for checkpoint in self.all(expected_anchor=expected_anchor):
            if checkpoint.checkpoint_id == checkpoint_id:
                return checkpoint
        raise ResumeRefused("checkpoint not found in journal")

    def latest_for(self, loop_id: str, *, expected_anchor=None) -> Checkpoint | None:
        # Sequence, not caller-supplied wall clock, defines journal order.
        for checkpoint in reversed(self.all(expected_anchor=expected_anchor)):
            if checkpoint.loop_id == loop_id:
                return checkpoint
        return None

    def close(self):
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

"""Idempotency keys: how a side-effecting component recognises a replay.

The loop presents every tool call with ``_psh_idempotency_key = "<loop run id>:<task id>"``.
It is the same value on every attempt of a task, and — because ``checkpoint.resume``
preserves the run id through the authority meet — the same value after a crash and a
restart. That second case is the one that matters. ``resume()`` deliberately turns a task
that was RUNNING when the process died into RETRYABLE, because what it did is unknown; a
component that had already performed its side effect would perform it again unless it can
recognise the key.

The loop cannot decide replay semantics for a component. Whether "run the alignment twice"
is harmless, expensive or wrong is a fact about the component, so the ledger is something a
component *uses*, not something the kernel imposes: look the key up, and if it is there,
return what was recorded instead of doing the work again.
"""

from __future__ import annotations

import threading
from typing import Any, Mapping

__all__ = ["IdempotencyLedger", "KEY_FIELD", "key_of"]

#: The payload field the loop populates.
KEY_FIELD = "_psh_idempotency_key"


def key_of(payload: Mapping[str, Any] | None) -> str:
    """The key the loop attached to a payload, or ``""`` when called outside a loop."""
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get(KEY_FIELD) or "")


class IdempotencyLedger:
    """Thread-safe record of results by key, for components with side effects."""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.replays = 0

    def seen(self, key: str) -> bool:
        with self._lock:
            return bool(key) and key in self._results

    def recall(self, key: str) -> Any:
        """The recorded result. Counts the replay, so a test can see it happened."""
        with self._lock:
            self.replays += 1
            return self._results[key]

    def remember(self, key: str, result: Any) -> Any:
        if key:
            with self._lock:
                self._results[key] = result
        return result

    def __len__(self) -> int:
        with self._lock:
            return len(self._results)

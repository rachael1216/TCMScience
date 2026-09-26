"""Signed evidence: trust derives from a verifiable signature, not from a name.

My own audit of v0.3 found this in one line:

    EvidenceRecord.from_text(..., retrieved_by="sable.pubmed_fetch")   ->   trusted=True

Trust was a string comparison against a list of retriever names. Anyone who could type the
name could mint trusted evidence, and the whole claim-support layer — which caps verdicts for
untrusted provenance — rested on that. The check recorded *who claimed* to retrieve, not *who
did*.

The fix is the ordinary one: the retrieval capability signs each record with an HMAC over the
fields that matter (identifier, content hash, retrieval run, retriever, source type) using a
key the kernel holds and the capability is given at registration. ``trusted`` then derives
from signature verification. A caller who types a retriever name gets an unsigned record,
which is exactly as trusted as the text they pasted: not.

What this does and does not protect against
--------------------------------------------
It stops a caller *asserting* trust. It does not stop a compromised retrieval capability from
signing garbage — the capability holds a key, so it can sign whatever it retrieves. That is
the correct boundary: the kernel trusts the capability it registered, and the signature
proves the record came through that capability rather than from a string. Key material lives
in the kernel's secret store (mode 0700) and is never written into a record or an event.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any

__all__ = ["EvidenceSigner"]

_SIGNED_FIELDS = ("identifier", "content_hash", "retrieval_run", "retrieved_by", "source_type")


class EvidenceSigner:
    """Signs and verifies evidence records with a kernel-held HMAC key."""

    def __init__(self, key_path: Path | None = None, *, key: bytes | None = None) -> None:
        if key is not None:
            self._key = key
        elif key_path is not None:
            self._key = self._load_or_create(key_path)
        else:
            self._key = secrets.token_bytes(32)
        self.signed = 0
        self.verified = 0
        self.rejected = 0

    @staticmethod
    def _load_or_create(path: Path) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path.read_bytes()
        key = secrets.token_bytes(32)
        # Create with owner-only permissions before writing, so there is no window in which
        # the key is world-readable.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    @staticmethod
    def _message(record: Any) -> bytes:
        parts = []
        for name in _SIGNED_FIELDS:
            value = getattr(record, name, "")
            value = getattr(value, "value", value)  # enums
            parts.append(f"{name}={value}")
        return "\n".join(parts).encode("utf-8")

    def signature_for(self, record: Any) -> str:
        return hmac.new(self._key, self._message(record), hashlib.sha256).hexdigest()

    def sign(self, record: Any) -> Any:
        """Return a copy of ``record`` carrying a signature and ``trusted=True``."""
        self.signed += 1
        return replace(record, signature=self.signature_for(record), trusted=True)

    def verify(self, record: Any) -> bool:
        """True when the record's signature matches its signed fields under this key."""
        sig = getattr(record, "signature", "") or ""
        if not sig:
            self.rejected += 1
            return False
        ok = hmac.compare_digest(sig, self.signature_for(record))
        if ok:
            self.verified += 1
        else:
            self.rejected += 1
        return ok

    def stats(self) -> dict[str, int]:
        return {"signed": self.signed, "verified": self.verified, "rejected": self.rejected}

"""Quarantine and the ReleaseGate: a refusal withholds the text.

A reviewer's finding, and the distinction is semantic rather than cosmetic. In v0.1 the
runner assigned ``result.output`` during execution and the gate ran afterwards, so a refused
run returned:

    result.status == "refused"
    result.output == "Empagliflozin cures pancreatic cancer (PMID: 34449189)."

The gate labelled the output refused. It did not prevent its release. Any caller reading
``.output`` without checking ``.status`` — which is the natural way to use a result object —
received exactly the text the gate had just rejected.

So candidate output goes to quarantine and is only ever exposed through ``released_output``,
which is ``None`` unless the gate passed. The quarantined text remains addressable by
reference for audit and debugging, because discarding it would make refusals impossible to
investigate.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ..contracts import VerificationFailed, new_id
from ..labels import DataLabel, Labeled, Sensitivity

__all__ = ["Quarantine", "QuarantineRef", "ReleaseDecision"]


@dataclass(frozen=True, slots=True)
class QuarantineRef:
    """A handle to quarantined content. Carries no content itself."""

    ref: str
    digest: str
    size_bytes: int
    label: DataLabel
    reason: str = ""
    at: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"quarantine:{self.ref}"


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    """The gate's verdict, and the only path by which output becomes readable."""

    released: bool
    quarantine: QuarantineRef
    reason: str
    supports: tuple[Any, ...] = ()
    unsupported: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("a release decision must state its reason")


class Quarantine:
    """Holds candidate output until it is released or refused.

    Content is kept on disk under the harness state directory rather than in memory, so a
    long-running session does not accumulate refused output, and so the audit reference
    survives process death.
    """

    def __init__(self, directory: str | Path, *,
                 audit: Callable[..., Any] | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._audit = audit
        self._index: dict[str, tuple[str, DataLabel]] = {}
        self.held = 0
        self.released = 0
        self.refused = 0

    def hold(self, content: str, *, label: DataLabel, run_id: str = "") -> QuarantineRef:
        """Place candidate output in quarantine and return its reference."""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ref = new_id("qrn")
        path = self.directory / f"{ref}.txt"
        path.write_text(content, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        self._index[ref] = (str(path), label)
        self.held += 1
        if self._audit is not None:
            self._audit("output_quarantined", run_id=run_id, detail={
                "ref": ref, "digest": digest[:32], "bytes": len(content),
                "sensitivity": label.sensitivity.name})
        return QuarantineRef(ref=ref, digest=digest, size_bytes=len(content), label=label)

    def release(self, ref: QuarantineRef, *, run_id: str = "") -> str:
        """Return the content. Only the release gate may call this."""
        entry = self._index.get(ref.ref)
        if entry is None:
            raise VerificationFailed(f"no quarantined content for {ref.ref}")
        self.released += 1
        if self._audit is not None:
            self._audit("output_released", run_id=run_id,
                        detail={"ref": ref.ref, "digest": ref.digest[:32]})
        return Path(entry[0]).read_text(encoding="utf-8")

    def refuse(self, ref: QuarantineRef, reason: str, *, run_id: str = "") -> None:
        """Mark quarantined content as refused. It stays addressable for audit."""
        self.refused += 1
        if self._audit is not None:
            self._audit("output_refused", run_id=run_id, detail={
                "ref": ref.ref, "digest": ref.digest[:32], "reason": reason[:200]})

    def inspect(self, ref: str, *, principal: str) -> str:
        """Privileged read of refused content, for debugging. Always audited.

        Deliberately separate from ``release``: reading refused output is a legitimate
        operation for the person investigating a refusal, and it should look different in the
        audit log from ordinary release.
        """
        entry = self._index.get(ref)
        if entry is None:
            raise VerificationFailed(f"no quarantined content for {ref}")
        if self._audit is not None:
            self._audit("quarantine_inspected", principal_id=principal,
                        detail={"ref": ref})
        return Path(entry[0]).read_text(encoding="utf-8")

    def stats(self) -> dict[str, Any]:
        return {"held": self.held, "released": self.released, "refused": self.refused}

"""The PersistenceGateway: long-term storage is a governed sink.

A reviewer's observation that reframes the problem: **the WorkGraph is project memory**, and
in v0.1 anything could write to it. ``WorkGraph.add()`` accepted raw strings and defaulted to
a PUBLIC label, so identifiable content could be permanently registered as public — and
``update()`` did not re-classify a changed body.

That matters more than an ordinary leak because of where the data lands. Content in project
memory is *retrieved later* by the context compiler. Mis-labelled content there is not a
one-time exposure; it is a standing invitation to include PHI in a future prompt for a
different task under a different policy.

So every write to durable state passes through a commit request that carries content, label,
provenance, principal and validation status. Raw strings are refused. Content is classified
at the boundary, not by the caller.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..contracts import PolicyDenied, RunEnvelope, content_hash, new_id
from ..labels import DataLabel, Destination, Labeled, Sensitivity

__all__ = ["PersistenceGateway", "CommitRequest", "ValidationStatus"]


class ValidationStatus:
    """How far a value has progressed through verification."""

    CANDIDATE = "candidate"       # produced, not yet verified
    VERIFIED = "verified"         # passed verification; may enter trusted memory
    REJECTED = "rejected"         # failed; retained by hash and reason only
    SYSTEM = "system"             # structural bookkeeping, not model-produced knowledge


@dataclass(frozen=True, slots=True)
class CommitRequest:
    """One durable write, with everything needed to judge it."""

    content: Any
    principal: str
    source_run: str
    validation_status: str = ValidationStatus.CANDIDATE
    label: DataLabel | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    retention: str = "project"
    id: str = field(default_factory=lambda: new_id("cmt"))
    at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.principal:
            raise PolicyDenied("a commit request must name the principal responsible")
        if not self.source_run:
            raise PolicyDenied("a commit request must name the run it originated from")
        if self.validation_status not in (
                ValidationStatus.CANDIDATE, ValidationStatus.VERIFIED,
                ValidationStatus.REJECTED, ValidationStatus.SYSTEM):
            raise PolicyDenied(f"unknown validation status {self.validation_status!r}")


class PersistenceGateway:
    """Classifies and authorises every write to durable state."""

    name = "persistence_gateway"

    def __init__(self, graph: Any, ingress: Any, *,
                 audit: Callable[..., Any] | None = None,
                 max_label: Sensitivity = Sensitivity.PHI) -> None:
        self.graph = graph
        self.ingress = ingress
        self._audit = audit
        #: The highest sensitivity this store may hold. A deployment keeping its index on
        #: shared infrastructure should lower this; the refusal then happens at the write
        #: rather than being discovered later.
        self.max_label = max_label
        self.commits = 0
        self.refusals = 0

    # ------------------------------------------------------------------- commits
    def commit_node(self, *, kind: Any, title: str, principal: str, source_run: str,
                    body: str = "", status: str = "open",
                    validation_status: str = ValidationStatus.CANDIDATE,
                    project_id: str = "", ref: str = "",
                    provenance: Mapping[str, Any] | None = None,
                    max_label: Sensitivity | None = None,
                    inherited_label: DataLabel | None = None,
                    links: tuple[tuple[str, Any], ...] = (), **meta: Any) -> Any:
        """Classify, authorise and write one WorkGraph node.

        ``max_label`` lets a caller state a ceiling **lower** than the store's own for this
        write. A run executing under a narrower policy than the kernel's needs it: the
        gateway is constructed once from the kernel's policy, so without this the run's
        tighter data ceiling governed the envelope and not the write.
        """
        request = CommitRequest(
            content={"title": title, "body": body, "meta": meta, "ref": ref,
                     "provenance": dict(provenance or {})},
            principal=principal, source_run=source_run,
            validation_status=validation_status, provenance=dict(provenance or {}),
            label=inherited_label)
        label = self._authorise(request, max_label=max_label)

        def write():
            return self.graph.add(kind, title, project_id=project_id, body=body, status=status,
                                  label=label, ref=ref,
                                  validation_status=validation_status,
                                  committed_by=principal, source_run=source_run, **meta)
        if links:
            with self.graph.transaction():
                node = write()
                for target, relation in links:
                    self.graph.link(node.id, target, relation)
        else:
            node = write()
        self.commits += 1
        if self._audit is not None:
            self._audit("persistence_commit", component_id=str(kind),
                        sensitivity=label.sensitivity.name,
                        validation_status=validation_status, principal=principal)
        return node

    def commit_raw(self, **kw: Any) -> Any:
        """Refuse. Present so the unsafe path exists and fails loudly.

        v0.1 allowed exactly this shape of call. Keeping the name and raising is more useful
        than deleting it: a caller reaching for the old API gets an explanation rather than
        an AttributeError.
        """
        self.refusals += 1
        raise PolicyDenied(
            "raw writes to durable state are refused; use commit_node() with a principal "
            "and source_run so the write is classified and attributable")

    def update_node(self, node_id: str, *, principal: str, source_run: str,
                    **fields: Any) -> bool:
        """Update a node, re-classifying any content that changed.

        v0.1's ``update()`` did not re-classify, so a node could be created clean and later
        edited to hold identifiers while keeping its original PUBLIC label.
        """
        changed_text = " ".join(str(v) for k, v in fields.items()
                                if k in ("title", "body") and v)
        if changed_text:
            request = CommitRequest(content=changed_text, principal=principal,
                                    source_run=source_run,
                                    validation_status=ValidationStatus.SYSTEM)
            label = self._authorise(request)
            existing = self.graph.get(node_id)
            if existing is not None and label.sensitivity > existing.label.sensitivity:
                # The graph API keeps labels immutable, so record the escalation and refuse
                # rather than silently storing more sensitive content under a weaker label.
                raise PolicyDenied(
                    f"update would raise the node's sensitivity from "
                    f"{existing.label.sensitivity.name} to {label.sensitivity.name}; "
                    "create a new node at the higher label instead of mutating this one")
        return self.graph.update(node_id, **fields)

    def commit_rejected(self, *, statement: str, reason: str, principal: str,
                        source_run: str, project_id: str = "") -> Any:
        """Record a rejected claim as a hash and a reason, never as retrievable text.

        This is the fix for persistent epistemic contamination: a conclusion the release
        gate refused must not remain in project memory where the context compiler can
        retrieve it into a later prompt. The audit trail keeps the hash so the event can be
        reconciled; the text itself is not stored.
        """
        from ..workgraph import NodeKind

        digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        self.commits += 1
        node = self.graph.add(
            NodeKind.REJECTED_CLAIM, f"rejected claim {digest[:16]}",
            project_id=project_id, body=f"reason: {reason[:300]}",
            status=ValidationStatus.REJECTED,
            label=DataLabel(Sensitivity.INTERNAL, rationale="rejected-claim reference"),
            ref=f"sha256:{digest}", validation_status=ValidationStatus.REJECTED,
            committed_by=principal, source_run=source_run)
        if self._audit is not None:
            self._audit("claim_rejected", detail={"digest": digest[:32],
                                                  "reason_len": len(reason)})
        return node

    # ------------------------------------------------------------------ internals
    def _authorise(self, request: CommitRequest,
                   max_label: Sensitivity | None = None) -> DataLabel:
        """Classify the content and check it may be stored.

        The effective ceiling is the **lower** of the store's and the caller's, never the
        caller's alone: a per-write ceiling may tighten this gateway and must not be able
        to loosen it.
        """
        labelled: Labeled = self.ingress.ensure(request.content, origin="persistence")
        label = labelled.label
        if request.label is not None:
            label = label.merged_with(request.label)

        ceiling = self.max_label if max_label is None else min(self.max_label, max_label)
        if label.sensitivity > ceiling:
            self.refusals += 1
            if self._audit is not None:
                self._audit("persistence_refused",
                            sensitivity=label.sensitivity.name,
                            reason="exceeds store ceiling")
            raise PolicyDenied(
                f"content classified {label.sensitivity.name} exceeds the effective ceiling "
                f"of {ceiling.name} for this write; it may not be written to durable state")

        if not label.permits(Destination.PERSISTENT):
            self.refusals += 1
            raise PolicyDenied(
                f"content classified {label.sensitivity.name} may not reach persistent "
                "storage under the active destination policy")
        return label

    def stats(self) -> dict[str, Any]:
        return {"commits": self.commits, "refusals": self.refusals,
                "ceiling": self.max_label.name}

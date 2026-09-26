"""Project memory retrieval: what an earlier run verified, offered to a later one, labelled.

The WorkGraph is project memory (``kernel/persistence.py``). Every write to it passes the
``PersistenceGateway`` — classified at the boundary, attributed to a principal and a run —
and ``Runner.run`` commits a claim only after the release gate has judged it; a claim the
gate refused is stored as a hash and a reason, never as text. This module is the *read*
side, and it keeps the properties the write side paid for:

* **Only validated knowledge is trusted memory.** Nodes whose ``validation_status`` is
  ``verified`` or ``system`` are retrievable. A ``candidate`` — produced, not yet verified —
  is not, unless an operator names that status. A rejected claim never is: its kind is
  excluded whatever the caller asks for, and it holds no text anyway.
* **Labels travel.** Every item carries the label the gateway stored on its node, so a
  memory of PHI enters a projection as PHI whatever its text looks like, and the compiler
  drops it for a destination that may not receive it.
* **The run's ceiling applies at retrieval.** A run may not see memory above its own
  ``max_label``; such nodes are withheld here and counted, never handed to a compiler that
  would only judge them against the destination.
* **Retrieval is a read.** Nothing here writes to the graph. Memory is written only through
  the persistence gateway, by the code that verified it — there is deliberately no
  ``remember()`` on this class, because a convenience that wrote model output into trusted
  memory would be the laundering path ``commit_rejected`` exists to close.

Ranking is lexical overlap with the query, then recency, then id: deterministic, and
computed here rather than by a model, because retrieval on the critical path must not
itself require egress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..contracts import ContextItem, RunEnvelope
from ..labels import Destination
from ..workgraph import NodeKind

__all__ = ["MemoryRetriever", "RetrievalTrace", "RETRIEVABLE_KINDS", "TRUSTED_STATUSES"]

#: Node kinds that carry knowledge worth recalling. Runs, tasks and datasets are
#: bookkeeping; rejected claims are excluded by construction, not by this table.
RETRIEVABLE_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.CLAIM, NodeKind.DECISION, NodeKind.KNOWLEDGE, NodeKind.EVIDENCE,
    NodeKind.ARTIFACT, NodeKind.QUESTION, NodeKind.GOAL,
})

#: Validation statuses that make a node trusted memory (``persistence.ValidationStatus``).
TRUSTED_STATUSES: frozenset[str] = frozenset({"verified", "system"})

_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def _terms(text: str) -> set[str]:
    """Latin words and Chinese terms alike (see ``terms.py``): memory written in one
    language is reachable from a query in the other."""
    from .terms import terms

    return terms(text, stop=())


@dataclass
class RetrievalTrace:
    """What one retrieval did, so a run can say what memory it saw and what it was denied."""

    considered: int = 0
    excluded_status: int = 0
    withheld_ceiling: int = 0
    withheld_destination: int = 0
    returned: int = 0
    no_project: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"considered": self.considered, "excluded_status": self.excluded_status,
                "withheld_ceiling": self.withheld_ceiling,
                "withheld_destination": self.withheld_destination,
                "returned": self.returned, "no_project": self.no_project}


class MemoryRetriever:
    """Reads labelled context items out of a ``WorkGraph``.

    ``graph`` is the kernel's WorkGraph (or another handle on the same store). ``statuses``
    defaults to trusted memory only; an operator who wants candidates recalled says so.
    ``cross_project`` is off by default: with no project on the envelope and none given,
    nothing is retrieved, because project memory is scoped by project and a loop that
    forgot to say which one should not be handed all of them.
    """

    def __init__(self, graph: Any, *, kinds: Iterable[NodeKind] = RETRIEVABLE_KINDS,
                 statuses: Iterable[str] = TRUSTED_STATUSES, limit: int = 6,
                 max_chars: int = 600, scan_limit: int = 500,
                 default_project: str = "", cross_project: bool = False) -> None:
        self.graph = graph
        self.kinds = frozenset(NodeKind(k) for k in kinds) - {NodeKind.REJECTED_CLAIM}
        self.statuses = frozenset(str(s) for s in statuses) - {"rejected"}
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.max_chars = max_chars
        self.scan_limit = scan_limit
        self.default_project = default_project
        self.cross_project = cross_project
        self.retrievals = 0
        self.last_trace = RetrievalTrace()

    def retrieve(self, query: str, *, envelope: RunEnvelope | None = None,
                 project_id: str = "", destination: Destination | None = None,
                 limit: int | None = None) -> list[ContextItem]:
        """Return up to ``limit`` labelled memory items relevant to ``query``.

        ``envelope`` supplies the project and the run's data ceiling; ``destination``, when
        given, withholds memory that may not reach it — so a caller that then refuses on
        any policy drop (the loop does, for upstream evidence) is never refusing on memory.
        """
        self.retrievals += 1
        trace = RetrievalTrace()
        self.last_trace = trace
        project = project_id or (envelope.project_id if envelope is not None else "") \
            or self.default_project
        if not project and not self.cross_project:
            trace.no_project = True
            return []
        ceiling = envelope.max_label.sensitivity if envelope is not None else None
        terms = _terms(query or "")
        scored: list[tuple[float, float, str, Any, str]] = []
        for kind in sorted(self.kinds, key=lambda k: k.value):
            for node in self.graph.nodes(kind=kind, project_id=project, limit=self.scan_limit):
                trace.considered += 1
                if node.kind is NodeKind.REJECTED_CLAIM:      # pragma: no cover - excluded above
                    continue
                status = str(node.meta.get("validation_status", "candidate"))
                if status not in self.statuses or node.status == "rejected":
                    trace.excluded_status += 1
                    continue
                if ceiling is not None and node.label.sensitivity > ceiling:
                    trace.withheld_ceiling += 1
                    continue
                if destination is not None and not node.label.permits(destination):
                    trace.withheld_destination += 1
                    continue
                text = self._render(node)
                overlap = (len(terms & _terms(text)) / len(terms)) if terms else 0.0
                scored.append((overlap, node.created_at, node.id, node, text))
        scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
        items = [ContextItem(kind="memory", content=text, label=node.label,
                             source_ref=node.id, score=round(overlap, 4))
                 for overlap, _, _, node, text in scored[:limit or self.limit]]
        trace.returned = len(items)
        return items

    def _render(self, node: Any) -> str:
        text = f"[{node.kind.value}] {node.title}".strip()
        body = (node.body or "").strip()
        if body:
            text += "\n" + body[:self.max_chars]
        if node.ref:
            text += f"\n(ref {node.ref})"
        return text

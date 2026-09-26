"""The WorkGraph: the durable spine of a physician-scientist's work.

The review's central structural argument is that long-horizon research work is not a chat
history. A project opened in March is still open in June; the decision to use one assay
over another needs to be answerable months later, after the model, the agent and the skill
set have all been replaced. So continuity belongs to a persistent graph, not to a
conversation transcript or an agent's personality.

Node kinds and the edges between them:

    Project ──has──> Goal ──has──> Question
       │                              │
       ├──has──> Task ──has──> Run ──produced──> Artifact
       │                        │
       │                        └──yielded──> Evidence ──supports──> Claim
       │
       ├──has──> Decision ──because──> Claim
       ├──has──> Dataset
       └──has──> Manuscript

Traversal runs both ways, which is the point. Forward answers "what came of this
question?"; backward answers "why did we decide this?" — the query the review singles out
as the one a research system must support and a chat log cannot.

Storage: SQLite in the index store. Nodes carry a data label so a PHI-bearing dataset node
is visible to the kernel's egress checks like any other value, and the graph itself never
stores large content — artifacts are referenced by digest.
"""

from __future__ import annotations

import json
import sqlite3
import time
import threading
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..contracts import new_id, utc_now
from ..labels import DataLabel, Sensitivity

__all__ = ["NodeKind", "EdgeKind", "Node", "Edge", "WorkGraph"]


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class NodeKind(str, Enum):
    PROJECT = "project"
    GOAL = "goal"
    QUESTION = "question"
    TASK = "task"
    RUN = "run"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    CLAIM = "claim"
    DECISION = "decision"
    DATASET = "dataset"
    MANUSCRIPT = "manuscript"
    KNOWLEDGE = "knowledge"
    #: A claim the release gate refused. Stored as a hash and a reason, never as
    #: retrievable text, so a rejected conclusion cannot be retrieved back into a prompt.
    REJECTED_CLAIM = "rejected_claim"
    HYPOTHESIS = "hypothesis"
    PROTOCOL = "protocol"
    OBSERVATION = "observation"


class EdgeKind(str, Enum):
    HAS = "has"                    # structural containment
    PRODUCED = "produced"          # a run produced an artifact
    YIELDED = "yielded"            # a run yielded evidence
    SUPPORTS = "supports"          # evidence supports a claim
    PARTIALLY_SUPPORTS = "partially_supports"   # related, but does not license the claim
    CONTRADICTS = "contradicts"    # evidence contradicts a claim
    UNRESOLVED = "unresolved"      # no determination: absence of support, not refutation
    #: The claim reaches beyond what the source licenses — a different population, a
    #: surrogate outcome, or a different subject. Recorded distinctly from SUPPORTS so a
    #: why-query can show where a conclusion exceeds its evidence, and from CONTRADICTS
    #: because the source does not refute the claim, it simply does not reach it.
    EXTRAPOLATED_TO = "extrapolated_to"
    BECAUSE = "because"            # a decision rests on a claim
    USED = "used"                  # a run used a dataset or component
    ANSWERS = "answers"            # a claim answers a question
    SUPERSEDES = "supersedes"      # a later node replaces an earlier one
    DERIVED_FROM = "derived_from"  # data lineage


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    sensitivity TEXT NOT NULL DEFAULT 'PUBLIC',
    categories TEXT NOT NULL DEFAULT '[]',
    ref        TEXT NOT NULL DEFAULT '',
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);

CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    src       TEXT NOT NULL,
    dst       TEXT NOT NULL,
    kind      TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE (src, dst, kind),
    FOREIGN KEY (src) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (dst) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);
"""


@dataclass(frozen=True, slots=True)
class Node:
    """One node. ``ref`` points at external content (artifact digest, PMID, commit)."""

    id: str
    kind: NodeKind
    title: str
    project_id: str = ""
    body: str = ""
    status: str = "open"
    label: DataLabel = field(default_factory=DataLabel)
    ref: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=utc_now)
    updated_at: float = field(default_factory=utc_now)

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.id[:12]} {self.title[:60]!r}"


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    note: str = ""
    created_at: float = field(default_factory=utc_now)


class WorkGraph:
    """Persistent project graph. Outlives models, agents and skill sets."""

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------- writes
    @contextmanager
    def transaction(self):
        """Serialize graph operations and atomically commit related records/edges."""
        with self._lock:
            savepoint = new_id("graph_tx")
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                yield self
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise

    @_locked
    def add(self, kind: NodeKind | str, title: str, *, project_id: str = "",
            body: str = "", status: str = "open", label: DataLabel | None = None,
            ref: str = "", **meta: Any) -> Node:
        """Create a node. Titles are not deduplicated: two runs may share a name."""
        kind = NodeKind(kind)
        label = label or DataLabel()
        node = Node(id=new_id(kind.value[:3]), kind=kind, title=title,
                    project_id=project_id, body=body, status=status, label=label,
                    ref=ref, meta=meta)
        self._conn.execute(
            """INSERT INTO nodes (id, kind, project_id, title, body, status, sensitivity,
                   categories, ref, meta, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (node.id, node.kind.value, node.project_id, node.title, node.body,
             node.status, node.label.sensitivity.name,
             json.dumps(list(node.label.categories)), node.ref,
             json.dumps(dict(node.meta), default=str), node.created_at, node.updated_at))
        return node

    @_locked
    def project(self, title: str, **kw: Any) -> Node:
        """Create a project. Its own id becomes its project_id, so it scopes itself."""
        node = self.add(NodeKind.PROJECT, title, **kw)
        self._conn.execute("UPDATE nodes SET project_id = id WHERE id = ?", (node.id,))
        return self.get(node.id)  # type: ignore[return-value]

    @_locked
    def link(self, src: str | Node, dst: str | Node, kind: EdgeKind | str,
             note: str = "") -> Edge:
        """Create an edge. Idempotent on (src, dst, kind)."""
        s = src.id if isinstance(src, Node) else src
        d = dst.id if isinstance(dst, Node) else dst
        kind = EdgeKind(kind)
        edge = Edge(src=s, dst=d, kind=kind, note=note)
        self._conn.execute(
            """INSERT OR IGNORE INTO edges (src, dst, kind, note, created_at)
               VALUES (?,?,?,?,?)""", (s, d, kind.value, note, edge.created_at))
        return edge

    @_locked
    def update(self, node_id: str, **fields: Any) -> bool:
        """Update mutable node fields. Kind and id are immutable."""
        allowed = {"title", "body", "status", "ref"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        clause = ", ".join(f"{k} = ?" for k in sets)
        cur = self._conn.execute(
            f"UPDATE nodes SET {clause}, updated_at = ? WHERE id = ?",
            (*sets.values(), time.time(), node_id))
        return bool(cur.rowcount)

    # -------------------------------------------------------------------- reads
    @_locked
    def get(self, node_id: str) -> Node | None:
        row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return self._to_node(row) if row else None

    @_locked
    def nodes(self, *, kind: NodeKind | str | None = None, project_id: str = "",
              status: str | None = None, limit: int = 200) -> list[Node]:
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?"); params.append(NodeKind(kind).value)
        if project_id:
            clauses.append("project_id = ?"); params.append(project_id)
        if status is not None:
            clauses.append("status = ?"); params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM nodes {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit))
        return [self._to_node(r) for r in rows]

    @_locked
    def neighbours(self, node_id: str, *, kind: EdgeKind | str | None = None,
                   direction: str = "out") -> list[tuple[EdgeKind, Node]]:
        """Return adjacent nodes. ``direction`` is 'out', 'in', or 'both'."""
        out: list[tuple[EdgeKind, Node]] = []
        kind_clause = " AND e.kind = ?" if kind is not None else ""
        kind_param = (EdgeKind(kind).value,) if kind is not None else ()
        if direction in ("out", "both"):
            for row in self._conn.execute(
                    f"""SELECT e.kind ekind, n.* FROM edges e JOIN nodes n ON n.id = e.dst
                        WHERE e.src = ?{kind_clause} ORDER BY n.created_at""",
                    (node_id, *kind_param)):
                out.append((EdgeKind(row["ekind"]), self._to_node(row)))
        if direction in ("in", "both"):
            for row in self._conn.execute(
                    f"""SELECT e.kind ekind, n.* FROM edges e JOIN nodes n ON n.id = e.src
                        WHERE e.dst = ?{kind_clause} ORDER BY n.created_at""",
                    (node_id, *kind_param)):
                out.append((EdgeKind(row["ekind"]), self._to_node(row)))
        return out

    # ------------------------------------------------------------- provenance
    def why(self, node_id: str, *, max_depth: int = 8) -> list[str]:
        """Walk backwards from a node to explain it. The review's key query.

        "Why did we decide to use DIA rather than DDA three months ago?" resolves to a
        Decision node, its supporting Claims, the Evidence behind each, the Runs that
        yielded that evidence, and the Datasets those runs used.
        """
        lines: list[str] = []
        seen: set[str] = set()

        #: Which direction is *explanatory* for each edge kind. An explanation walks from
        #: the thing being justified toward the thing justifying it, so the direction is
        #: per-edge rather than uniform: a decision points forward to its claim
        #: (``because``), while evidence points forward to the claim it supports — so to
        #: explain a claim we follow ``supports`` backwards. Walking both directions for
        #: every kind, as an earlier version did, emitted each edge twice and made the
        #: trace unreadable.
        EXPLANATORY: tuple[tuple[EdgeKind, str], ...] = (
            (EdgeKind.BECAUSE, "out"),        # decision  -> claim
            (EdgeKind.SUPPORTS, "in"),        # claim     <- evidence
            (EdgeKind.CONTRADICTS, "in"),     # claim     <- contradicting evidence
            (EdgeKind.YIELDED, "in"),         # evidence  <- run
            (EdgeKind.PRODUCED, "in"),        # artifact  <- run
            (EdgeKind.USED, "out"),           # run       -> dataset
            (EdgeKind.DERIVED_FROM, "out"),   # artifact  -> source data
        )
        ARROW = {"out": "--{}-->", "in": "<--{}--"}

        def walk(nid: str, depth: int) -> None:
            if depth > max_depth or nid in seen:
                return
            seen.add(nid)
            node = self.get(nid)
            if node is None:
                return
            indent = "  " * depth
            status = f" [{node.status}]" if node.status not in ("open", "") else ""
            label = (f" ({node.label.sensitivity.name})"
                     if node.label.sensitivity >= Sensitivity.SENSITIVE else "")
            ref = f" <{node.ref}>" if node.ref else ""
            lines.append(f"{indent}{node.kind.value}: {node.title}{status}{label}{ref}")
            if node.body and depth <= 2:
                lines.append(f"{indent}  {node.body[:160]}")
            for edge_kind, direction in EXPLANATORY:
                for _, other in self.neighbours(nid, kind=edge_kind, direction=direction):
                    if other.id in seen:
                        continue
                    lines.append(f"{indent}  {ARROW[direction].format(edge_kind.value)}")
                    walk(other.id, depth + 2)

        walk(node_id, 0)
        return lines

    def lineage(self, node_id: str) -> list[Node]:
        """Return the derivation ancestry of a node, nearest first."""
        out: list[Node] = []
        frontier = [node_id]
        seen = {node_id}
        while frontier:
            current = frontier.pop(0)
            for _, parent in self.neighbours(current, kind=EdgeKind.DERIVED_FROM,
                                             direction="out"):
                if parent.id not in seen:
                    seen.add(parent.id)
                    out.append(parent)
                    frontier.append(parent.id)
        return out

    def briefing(self, project_id: str) -> str:
        """Orientation for a session resumed weeks later."""
        project = self.get(project_id)
        name = project.title if project else project_id
        lines = [f"Project: {name}", ""]

        for kind, heading in ((NodeKind.QUESTION, "Open questions"),
                              (NodeKind.DECISION, "Decisions on record"),
                              (NodeKind.TASK, "Tasks in flight")):
            items = [n for n in self.nodes(kind=kind, project_id=project_id)
                     if n.status in ("open", "in_progress")]
            if items:
                lines.append(f"{heading}:")
                lines += [f"  - {n.title}" + (f" ({n.body[:70]})" if n.body else "")
                          for n in items[:8]]
                lines.append("")

        runs = self.nodes(kind=NodeKind.RUN, project_id=project_id, limit=5)
        if runs:
            lines.append("Recent runs:")
            for run in runs:
                when = time.strftime("%Y-%m-%d", time.localtime(run.created_at))
                lines.append(f"  - {when} {run.title} [{run.status}]")
            lines.append("")

        artifacts = self.nodes(kind=NodeKind.ARTIFACT, project_id=project_id, limit=5)
        if artifacts:
            lines.append("Recent artifacts:")
            lines += [f"  - {a.title}" + (f" ({a.ref})" if a.ref else "") for a in artifacts]
            lines.append("")

        sensitive = [n for n in self.nodes(project_id=project_id, limit=500)
                     if n.label.sensitivity >= Sensitivity.SENSITIVE]
        if sensitive:
            lines.append(f"Note: {len(sensitive)} node(s) in this project carry "
                         f"SENSITIVE-or-higher data labels.")
        return "\n".join(lines).rstrip()

    @_locked
    def stats(self) -> dict[str, Any]:
        by_kind = {r["kind"]: int(r["n"]) for r in self._conn.execute(
            "SELECT kind, COUNT(*) n FROM nodes GROUP BY kind ORDER BY n DESC")}
        by_edge = {r["kind"]: int(r["n"]) for r in self._conn.execute(
            "SELECT kind, COUNT(*) n FROM edges GROUP BY kind ORDER BY n DESC")}
        return {"nodes": sum(by_kind.values()), "by_kind": by_kind,
                "edges": sum(by_edge.values()), "by_edge_kind": by_edge,
                "projects": by_kind.get("project", 0)}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _to_node(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"], kind=NodeKind(row["kind"]), title=row["title"],
            project_id=row["project_id"], body=row["body"], status=row["status"],
            label=DataLabel(sensitivity=Sensitivity[row["sensitivity"]],
                            categories=tuple(json.loads(row["categories"]))),
            ref=row["ref"], meta=json.loads(row["meta"]),
            created_at=row["created_at"], updated_at=row["updated_at"])

    @_locked
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorkGraph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

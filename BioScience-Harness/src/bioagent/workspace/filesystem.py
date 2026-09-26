"""File workspace with an enforced trust boundary.

Everything an agent may evolve is a file: agents, skills, memory, context,
workflows, proposals. Everything that constrains the agent — policy, kernel
config, audit — is outside its reach.

The boundary is enforced in code: `Workspace.write()` refuses paths in the
immutable plane, so "the agent may not edit policy" is a check rather than a
convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

#: Agent-writable subtrees.
MUTABLE_DIRS = ("agents", "skills", "memory", "context", "workflows", "proposals",
                "artifacts", "runs", "data")

#: Never agent-writable, even with an explicit path.
IMMUTABLE_DIRS = ("kernel", "policies", "executors", "security", "license_policy",
                  "secret_store", "permission_engine", "audit")


class TrustBoundaryError(PermissionError):
    """Raised when a write targets the immutable plane."""


@dataclass
class Workspace:
    """A file-based workspace with a mutable/immutable split."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # ------------------------------------------------------------------ layout
    def init(self) -> "Workspace":
        for d in MUTABLE_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for d in ("memory/episodic", "memory/semantic", "memory/project", "memory/user",
                  "context", "proposals"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for d in IMMUTABLE_DIRS[:2]:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("__pycache__/\n*.pyc\n.DS_Store\n", encoding="utf-8")
        return self

    # ------------------------------------------------------------------ guards
    def _classify(self, rel: str | Path) -> tuple[Path, str]:
        """Resolve a path and return (absolute, first component RELATIVE TO ROOT).

        The v2 defect: `is_mutable()` looked at the *literal* first component of
        the request, so "memory/../kernel/policy.yaml" was classified as a write
        to memory/ while landing in kernel/. Classification must use the path as
        it will actually land — after `..`, `.` and symlinks are resolved.
        """
        root = self.root.resolve()
        raw = Path(rel)
        candidate = raw if raw.is_absolute() else root / raw
        # resolve() follows symlinks on the parts that exist, which is exactly
        # the behaviour an attacker exploits — so classify on the resolved path.
        resolved = candidate.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise TrustBoundaryError(f"path escapes workspace: {rel}")
        try:
            top = resolved.relative_to(root).parts[0]
        except (ValueError, IndexError):
            top = ""
        return resolved, top

    def is_mutable(self, rel: str | Path) -> bool:
        """True if the path, *as it will land*, is inside a mutable subtree."""
        try:
            _, top = self._classify(rel)
        except TrustBoundaryError:
            return False
        return top in MUTABLE_DIRS and top not in IMMUTABLE_DIRS

    def resolve(self, rel: str | Path) -> Path:
        """Resolve a workspace-relative path, refusing traversal outside root."""
        return self._classify(rel)[0]

    # ------------------------------------------------------------------- io
    def write(self, rel: str | Path, content: str, *, agent: bool = True) -> Path:
        """Write a workspace file. Agent writes to the immutable plane are refused.

        The check is performed on the resolved landing path, and the parent is
        re-verified after creation so a symlinked intermediate directory cannot
        redirect the write.
        """
        resolved, top = self._classify(rel)
        if agent and not (top in MUTABLE_DIRS and top not in IMMUTABLE_DIRS):
            raise TrustBoundaryError(
                f"{rel} lands in {top or '<root>'}/ which is outside the mutable plane "
                f"({', '.join(MUTABLE_DIRS[:4])}, ...); agent-authored changes are not "
                "permitted there")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after mkdir: an existing symlinked parent resolves differently.
        landed = resolved.parent.resolve() / resolved.name
        if landed != resolved:
            raise TrustBoundaryError(f"{rel} would be redirected to {landed}")
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def read(self, rel: str | Path) -> str:
        return self.resolve(rel).read_text(encoding="utf-8", errors="replace")

    def exists(self, rel: str | Path) -> bool:
        return self.resolve(rel).exists()

    def list(self, rel: str | Path = ".") -> list[str]:
        base = self.resolve(rel)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

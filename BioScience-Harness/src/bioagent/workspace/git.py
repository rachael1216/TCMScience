"""Git layer — commit, diff, branch and rollback of agent-authored change.

Self-modification is only safe if it is reviewable and reversible. Every agent
edit lands as a commit on a branch, so a bad change is a `revert` rather than an
incident, and `diff()` shows exactly what the agent did.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git operation fails."""


@dataclass
class GitLayer:
    """Thin, explicit wrapper over git for one workspace."""

    root: Path
    author_name: str = "bioagent"
    author_email: str = "bioagent@localhost"

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def _run(self, *args: str, check: bool = True) -> str:
        if not self.available():
            raise GitError("git is not installed")
        proc = subprocess.run(["git", *args], cwd=str(self.root),  # noqa: S603
                              capture_output=True, text=True, check=False)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()[:400]}")
        return proc.stdout

    # -------------------------------------------------------------------- api
    def init(self) -> "GitLayer":
        if not (self.root / ".git").exists():
            self._run("init", "-q")
            self._run("config", "user.name", self.author_name)
            self._run("config", "user.email", self.author_email)
            self._run("config", "commit.gpgsign", "false")
        return self

    def commit(self, message: str, paths: list[str] | None = None) -> str:
        self._run("add", *(paths or ["-A"]))
        status = self._run("status", "--porcelain")
        if not status.strip():
            return ""
        self._run("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self._run("rev-parse", "HEAD").strip()

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def branch(self, name: str) -> str:
        self._run("checkout", "-q", "-b", name)
        return name

    def checkout(self, ref: str) -> str:
        self._run("checkout", "-q", ref)
        return ref

    def diff(self, ref: str = "HEAD", paths: list[str] | None = None) -> str:
        args = ["diff", ref]
        if paths:
            args += ["--", *paths]
        return self._run(*args, check=False)

    def diff_between(self, a: str, b: str) -> str:
        return self._run("diff", a, b, check=False)

    def revert_to(self, commit: str) -> str:
        """Hard-reset the working tree to a commit (rollback)."""
        self._run("reset", "-q", "--hard", commit)
        return self.head()

    def log(self, n: int = 10) -> list[dict]:
        out = self._run("log", f"-{n}", "--pretty=format:%H|%s|%ai", check=False)
        rows = []
        for line in out.strip().split("\n"):
            if not line:
                continue
            sha, _, rest = line.partition("|")
            msg, _, when = rest.rpartition("|")
            rows.append({"commit": sha, "message": msg, "date": when})
        return rows

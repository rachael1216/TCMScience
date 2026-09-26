"""Harness configuration and the four-store layout. Frozen module.

The store split is the review's correction to "everything mutable is a file". Authored
state belongs in Git because diffing and reviewing it is the point; runtime state does
not, because a per-tool-call commit history is noise that makes the useful history
unreadable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .contracts import Budget, RiskTier
from .labels import Destination, Sensitivity

__all__ = ["PSHConfig", "default_state_dir"]


def default_state_dir() -> Path:
    return Path(os.environ.get("PSH_HOME", Path.home() / ".psh")).expanduser()


@dataclass(frozen=True, slots=True)
class PSHConfig:
    """Top-level settings.

    Defaults are deliberately strict on egress and permissive on local work: a physician
    should be able to read their own notes at full fidelity while nothing identifiable
    leaves the machine without an explicit, recorded decision.
    """

    state_dir: Path = field(default_factory=default_state_dir)
    profile: str = "default"
    policy_version: str = "1"
    context_token_budget: int = 120_000
    compaction_threshold: float = 0.75
    offload_threshold_bytes: int = 8_000
    stuck_loop_threshold: int = 3
    max_retries: int = 2
    budget: Budget = field(default_factory=Budget)
    default_risk: RiskTier = RiskTier.R1_ROUTINE
    require_claim_support: bool = True
    #: Ceiling per destination. Overridable by a work-mode profile, never by a component.
    destination_ceilings: dict[Destination, Sensitivity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A string state_dir is the obvious way to write this from a script, and failing
        # four properties later with "unsupported operand type(s) for /: 'str' and 'str'"
        # is an unhelpful way to say so.
        if not isinstance(self.state_dir, Path):
            object.__setattr__(self, "state_dir", Path(self.state_dir).expanduser())
        if not 0.1 <= self.compaction_threshold <= 0.95:
            raise ValueError("compaction_threshold must be in [0.1, 0.95]")
        if self.stuck_loop_threshold < 2:
            raise ValueError("stuck_loop_threshold must be >= 2")

    # --- the four stores, plus secrets outside them -------------------------
    @property
    def git_workspace(self) -> Path:
        """Authored state: prompts, skills, workflows, policies, project docs."""
        return self.state_dir / "workspace"

    @property
    def event_store(self) -> Path:
        """Runtime state: runs, checkpoints, approvals, budgets."""
        return self.state_dir / "events.db"

    @property
    def artifact_store(self) -> Path:
        """Content-addressed large outputs."""
        return self.state_dir / "artifacts"

    @property
    def index_store(self) -> Path:
        """Queryable state: WorkGraph, evidence graph, memory index."""
        return self.state_dir / "index.db"

    @property
    def secret_store(self) -> Path:
        """Secrets live outside the four stores and are never Git-tracked."""
        return self.state_dir / "secrets"

    def ensure_dirs(self) -> "PSHConfig":
        for path in (self.state_dir, self.git_workspace, self.artifact_store,
                     self.secret_store):
            path.mkdir(parents=True, exist_ok=True)
        # Secrets must not be world-readable even if the parent tree is.
        try:
            self.secret_store.chmod(0o700)
        except OSError:  # pragma: no cover - platform dependent
            pass
        return self

    def with_(self, **kw) -> "PSHConfig":
        return replace(self, **kw)

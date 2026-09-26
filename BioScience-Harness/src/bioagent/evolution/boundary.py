"""The kernel boundary: what self-evolution may never touch.

The converged architecture has two planes. The trusted plane — PSH's kernel, its policy,
labels, contracts and licensing tables, and this package's own policy kernel and bridge —
is immutable from inside the system. The capability plane — components, connectors,
datasets, skills — is what evolution proposes changes to. ``EvolutionPipeline`` already
refuses a proposal that fails its smoke test or regresses a benchmark; this refuses one
that would land in the wrong plane at all, whatever its score.

The check is structural and deliberately dumb: module prefixes and path markers. A
proposal names where its code lives (``runtime.entrypoint``, ``provider.source_path``)
and where it writes (``permissions.filesystem_write``); any of those inside the trusted
plane is a boundary violation. It does not try to be clever about symlinks or encodings,
because it is one gate of several and the others (the filesystem workspace's trust
boundary, PSH's own component-id validation) cover the paths this cannot see.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = ["BoundaryViolation", "KernelBoundary", "PROTECTED_MODULE_PREFIXES",
           "PROTECTED_PATH_MARKERS"]


class BoundaryViolation(ValueError):
    """A proposal targets the trusted plane."""


#: Python modules that constitute the trusted plane. A proposal whose entrypoint resolves
#: into one of these is refused whatever else it declares.
PROTECTED_MODULE_PREFIXES: tuple[str, ...] = (
    "psh.kernel", "psh.policy", "psh.labels", "psh.contracts", "psh.licensing",
    "psh.profiles", "psh.config",
    "bioagent.policy", "bioagent.status", "bioagent.psh", "bioagent.evolution.boundary",
)

#: Path fragments that identify the same plane on disk, for source paths and write targets.
PROTECTED_PATH_MARKERS: tuple[str, ...] = (
    "psh/kernel/", "psh/policy.py", "psh/labels.py", "psh/contracts.py",
    "psh/licensing.py", "psh/profiles.py", "psh/config.py",
    "bioagent/policy.py", "bioagent/status.py", "bioagent/psh/",
    "bioagent/evolution/boundary.py",
)


def _normalise(path: str) -> str:
    text = str(path).replace("\\", "/")
    collapsed = posixpath.normpath(text)
    # normpath drops a trailing slash; markers for directories end with one, so add it
    # back when the original named a directory.
    if text.endswith("/") and not collapsed.endswith("/"):
        collapsed += "/"
    return collapsed


@dataclass(frozen=True)
class KernelBoundary:
    """Refuses proposals that would modify the trusted plane."""

    module_prefixes: tuple[str, ...] = PROTECTED_MODULE_PREFIXES
    path_markers: tuple[str, ...] = PROTECTED_PATH_MARKERS
    #: Absolute directories that are also protected — a PSH state directory, an installed
    #: PSH source tree — for deployments that know where those live.
    protected_roots: tuple[Path, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------ predicates
    def module_is_protected(self, module: str) -> bool:
        name = (module or "").strip()
        return any(name == p or name.startswith(p + ".") for p in self.module_prefixes)

    def path_is_protected(self, path: str) -> bool:
        if not path:
            return False
        norm = _normalise(path)
        probe = norm if norm.endswith("/") else norm + "/"
        if any(marker in norm or marker in probe for marker in self.path_markers):
            return True
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return True                              # unresolvable: fail closed
        for root in self.protected_roots:
            try:
                resolved.relative_to(Path(root).resolve())
                return True
            except ValueError:
                continue
        return False

    # ----------------------------------------------------------------- the check
    def violations(self, manifest: Any) -> list[str]:
        """Every way this manifest reaches into the trusted plane, or an empty list."""
        out: list[str] = []
        runtime = getattr(manifest, "runtime", None)
        entrypoint = str(getattr(runtime, "entrypoint", "") or "")
        module = entrypoint.partition(":")[0] if ":" in entrypoint else entrypoint
        if module and self.module_is_protected(module):
            out.append(f"entrypoint {entrypoint!r} targets the trusted plane")
        provider = getattr(manifest, "provider", None)
        source_path = str(getattr(provider, "source_path", "") or "")
        if source_path and self.path_is_protected(source_path):
            out.append(f"source path {source_path!r} is inside the trusted plane")
        permissions = getattr(manifest, "permissions", None)
        for target in tuple(getattr(permissions, "filesystem_write", ()) or ()):
            if self.path_is_protected(str(target)):
                out.append(f"declares writes into the trusted plane: {target!r}")
        return out

    def check(self, manifest: Any) -> None:
        problems = self.violations(manifest)
        if problems:
            raise BoundaryViolation(
                f"{getattr(manifest, 'id', '?')}: " + "; ".join(problems))

    def check_paths(self, paths: Iterable[str]) -> None:
        bad = [p for p in paths if self.path_is_protected(str(p))]
        if bad:
            raise BoundaryViolation(f"paths inside the trusted plane: {bad}")

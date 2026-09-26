"""Component providers — discover components from an upstream ecosystem.

v1 wrote one adapter class per project, so supporting N ecosystems meant N
hand-written wrappers and a growing `elif` chain. A provider instead *discovers*
many components from one source: point it at a cloned repo and it yields
manifests. Adding an ecosystem becomes adding one provider, not 456 wrappers.
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Iterator

from ..runtime.component import ComponentManifest


class Provider(abc.ABC):
    """Discovers components from one upstream source."""

    #: stable provider id, recorded in component provenance
    name: str = "provider"

    @abc.abstractmethod
    def discover(self) -> Iterator[ComponentManifest]:
        """Yield manifests for everything this provider can offer."""

    def available(self) -> bool:
        """Whether the upstream source is present locally."""
        return True

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name}>"


def walk_python_files(root: Path, skip_dirs: frozenset[str] = frozenset(
        {".git", "__pycache__", "node_modules", ".venv", "tests", "test"})) -> Iterator[Path]:
    """Yield .py files under root, skipping vendored and test directories."""
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*.py")):
        if any(part in skip_dirs for part in p.parts):
            continue
        if p.name.startswith(("_", "setup", "conftest")):
            continue
        yield p

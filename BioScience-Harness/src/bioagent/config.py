"""Path configuration — no machine-specific absolute paths in source.

Resolution order for every location: explicit argument, then environment
variable, then a repository-relative default. This keeps the package portable
and lets the unit test tier run on a clean checkout with no data present.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root (this file is <repo>/src/bioagent/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_DATA_LAKE = "BIOAGENT_DATA_LAKE"
ENV_WORKSPACE = "BIOAGENT_WORKSPACE"
ENV_CATALOGUE = "BIOAGENT_CATALOGUE"


def data_lake_dir(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_DATA_LAKE)
    if env:
        return Path(env).expanduser()
    return REPO_ROOT.parent / "data" / "biomni_lake"


def workspace_dir(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_WORKSPACE)
    if env:
        return Path(env).expanduser()
    return REPO_ROOT / "workspace"


#: Legacy location: the catalogue lived only here before it was packaged.
LEGACY_CATALOGUE = REPO_ROOT / "data" / "unified_capability_catalogue.csv"


def catalogue_path(explicit: str | os.PathLike | None = None) -> Path:
    """Where the capability catalogue lives.

    Resolution order: explicit argument, `$BIOAGENT_CATALOGUE`, the copy shipped
    inside the package, then the pre-packaging repository location. The packaged
    copy is what makes an installed wheel usable — before it existed this
    returned a `<repo>/data/...` path that only ever resolves in a source
    checkout, so every wheel install found nothing.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CATALOGUE)
    if env:
        return Path(env).expanduser()
    from .data import CATALOGUE_CSV

    if CATALOGUE_CSV.exists():
        return CATALOGUE_CSV
    return LEGACY_CATALOGUE


def data_lake_available(explicit: str | os.PathLike | None = None) -> bool:
    d = data_lake_dir(explicit)
    return d.is_dir() and any(d.iterdir())

"""Runtime data shipped inside the package.

The unified capability catalogue is not a report artifact — it is the index the
registry loads on import of a `CapabilityRegistry`, so it has to travel with the
distribution. It previously lived only in the repository's top-level `data/`
directory, which setuptools never collected: `pip install bioagent` produced a
wheel with no catalogue, and `config.catalogue_path()` pointed at a path that
exists only in a source checkout.

Analysis outputs (census tables, audits, comparison matrices) stay in the
repository's `data/` directory; only files the library reads at runtime belong
here.
"""

from __future__ import annotations

from pathlib import Path

#: Directory holding the packaged runtime data files.
DATA_DIR = Path(__file__).resolve().parent

#: The capability catalogue the registry loads by default.
CATALOGUE_CSV = DATA_DIR / "unified_capability_catalogue.csv"


def packaged(name: str) -> Path:
    """Absolute path to a packaged data file."""
    return DATA_DIR / name

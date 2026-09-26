"""Make `bioagent` (and the sibling `psh`) importable for a standalone script.

Every check in this directory is run two ways: by a developer with `PYTHONPATH`
already set, and by a CI step that has only run `pip install -e .`. Those two
environments differ in exactly one respect — whether `psh` is on the path — and
the difference is invisible locally, which is how two of these checks shipped
broken: they worked in every terminal that had been used to develop them and
failed on the first fresh checkout.

So each script bootstraps its own imports rather than trusting an environment
variable to be set by a workflow file. This mirrors `tests/conftest.py`, which
resolves the sibling tree the same way for the same reason.

Import order matters: `src` first, then the sibling `psh` tree, and only if `psh`
is not already importable — an installed `psh` should win over a sibling
checkout, so that a packaged release is tested against its declared dependency
rather than against whatever happens to sit next to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The harness root (one level above `scripts/`).
ROOT = Path(__file__).resolve().parents[1]
#: The repository root, which holds the sibling kernel.
REPO_ROOT = ROOT.parent


def bootstrap() -> None:
    """Put `src` and, if needed, the sibling `psh` tree on `sys.path`."""
    for entry in (ROOT / "src", REPO_ROOT / "PSH-Harness" / "src"):
        if not entry.is_dir():
            continue
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)

    try:
        import psh  # noqa: F401
    except ModuleNotFoundError as exc:
        # Naming the cause matters: "No module named 'psh'" from a check script
        # reads as a broken repository rather than as a missing checkout.
        raise SystemExit(
            f"{exc}\n\nThis script needs PSH-Harness alongside BioScience-Harness.\n"
            f"Looked for it at: {REPO_ROOT / 'PSH-Harness' / 'src'}\n"
            "Either clone the full repository, or `pip install -e PSH-Harness`."
        ) from exc

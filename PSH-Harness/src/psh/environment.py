"""What this PSH installation can enforce, as a plain report for tools outside the kernel.

``bioagent doctor`` and any other readiness check outside the trusted plane need two
facts the kernel owns: which PHI detector is in use, and what the isolated runner
confines. Reaching into ``psh.kernel`` for them is exactly what the BioScience boundary
forbids, so this module is the public door: it reads the facts and hands back data.
Nothing here builds a kernel, mints an envelope or executes anything.
"""

from __future__ import annotations

from typing import Any

__all__ = ["environment_report"]


def environment_report() -> dict[str, Any]:
    """Version, classifier and isolation facts for this process. Read-only."""
    from . import __version__
    from .kernel.classify import SABLE_AVAILABLE, Classifier
    from .kernel.isolation import IsolatedRunner

    classifier = Classifier()
    runner = IsolatedRunner()
    return {
        "version": __version__,
        "classifier": {"detector": classifier.detector_name,
                       "validated": classifier.validated,
                       "sable_available": SABLE_AVAILABLE},
        "isolation": runner.report().as_dict(),
    }

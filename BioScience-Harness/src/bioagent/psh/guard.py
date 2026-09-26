"""Re-export of the kernel boundary, so bridge users find it beside the bridge.

The implementation lives in ``bioagent.evolution.boundary`` because the evolution
pipeline must enforce it whether or not PSH is installed; importing it through this
package would make the boundary depend on the very thing it protects.
"""

from ..evolution.boundary import BoundaryViolation, KernelBoundary

__all__ = ["BoundaryViolation", "KernelBoundary"]

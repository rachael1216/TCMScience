"""Conservative change impact analysis, not automatic execution or cache admission."""

from dataclasses import dataclass
from typing import Any, Iterable

from ..contracts import RunEnvelope
from ..scientist.ports import ProtocolResolver
from .compiler import ScientificCompiler
from .ir import ScientificProgram, SideEffect


@dataclass(frozen=True)
class Amendment:
    old_fingerprint: str
    new_fingerprint: str
    invalidated: tuple[str, ...]
    removed: tuple[str, ...]
    reusable_candidates: tuple[str, ...]


def assess_amendment(old: ScientificProgram, new: ScientificProgram,
                     envelope: RunEnvelope, *, completed: Iterable[str] = (),
                     registry: Any = None, policy: Any = None,
                     scientific_ledger: ProtocolResolver | None = None) -> Amendment:
    """Validate both versions against current authority and propagate invalidation.

    A candidate is only an unchanged, completed pure task. Before reusing any result,
    callers still need its verified artifact digest, current labels, policy admission
    and execution environment identity. No result is loaded or executed here.
    """
    old = ScientificProgram.from_dict(old.to_dict())
    new = ScientificProgram.from_dict(new.to_dict())
    compiler = ScientificCompiler(registry, scientific_ledger=scientific_ledger)
    before = compiler.compile(old, envelope, policy=policy)
    after = compiler.compile(new, envelope, policy=policy)
    finished = set(completed)
    if not finished <= set(before.task_fingerprints):
        raise ValueError("completed contains unknown task IDs")
    # A changed research objective/acceptance contract invalidates the whole result set.
    def context(program: ScientificProgram) -> dict[str, Any]:
        body = program.plan.to_dict()
        return {k: v for k, v in body.items() if k not in {"tasks", "plan_id"}}
    changed_context = context(old) != context(new)
    invalidated = tuple(tid for tid in after.validated.order if changed_context or
                        before.task_fingerprints.get(tid) != after.task_fingerprints[tid])
    reusable = tuple(tid for tid in after.validated.order if tid in finished and
                     tid not in invalidated and
                     new.contracts[tid].side_effect == SideEffect.PURE)
    return Amendment(before.fingerprint, after.fingerprint, invalidated,
                     tuple(sorted(set(before.task_fingerprints) - set(after.task_fingerprints))),
                     reusable)

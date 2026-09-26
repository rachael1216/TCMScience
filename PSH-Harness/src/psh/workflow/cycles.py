"""Cycle admission: graph topology cannot turn a new operation key into consent."""
from ..runtime.plan import TaskKind
from ..runtime.plan_validator import PlanRejected, PlanViolation, _manifest_for
from .ir import SideEffect


def cyclic_stages(workflow):
    """Tarjan SCC pass, including self edges and every declared branch.

    DynamicWorkflow bounds the graph at 256 nodes. Unreachable declared cycles
    are checked too, consistent with scientific preflight of all stages.
    """
    edges = {stage.stage_id: tuple(t for t in (
        (stage.branch.if_true, stage.branch.if_false) if stage.branch else
        (stage.next_stage,)) if t is not None) for stage in workflow.stages}
    indices, low = {}, {}
    stack, active, cyclic = [], set(), set()

    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in edges[node]:
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in edges[node]:
                cyclic.update(component)

    for node in edges:
        if node not in indices:
            visit(node)
    return frozenset(cyclic)


def validate_repetition(stage, registry):
    """Both requested semantics and trusted, invocable facts must allow repeat.

    Model invocations remain governed by model budgets and egress. They are not
    declared pure here. Delegation is refused in cycles: this controller cannot
    attest the effects of an arbitrary delegated workflow.
    """
    violations = []
    for task in stage.program.plan.tasks:
        if task.kind == TaskKind.MODEL:
            continue
        if task.kind == TaskKind.DELEGATE:
            violations.append(PlanViolation("REPEAT103", task.task_id,
                "cyclic delegation has no trusted repeat-safety attestation"))
            continue
        if stage.program.contracts[task.task_id].side_effect not in {
                SideEffect.PURE, SideEffect.IDEMPOTENT, SideEffect.AT_LEAST_ONCE}:
            violations.append(PlanViolation("REPEAT101", task.task_id,
                "tool contract does not permit cyclic repetition"))
        manifest = _manifest_for(registry, task.component_id)
        try:
            component = registry.component(task.component_id) if registry is not None else None
        except Exception:
            component = None
        actual = getattr(component, "manifest", None)
        if (getattr(manifest, "idempotent", None) is not True or
                getattr(actual, "idempotent", None) is not True):
            violations.append(PlanViolation("REPEAT102", task.task_id,
                "cyclic tools require trusted invocable idempotency"))
    if violations:
        raise PlanRejected("cyclic stage repetition refused", violations)

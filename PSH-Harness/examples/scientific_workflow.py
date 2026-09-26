"""Offline compiler demonstration with synthetic, non-clinical fixture metadata.

Run from PSH-Harness after installing psh: python examples/scientific_workflow.py
No model, network, tool execution or filesystem writes are required.
"""

from dataclasses import replace

from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import Criterion, Plan, PlanRejected, PlanTask
from psh.workflow import (
    ClaimSpec, ClaimType, Effect, EvidenceSpec, ScientificCompiler, ScientificProgram,
    SideEffect, TaskContract, assess_amendment,
)


def main():
    policy = PolicySnapshot(profile_id="scientific-demo", require_claim_support=False)
    envelope = policy.envelope()
    tasks = tuple(PlanTask(
        task_id=tid, objective=f"Inspect synthetic {tid} fixture",
        dependencies=deps, destinations=(Destination.LOCAL_MODEL,),
        max_label=Sensitivity.PUBLIC,
    ) for tid, deps in (("source", ()), ("claim", ("source",)), ("independent", ())))
    local = TaskContract(sensitivity=Sensitivity.PUBLIC, effects=(Effect.LOCAL_MODEL,),
                         side_effect=SideEffect.PURE)
    program = ScientificProgram(
        Plan(objective="Check synthetic evidence contracts", tasks=tasks,
             completion_criteria=(Criterion("fixture inspected", "task"),)),
        {"source": replace(local, evidence=EvidenceSpec(
            "animal", "mouse cohort A", "fixture intervention", "fixture endpoint",
            ("fixture:synthetic-source",))),
         "claim": replace(local, claim=ClaimSpec(
             ClaimType.MECHANISTIC, "mouse cohort A", "fixture intervention",
             "fixture endpoint", ("source",))),
         "independent": local})
    compiled = ScientificCompiler().compile(program, envelope, policy=policy)
    print("Compiled task order:", compiled.validated.order)
    print("Content fingerprint:", compiled.fingerprint)

    amended = replace(program, plan=replace(program.plan, tasks=(
        tasks[0], replace(tasks[1], payload={"threshold": 0.01}), tasks[2])))
    delta = assess_amendment(program, amended, envelope, completed=program.contracts)
    print("Invalidated:", delta.invalidated)
    print("Reuse candidates (not admitted cached results):", delta.reusable_candidates)

    invalid = replace(program, contracts={**program.contracts, "claim": replace(
        program.contracts["claim"], claim=replace(
            program.contracts["claim"].claim, kind=ClaimType.CLINICAL))})
    try:
        ScientificCompiler().compile(invalid, envelope)
    except PlanRejected as exc:
        print("Expected refusal:", exc)
    else:
        raise AssertionError("animal evidence was incorrectly accepted for clinical efficacy")


if __name__ == "__main__":
    main()

"""Compile declared scientific contracts; runtime verification remains mandatory."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from ..contracts import PolicyDenied, RunEnvelope
from ..kernel.authority import AuthorityLattice
from ..scientist.ports import ProtocolResolver
from ..labels import DataLabel, Destination, Sensitivity
from ..runtime.plan import Plan, TaskKind
from ..runtime.plan_validator import PlanRejected, PlanValidator, PlanViolation, ValidatedPlan
from ..runtime.plan_validator import _manifest_for
from .ir import (EVIDENCE_DESIGNS, PREDICTIVE_DESIGNS, ClaimType, Effect,
                 ScientificProgram, SideEffect, digest)


_DESTINATIONS = {
    Effect.LOCAL_READ: Destination.LOCAL_COMPUTE,
    Effect.LOCAL_COMPUTE: Destination.LOCAL_COMPUTE,
    Effect.LOCAL_MODEL: Destination.LOCAL_MODEL,
    Effect.TRUSTED_REMOTE: Destination.TRUSTED_REMOTE,
    Effect.PUBLIC_REMOTE: Destination.PUBLIC_REMOTE,
    Effect.PERSIST: Destination.PERSISTENT,
    Effect.USER_OUTPUT: Destination.USER_OUTPUT,
}
#: Which evidence designs may license which claim kind.
#:
#: Two things are deliberately *not* in this table, and both are load-bearing.
#:
#: **Predictive designs license only MECHANISTIC.** A docking score or a
#: predicted target is a statement about a model, so it can support a mechanism
#: claim — that is the entire output of network pharmacology and refusing it
#: would make the tool useless. It appears in exactly one row. It cannot support
#: ASSOCIATION, CLINICAL or SAFETY, because those are statements about patients
#: and a prediction is not an observation of one. This is the type-level form of
#: the plan's "computational prediction cannot be asserted as clinical fact".
#:
#: **CLINICAL accepts only `randomized_trial`.** Observational evidence is not
#: enough for an efficacy claim, so there is no row here where a prediction and
#: a clinical claim can meet.
_SUPPORTS = {
    ClaimType.CLASSICAL: frozenset({"classical_text"}),
    ClaimType.TRADITIONAL: frozenset({"classical_text", "expert_consensus"}),
    ClaimType.MECHANISTIC: EVIDENCE_DESIGNS - {"classical_text", "expert_consensus"}
                           | PREDICTIVE_DESIGNS,
    ClaimType.ASSOCIATION: frozenset({"observational", "randomized_trial"}),
    ClaimType.CLINICAL: frozenset({"randomized_trial"}),
    ClaimType.SAFETY: frozenset({"case_report", "observational", "randomized_trial"}),
}

#: Claim kinds that assert something about patients. Used to report, rather than
#: to refuse, a mechanism claim that rests on prediction: the claim is licensed,
#: but every material claim downstream of a simulation is weaker than one
#: downstream of a measurement, and a reader is entitled to know which they have.
_CLINICAL_CLAIMS = frozenset({ClaimType.CLINICAL, ClaimType.ASSOCIATION,
                              ClaimType.SAFETY})


@dataclass(frozen=True)
class Compilation:
    validated: ValidatedPlan
    fingerprint: str
    task_fingerprints: Mapping[str, str]
    sensitivities: Mapping[str, Sensitivity]

    @property
    def plan(self) -> Plan:
        return self.validated.plan


class ScientificCompiler:
    def __init__(self, registry: Any = None, *, scientific_ledger: ProtocolResolver | None = None) -> None:
        self.validator = PlanValidator(registry=registry)
        self.scientific_ledger = scientific_ledger

    def compile(self, program: ScientificProgram, envelope: RunEnvelope, *,
                policy: Any = None, input_label: DataLabel | None = None) -> Compilation:
        # Snapshot nested mappings through the public wire format; callers cannot mutate
        # the source payload halfway through a pass or retain aliases into the result.
        program = ScientificProgram.from_dict(json.loads(json.dumps(
            program.to_dict(), allow_nan=False)))
        # A caller may retain an envelope minted before policy was tightened.
        # Use the shared lattice for every authority dimension, before graph
        # validation or any registered-protocol reads. Never widen the run.
        if policy is not None:
            envelope = AuthorityLattice.meet(envelope, policy.ceiling())
        validated = self.validator.validate(program.plan, envelope, policy=policy)
        tasks = {t.task_id: t for t in program.plan.tasks}
        violations: list[PlanViolation] = []
        labels: dict[str, Sensitivity] = {}
        hashes: dict[str, str] = {}

        def reject(code: str, task_id: str, detail: str) -> None:
            violations.append(PlanViolation(code, task_id, detail))

        for tid in validated.order:
            task, contract = tasks[tid], program.contracts[tid]
            trusted_idempotent = None
            if task.kind == TaskKind.TOOL:
                manifest = _manifest_for(self.validator.registry, task.component_id)
                trusted_idempotent = getattr(manifest, "idempotent", None) is True
                if not trusted_idempotent:
                    if contract.side_effect in {SideEffect.PURE, SideEffect.IDEMPOTENT}:
                        reject("EFFECT106", tid,
                               "repeat-safe declaration requires trusted manifest idempotency")
                    if task.retry.max_attempts > 1:
                        reject("RETRY102", tid,
                               "automatic tool retries require trusted manifest idempotency")
            if contract.statistics is not None:
                for code, detail in contract.statistics.violations():
                    reject(code, tid, detail)
            effective = validated.envelope_for(tid)
            registered_label = Sensitivity.PUBLIC
            if contract.protocol_binding is not None:
                binding = contract.protocol_binding
                if contract.statistics is None:
                    reject("PROTOCOL101", tid, "bound task requires a statistical design")
                if self.scientific_ledger is None:
                    reject("PROTOCOL102", tid, "bound task requires a scientific ledger")
                else:
                    try:
                        registered, record_label = self.scientific_ledger.resolve_protocol(
                            binding.record_id, effective)
                    except (PolicyDenied, ValueError, TypeError, KeyError):
                        # Do not echo stored content, IDs or underlying exception text.
                        reject("PROTOCOL103", tid, "registered protocol unavailable, invalid or unauthorized")
                    else:
                        registered_label = record_label.sensitivity
                        if registered.fingerprint != binding.fingerprint:
                            reject("PROTOCOL104", tid, "registered protocol fingerprint differs from binding")
                        if contract.statistics is not None and registered != contract.statistics.protocol:
                            reject("PROTOCOL105", tid, "analysis protocol differs from registered protocol")
            # All dependencies carry data in today's loop, even without InputBinding.
            label = max((contract.sensitivity, task.input_sensitivity, registered_label,
                         input_label.sensitivity if input_label else Sensitivity.PUBLIC,
                         *(labels[d] for d in task.dependencies)))
            labels[tid] = label
            if any(n < 0 for n in (task.estimated_tokens, task.estimated_usd,
                                  task.estimated_seconds)):
                reject("RESOURCE101", tid, "resource estimates cannot be negative")
            if label > effective.max_label.sensitivity:
                reject("FLOW101", tid, "derived sensitivity exceeds the task/run ceiling")
            if not task.destinations:
                reject("EFFECT101", tid, "scientific tasks must explicitly declare destinations")
            if not contract.effects:
                reject("EFFECT102", tid, "scientific tasks must explicitly declare effects")
            declared = {_DESTINATIONS[e] for e in contract.effects}
            if declared != set(task.destinations):
                reject("EFFECT103", tid, "effects and execution destinations must agree")
            if task.kind == TaskKind.MODEL and not declared.intersection({
                    Destination.LOCAL_MODEL, Destination.TRUSTED_REMOTE,
                    Destination.PUBLIC_REMOTE}):
                reject("EFFECT104", tid, "model task requires a model effect")
            for destination in declared | set(task.destinations):
                if not DataLabel(label).permits(destination):
                    reject("FLOW102", tid,
                           f"derived {label.name} data may not reach {destination.name}")
            if (task.retry.max_attempts > 1 and contract.side_effect not in
                    {SideEffect.PURE, SideEffect.IDEMPOTENT, SideEffect.AT_LEAST_ONCE}):
                reject("RETRY101", tid, "this side-effect class does not permit automatic retries")
            if contract.side_effect == SideEffect.PURE and declared.intersection({
                    Destination.PERSISTENT, Destination.USER_OUTPUT,
                    Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE}):
                reject("EFFECT105", tid, "a pure task cannot declare externally visible effects")

            if contract.claim:
                claim = contract.claim
                for source in claim.evidence_from:
                    if source not in task.dependencies:
                        reject("EVIDENCE101", tid, "evidence source must be a direct dependency")
                        continue
                    evidence = program.contracts[source].evidence
                    if evidence is None:
                        reject("EVIDENCE102", tid, "referenced task declares no evidence")
                        continue
                    designs = (evidence.underlying_designs if evidence.design ==
                               "systematic_review" else (evidence.design,))
                    if not set(designs) <= _SUPPORTS[claim.kind]:
                        reject("EVIDENCE103", tid,
                               f"{designs} cannot license {claim.kind.value}")
                    for dimension in ("population", "intervention", "outcome"):
                        if getattr(evidence, dimension).strip().casefold() != getattr(
                                claim, dimension).strip().casefold():
                            reject("EVIDENCE104", tid, f"evidence/claim {dimension} mismatch")

            hash_input = {"task": task.to_dict(), "contract": contract.to_dict(),
                          "dependencies": {d: hashes[d] for d in task.dependencies}}
            if task.kind == TaskKind.TOOL:
                hash_input["trusted_idempotent"] = trusted_idempotent
            if contract.protocol_binding is not None:
                hash_input["registered_sensitivity"] = registered_label.name
            hashes[tid] = digest(hash_input)
        if violations:
            raise PlanRejected("scientific compilation failed: " + "; ".join(
                str(v) for v in violations[:8]), violations)
        # plan_id is a transport identity, not scientific content.
        content = program.to_dict()
        content["plan"].pop("plan_id")
        lowered = replace(program.plan, tasks=tuple(
            replace(t, input_sensitivity=labels[t.task_id]) for t in program.plan.tasks))
        validated = self.validator.validate(lowered, envelope, policy=policy)
        return Compilation(validated, digest(content), MappingProxyType(hashes),
                           MappingProxyType(labels))


class ScientificPlanner:
    """AgentLoopController adapter: compile afresh under the actual run envelope.

    The loop then uses its normal validator, broker and release gates. This adapter
    does not authorize a tool, certify source content or make a claim releasable.
    """

    def __init__(self, program: ScientificProgram, *, registry: Any = None,
                 policy: Any = None, scientific_ledger: ProtocolResolver | None = None) -> None:
        self._program = json.loads(json.dumps(program.to_dict(), allow_nan=False))
        self.compiler = ScientificCompiler(registry, scientific_ledger=scientific_ledger)
        self.policy = policy

    def plan(self, state: Any, *, feedback: Any = None) -> Plan:
        label = getattr(state, "objective_label", None)
        inherited = getattr(state, "plan_label", None)
        if inherited is not None:
            label = inherited if label is None else label.merged_with(inherited)
        return self.compiler.compile(ScientificProgram.from_dict(self._program),
                                     state.envelope, policy=self.policy, input_label=label).plan

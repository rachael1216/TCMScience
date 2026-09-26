"""Compile a `SkillSpec` into an executable scientific program.

The output is a ``psh.workflow.ir.ScientificProgram``, not a bare ``Plan``, and
that choice is the point of this module. A `ScientificProgram` carries a
`TaskContract` per task — sensitivity, effects, evidence scope, claim scope — and
is checked by **PSH's own `ScientificCompiler`**. So a skill's declarations are
not re-implemented here and then hoped to agree with the kernel: the kernel is
the thing that enforces them. On the project's stated rule that "the model may
reason but does not define the laws of the laboratory", this compiler is a
*translator*, not a second legislator.

What it does, in order:

1. **Derives destinations from declared hosts.** Not from the backend. The
   authority a skill requests follows what it reaches, the same direction
   `psh.bridge.manifest` already uses, so a `python` skill that declares a host
   cannot end up with `LOCAL_COMPUTE`-only authority.
2. **Intersects with the envelope.** The effective authority is the meet of what
   the skill asks, what the operator's policy ceiling allows, and what the run
   envelope grants. A skill that asks for more than the envelope holds is
   **refused** (`SKILL_PERMISSION_WIDENS_ENVELOPE`), never silently trimmed: a
   silent trim turns a misdeclared skill into a mysteriously failing one.
3. **Emits one task per declared output.** Each carries the evidence and claim
   contract the skill declared, so PSH can refuse a program whose claim kind
   outruns its evidence design before anything executes.
4. **Checks the evidence policy.** Claim kinds the skill forbids are rejected at
   compile time rather than at artifact validation, because a skill that cannot
   emit the claim should never have produced a program that tries to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Imported through PSH's public door only. `bioagent` never imports `psh.kernel`
# — that boundary is enforced by
# `tests/test_psh_bridge.py::test_bioagent_never_reaches_into_the_kernel_internals`,
# and an unused `AuthorityLattice` import here briefly broke it. The kernel is
# reached by handing it a program to validate, not by calling its lattice.
from psh.contracts import Autonomy, RiskTier, RunEnvelope
from psh.labels import Destination, Sensitivity
from psh.workflow.ir import (PREDICTIVE_DESIGNS, ClaimType, EvidenceSpec, ClaimSpec,
                             Effect, ScientificProgram, SideEffect, TaskContract)

from ..tcm.model import CLAIM_KINDS
from .models import SkillSpec, destinations_for, label_ceiling_for

__all__ = ["CompilationRefused", "SkillCompilation", "compile_skill", "COMPILE_CODES"]

#: Stable refusal codes, in the style of PSH's scientific compiler (`EVIDENCE101`
#: …). These strings end up in registry audit records and must not be renumbered.
COMPILE_CODES: Mapping[str, str] = {
    "SKILL101": "skill requests authority the run envelope does not hold",
    "SKILL102": "skill declares a claim kind its own evidence policy forbids",
    "SKILL103": "skill declares no outputs to compile",
    "SKILL104": "skill reaches the network but declares no hosts",
    "SKILL105": "skill's license does not permit its declared integration mode",
    "SKILL106": "skill requires secrets, which the compiler cannot grant",
    "SKILL107": "skill mutates but requests a non-mutating autonomy",
}

#: Claim kind (BioScience vocabulary) → PSH `ClaimType`.
_CLAIM_TYPE: Mapping[str, ClaimType] = {
    "attribution": ClaimType.CLASSICAL,
    "traditional_use": ClaimType.TRADITIONAL,
    "mechanism": ClaimType.MECHANISTIC,
    "association": ClaimType.ASSOCIATION,
    "efficacy": ClaimType.CLINICAL,
    "recommendation": ClaimType.CLINICAL,
    "safety_signal": ClaimType.SAFETY,
}

#: PSH `ClaimType` → the strongest design its `_SUPPORTS` table admits for it.
#: Used to pick the `EvidenceSpec.design` the compiler declares, so the IR
#: validates against the kernel's own table rather than against a guess.
_DESIGN_FOR_CLAIM: Mapping[ClaimType, str] = {
    ClaimType.CLASSICAL: "classical_text",
    ClaimType.TRADITIONAL: "expert_consensus",
    ClaimType.MECHANISTIC: "animal",
    ClaimType.ASSOCIATION: "observational",
    ClaimType.CLINICAL: "randomized_trial",
    ClaimType.SAFETY: "case_report",
}

#: Hosts a skill reaches → destination. A host is a public third party that
#: receives the query whatever its reputation; the operator's HostPolicy is what
#: promotes one to trusted, and this compiler does not second-guess that.
#:
#: The mapping itself lives in `models.destinations_for` so the compiler and the
#: skill's own `requested_authority()` cannot drift; this is an alias, not a copy.
_destinations_for = destinations_for


def _effects_for(spec: SkillSpec) -> tuple[Effect, ...]:
    out: list[Effect] = [Effect.LOCAL_COMPUTE]
    if spec.permissions.filesystem_read:
        out.append(Effect.LOCAL_READ)
    if spec.permissions.network:
        out.append(Effect.PUBLIC_REMOTE)
    if spec.permissions.filesystem_write:
        out.append(Effect.PERSIST)
    return tuple(dict.fromkeys(out))


def _side_effect_for(spec: SkillSpec, *, trusted_idempotent: bool) -> SideEffect:
    """What the task's side-effect class may be, given what we can prove.

    PSH refuses a repeat-safe declaration (`PURE`/`IDEMPOTENT`) from a tool whose
    manifest it has not verified as idempotent (`EFFECT106`), and refuses
    automatic retries for the same reason (`RETRY102`). So the honest default for
    a freshly compiled skill — one not yet resolved in the trusted registry — is
    the *non*-repeatable class, and this is deliberately the conservative
    direction: a caller that has resolved the manifest passes
    ``trusted_idempotent=True`` and gets retries.

    `PURE` additionally requires that nothing externally visible happens
    (`EFFECT105`), so a skill reaching a public host can never be pure however
    deterministic it is. Deriving that from the declared destinations rather than
    from `deterministic` is the difference between a correct declaration and one
    that reads well.
    """
    external = bool(spec.permissions.network or spec.permissions.filesystem_write)
    if spec.mutates:
        return (SideEffect.IDEMPOTENT if trusted_idempotent
                else SideEffect.NON_REPEATABLE)
    if external:
        return SideEffect.IDEMPOTENT if trusted_idempotent else SideEffect.AT_MOST_ONCE
    if trusted_idempotent and spec.runtime.deterministic:
        return SideEffect.PURE
    return SideEffect.AT_MOST_ONCE


class CompilationRefused(ValueError):
    """A skill could not be compiled, with every reason, not just the first.

    Carries :attr:`codes` so a registry audit or an Arena UI can group refusals
    by cause without parsing prose.
    """

    def __init__(self, skill_id: str, reasons: Sequence[tuple[str, str]]):
        self.skill_id = skill_id
        self.reasons = tuple(reasons)
        self.codes = tuple(code for code, _ in reasons)
        body = "\n".join(f"  {COMPILE_CODES.get(c, c)}: {d}" for c, d in reasons)
        super().__init__(f"skill {skill_id!r} was refused:\n{body}")


@dataclass(frozen=True, slots=True)
class SkillCompilation:
    """A compiled skill: the program PSH will validate, and what it may reach."""

    spec: SkillSpec
    program: ScientificProgram
    #: The authority the skill actually gets, after intersecting with the
    #: envelope. Narrower than ``spec.requested_authority()`` or equal to it.
    effective_max_label: Sensitivity = Sensitivity.PUBLIC
    destinations: tuple[Destination, ...] = ()
    #: Informational: claims licensed by prediction alone. The kernel permits
    #: these — mechanism claims from docking are legitimate — but a reader is
    #: entitled to know the run's mechanism claims rest on simulation.
    prediction_backed_claims: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        from psh.workflow.ir import digest
        return digest({"skill": self.spec.as_dict(),
                       "program": self.program.to_dict()})

    def as_dict(self) -> dict[str, Any]:
        return {"skill_id": self.spec.id, "skill_version": self.spec.version,
                "composite_id": self.spec.composite_id,
                "fingerprint": self.fingerprint,
                "effective_max_label": self.effective_max_label.name.lower(),
                "destinations": [d.name.lower() for d in self.destinations],
                "prediction_backed_claims": list(self.prediction_backed_claims),
                "notes": list(self.notes),
                "tasks": [t.task_id for t in self.program.plan.tasks]}


def compile_skill(spec: SkillSpec, envelope: RunEnvelope, *,
                  objective: str = "", project_id: str = "",
                  local_ceiling: Sensitivity | None = None,
                  plan_id: str = "",
                  trusted_idempotent: bool = False) -> SkillCompilation:
    """Compile ``spec`` into a program the run envelope is allowed to execute.

    Pure. Consults the envelope's policy ceiling, the skill's own declarations and
    the static licence lattice — no clock, no network, no registry — so the same
    arguments always yield the same program and fingerprint.

    ``trusted_idempotent`` is how a caller reports that the kernel has *verified*
    this skill's component as idempotent — normally by resolving it in the
    registry. It defaults to ``False``, which is the conservative direction: PSH
    refuses a repeat-safe declaration or an automatic retry from a tool whose
    manifest it has not checked (`EFFECT106`, `RETRY102`), so compiling before
    registration yields a plan with no retries and a non-repeatable side effect
    rather than one that will not compile. Keeping the lookup out of this
    function is deliberate; the compiler states what it was told, and the caller
    that resolved the manifest is the one that knows.
    """
    refusals: list[tuple[str, str]] = []

    # -- 1. secrets -------------------------------------------------------
    # The compiler has no way to grant a secret: secrets are resolved by the
    # kernel's ingress, and a skill claiming one would be asking for something
    # this path cannot deliver. Refusing is honest; granting would be a lie.
    if spec.permissions.secrets:
        refusals.append(("SKILL106", (
            f"declares secrets {list(spec.permissions.secrets)}; a skill cannot "
            "receive secrets through this compilation path")))

    # -- 2. network without hosts ----------------------------------------
    if spec.runtime.backend == "http" and not spec.permissions.network:
        refusals.append(("SKILL104", (
            "backend 'http' reaches the network but permissions.network is empty; "
            "a host that is not declared cannot be gated")))

    # -- 3. mutation vs autonomy -----------------------------------------
    if spec.mutates and spec.autonomy == Autonomy.OBSERVE:
        refusals.append(("SKILL107", (
            f"skill mutates ({'subprocess' if spec.permissions.subprocess else 'writes'}) "
            "but requests autonomy 'observe'; a mutating skill needs at least "
            "'act_with_approval'")))

    # -- 4. licence ------------------------------------------------------
    if spec.license_spdx:
        from psh.licensing import license_ruling
        ruling = license_ruling(spec.license_spdx, spec.integration_mode)
        if not ruling.allowed:
            refusals.append(("SKILL105", (
                f"license {spec.license_spdx!r} under integration_mode "
                f"{spec.integration_mode!r}: {ruling.decision.value}")))

    # -- 5. claim kinds the skill forbids itself -------------------------
    for kind in spec.evidence.forbidden_claims:
        if kind in spec.evidence.claim_kinds:
            # SkillEvidencePolicy already refuses this at construction; kept as a
            # belt-and-braces check because a hand-built spec could bypass it.
            refusals.append(("SKILL102", f"claim kind {kind!r} is both permitted "
                             "and forbidden"))

    # -- 6. authority: the skill may not widen the envelope ---------------
    destinations = destinations_for(spec)
    # The skill's own reach sets a ceiling (a public host cannot hold PHI); the
    # envelope can only narrow it further, never raise it.
    ceiling = (_as_sensitivity(local_ceiling) if local_ceiling is not None
               else _as_sensitivity(envelope.max_label))
    effective_label = min(label_ceiling_for(spec), ceiling)

    # A skill that reaches a public host cannot hold PHI, and the envelope
    # cannot raise that. Compare against the envelope's own ceiling: if the
    # skill's request exceeds what the envelope grants, refuse — do not trim.
    if spec.risk_tier > envelope.risk:
        refusals.append(("SKILL101", (
            f"requests risk tier {spec.risk_tier.name.lower()} but the run envelope "
            f"holds {envelope.risk.name.lower()}")))
    if spec.autonomy.value and _autonomy_rank(spec.autonomy) < _autonomy_rank(envelope.autonomy):
        refusals.append(("SKILL101", (
            f"requests autonomy {spec.autonomy.value!r}, which is broader than the "
            f"envelope's {envelope.autonomy.value!r}")))
    for destination in destinations:
        if not envelope.permits_destination(destination):
            refusals.append(("SKILL101", (
                f"reaches {destination.name.lower()}, which the run envelope does "
                "not permit")))

    # -- 7. something to compile -----------------------------------------
    if not spec.outputs:
        refusals.append(("SKILL103", "declares no outputs, so there is nothing to "
                         "produce and nothing to validate"))

    if refusals:
        raise CompilationRefused(spec.id, refusals)

    # ------------------------------------------------------------------
    # Build the program.
    # ------------------------------------------------------------------
    tasks = _tasks_for(spec, trusted_idempotent=trusted_idempotent)
    contracts = _contracts_for(spec, destinations, trusted_idempotent=trusted_idempotent)

    program = ScientificProgram(
        plan=_plan_for(spec, tasks, objective=objective, plan_id=plan_id),
        contracts=contracts)

    prediction_backed = tuple(
        kind for kind in spec.evidence.claim_kinds
        if _CLAIM_TYPE.get(kind) is ClaimType.MECHANISTIC)

    notes: list[str] = []
    if prediction_backed:
        notes.append(
            "mechanism claims from this skill may rest on computational prediction; "
            "such claims are licensed but must not be restated as clinical findings")
    if effective_label < Sensitivity.SENSITIVE and spec.permissions.network:
        notes.append(
            f"maximum data label is {effective_label.name.lower()} because the skill "
            "reaches a public host; sensitive data cannot be sent to it")

    return SkillCompilation(
        spec=spec, program=program, effective_max_label=effective_label,
        destinations=destinations, prediction_backed_claims=prediction_backed,
        notes=tuple(notes))


def _autonomy_rank(autonomy: Autonomy) -> int:
    from psh.contracts import AUTONOMY_ORDER
    try:
        return AUTONOMY_ORDER.index(autonomy)
    except ValueError:
        return len(AUTONOMY_ORDER)


def _as_sensitivity(value: Any) -> Sensitivity:
    """Reduce a ceiling to a plain :class:`Sensitivity`.

    ``RunEnvelope.max_label`` is annotated ``Sensitivity`` but is a
    ``DataLabel`` at runtime, and the two do not compare — ``min()`` over a list
    holding both raises ``TypeError`` rather than returning a wrong answer, which
    is how this was found. Both are accepted here rather than changing the
    kernel's field, because the annotation is the kernel's business and this
    compiler only needs the ordering.
    """
    if isinstance(value, Sensitivity):
        return value
    sensitivity = getattr(value, "sensitivity", None)
    if isinstance(sensitivity, Sensitivity):
        return sensitivity
    if isinstance(value, str):
        return Sensitivity[value.upper()]
    raise TypeError(f"cannot read a Sensitivity from {value!r}")


def _tasks_for(spec: SkillSpec, *, trusted_idempotent: bool) -> tuple[Any, ...]:
    """One ingest task per declared source, then one task for the skill itself.

    One task *per source* rather than one task reading all of them: a task names
    a single component, so a combined ingest task would have to name one
    connector and quietly drop the others — the plan would read HERB and never
    ETCM while reporting that it consulted both. Separate tasks also let PSH
    attach the right sensitivity and destination to each source, which matters
    as soon as one of them is a public host and another is a local file.

    Deliberately not clever beyond that. A skill is one operation with a declared
    output schema; the multi-step research loops that will eventually wrap it
    belong to the planner, not here.
    """
    from psh.runtime.plan import PlanTask, RetryPolicy, TaskKind

    # Retries are only available to a task whose repeat-safety the kernel has
    # verified. Asking for one anyway is refused (RETRY102), so the compiler
    # asks for what it can support rather than emitting a plan that will not
    # compile.
    attempts = 2 if trusted_idempotent else 1
    destinations = _destinations_for(spec)

    tasks: list[PlanTask] = []
    for source in spec.sources:
        tasks.append(PlanTask(
            task_id=f"ingest.{source}",
            objective=f"read source {source} for {spec.name}",
            kind=TaskKind.TOOL, component_id=source,
            capability_requirements=(source,),
            max_risk=spec.risk_tier, max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
            destinations=destinations, autonomy=spec.autonomy,
            evidence_required=False,
            retry=RetryPolicy(max_attempts=attempts)))

    tasks.append(PlanTask(
        task_id="run", objective=f"execute {spec.name}",
        kind=TaskKind.TOOL, component_id=f"skill.{spec.id}",
        dependencies=tuple(f"ingest.{s}" for s in spec.sources),
        capability_requirements=(f"skill.{spec.id}",),
        max_risk=spec.risk_tier,
        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        destinations=destinations, autonomy=spec.autonomy,
        estimated_tokens=spec.resources.expected_tokens,
        estimated_usd=spec.resources.expected_usd,
        estimated_seconds=spec.resources.expected_latency_s,
        output_schema=dict(spec.outputs or {}),
        evidence_required=bool(spec.sources),
        retry=RetryPolicy(max_attempts=1 if spec.mutates else attempts)))
    return tuple(tasks)


def _contracts_for(spec: SkillSpec, destinations: Sequence[Destination], *,
                   trusted_idempotent: bool) -> Mapping[str, TaskContract]:
    """The evidence and claim contract each task runs under.

    The kernel's model is that **evidence and claims live on different tasks**:
    an evidence-producing task declares an `EvidenceSpec`, and a claiming task
    declares a `ClaimSpec` whose `evidence_from` names those tasks — which must be
    its direct dependencies (`EVIDENCE101`) and must themselves declare evidence
    (`EVIDENCE102`). Getting this wrong is refused, which is why the compiler
    reads the kernel's own `_SUPPORTS` table rather than a private copy of it.

    So: each `ingest.<source>` task declares what it retrieves, and `run` claims
    over them. A skill with no declared sources has no evidence-producing task to
    cite, so its `run` task declares no claim — its claims are made inside the
    artifact and are governed there by `bioagent.contracts.validate_artifact`,
    which is the layer that actually gates publication. Inventing a
    self-referential claim to satisfy the kernel would be worse than omitting it.
    """
    from psh.workflow.compiler import _SUPPORTS

    claim_kinds = [k for k in spec.evidence.claim_kinds if k in _CLAIM_TYPE]
    claim_type = _CLAIM_TYPE[claim_kinds[0]] if claim_kinds else ClaimType.MECHANISTIC

    population = spec.inputs.get("population") or "as declared by the run"
    intervention = spec.inputs.get("intervention") or spec.name
    outcome = spec.inputs.get("outcome") or "as declared by the run"

    design = _design_for(spec, claim_type, _SUPPORTS)

    read_effects = tuple(e for e in _effects_for(spec)
                         if e in (Effect.LOCAL_COMPUTE, Effect.LOCAL_READ,
                                  Effect.PUBLIC_REMOTE, Effect.TRUSTED_REMOTE))
    ingest_side_effect = (SideEffect.IDEMPOTENT if trusted_idempotent
                          else SideEffect.AT_MOST_ONCE)

    contracts: dict[str, TaskContract] = {}
    for source in spec.sources:
        contracts[f"ingest.{source}"] = TaskContract(
            sensitivity=Sensitivity.RESEARCH_DEIDENTIFIED,
            effects=read_effects or (Effect.LOCAL_COMPUTE,),
            side_effect=ingest_side_effect,
            evidence=EvidenceSpec(design=design, population=population,
                                  intervention=intervention, outcome=outcome,
                                  provenance=(source,)),
            claim=None)

    claim = None
    if spec.sources:
        claim = ClaimSpec(kind=claim_type, population=population,
                          intervention=intervention, outcome=outcome,
                          evidence_from=tuple(f"ingest.{s}" for s in spec.sources))

    contracts["run"] = TaskContract(
        sensitivity=Sensitivity.RESEARCH_DEIDENTIFIED,
        effects=_effects_for(spec),
        side_effect=_side_effect_for(spec, trusted_idempotent=trusted_idempotent),
        evidence=None, claim=claim)
    return contracts


def _design_for(spec: SkillSpec, claim_type: ClaimType,
                supports: Mapping[ClaimType, frozenset[str]]) -> str:
    """Pick the `EvidenceSpec.design` the kernel will accept for this claim.

    Chosen from the designs `_SUPPORTS` admits for the claim kind, narrowed by
    the skill's own tier ceiling, and preferring in order:

    1. a **predictive** design when the ceiling is preclinical — a skill capped at
       preclinical evidence that claims a mechanism is describing a model, and
       calling it ``in_vitro`` would record a simulation as a bench experiment;
    2. otherwise the design whose tier is closest to the skill's ceiling, so the
       declared evidence is the strongest the skill said it would use.

    Raises rather than guessing. A skill whose policy admits no design that can
    license its own claim kind is misdeclared, and ``SkillEvidencePolicy``
    catches most such cases at load — this is the backstop for a hand-built spec.
    """
    from ..contracts.evidence_item import EVIDENCE_TIER_FOR_DESIGN
    from ..tcm.model import EvidenceTier

    admitted = supports.get(claim_type, frozenset())
    ceiling = spec.evidence.tier_ceiling
    candidates = [d for d in admitted if EVIDENCE_TIER_FOR_DESIGN[d] <= ceiling]
    if not candidates:
        raise CompilationRefused(spec.id, [("SKILL102", (
            f"no evidence design licensed for {claim_type.value} is within the skill's "
            f"tier ceiling {ceiling.name.lower()}; admitted designs are "
            f"{sorted(admitted)}"))])

    predictive = [d for d in candidates if d in PREDICTIVE_DESIGNS]
    if predictive and ceiling is EvidenceTier.PRECLINICAL:
        return sorted(predictive)[0]
    return max(candidates, key=lambda d: EVIDENCE_TIER_FOR_DESIGN[d])


def _plan_for(spec: SkillSpec, tasks: Sequence[Any], *, objective: str, plan_id: str) -> Any:
    from psh.runtime.plan import Criterion, Plan

    return Plan(
        objective=objective or f"run skill {spec.name}",
        tasks=tuple(tasks),
        assumptions=(
            f"skill {spec.composite_id} implements its declared evidence policy",
            "declared sources are pinned to the snapshots recorded in their SourceCards",
        ),
        completion_criteria=(
            Criterion(description="the skill emitted a validating ResearchArtifact",
                      kind="artifact"),
            Criterion(description="every claim cites evidence that licenses it",
                      kind="claim"),
        ),
        evidence_requirements=tuple(spec.sources),
        plan_id=plan_id or f"plan.{spec.id}@{spec.version}",
        produced_by=f"bioagent.skills.compiler@{spec.composite_id}")

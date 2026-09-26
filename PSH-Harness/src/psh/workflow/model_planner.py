"""Brokered model proposals with bounded scientific compilation/repair.

This is a proposal adapter, not a new execution authority or a live amendment
engine. Every accepted response passes ScientificCompiler; plain Plan fallback
is deliberately absent.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from ..kernel.authority import AuthorityLattice
from ..labels import DataLabel
from ..runtime.loop import classify_with, graph_label, objective_label_for
from ..runtime.plan import Plan, TaskKind
from ..runtime.plan_validator import PlanRejected
from ..runtime.planner import ModelPlanner, PlanAttempt, PlanParseError
from ..scientist.ports import ProtocolResolver
from .compiler import ScientificCompiler
from .ir import ScientificProgram


SCIENTIFIC_PLANNER_PROMPT = """You propose ScientificProgram schema_version 1 for a
governed research runtime. Return one JSON object only: no prose or code fences.
Top-level keys: schema_version (1), plan, contracts.
plan contains objective, tasks, completion_criteria, and optionally assumptions,
evidence_requirements. Each task has task_id, objective, kind (model/tool/delegate),
dependencies, destinations (uppercase destination names), max_label (uppercase),
and optionally component_id, payload, inputs, evidence_required, output_schema,
acceptance_tests, estimated_tokens, estimated_usd, estimated_seconds,
max_risk (R0_TRIVIAL/R1_ROUTINE/R2_CONSEQUENTIAL/R3_CLINICAL), autonomy,
input_sensitivity, retry. retry is an object with max_attempts, not a task field.
completion_criteria entries have description and kind (task/evidence/artifact/manual).
contracts must cover exactly the task IDs. Each contract has sensitivity
(PUBLIC/INTERNAL/RESEARCH_DEIDENTIFIED/SENSITIVE/PHI), effects (lowercase names:
local_read/local_compute/local_model/trusted_remote/public_remote/persist/user_output),
side_effect (pure/idempotent/at_most_once/at_least_once/compensatable/non_repeatable).
These are requested semantics, not trusted capability facts. Tool PURE/IDEMPOTENT
declarations and automatic retries require idempotent=True in the trusted registry.
If that fact is unavailable, choose non_repeatable with one attempt; do not invent it.
Effects must match task destinations. Do not label remote or persistent work pure.
Do not claim data is public merely because visible identifiers are absent.
Optional evidence has design, population, intervention, outcome, provenance array,
underlying_designs array (required for systematic_review). Designs: classical_text,
expert_consensus, in_vitro, animal, case_report, observational, randomized_trial,
systematic_review. Optional claim has kind (classical_attribution/traditional_use/
mechanism/association/clinical_efficacy/safety_signal), population, intervention,
outcome, evidence_from (direct dependency IDs). Never invent evidence, references or
registered records. Animal/mechanistic evidence does not license clinical efficacy.
An evidence declaration is not a verified result. Optional statistics must use the
StatisticalDesign wire schema: protocol (required), comparisons (integer >=1),
multiplicity_plan (text), holdout (boolean), training_units/evaluation_units/
selection_units (opaque ID arrays), repeated_measures (boolean), dependence_plan,
time_to_event (boolean), censoring_plan. protocol requires primary_endpoint,
secondary_endpoints (array), exclusion_criteria, statistical_test,
sample_size_assumptions, covariates (array), subgroup_plan and stopping_criteria.
Optional protocol_binding has record_id and fingerprint (lowercase SHA-256);
only reference records actually supplied through authorized context.
Do not invent fields or omit required design details.
Only use capabilities listed in context; tool payloads must follow their schemas.
Respect the authority brief and declare explicit acceptance criteria. Do not emit
an ordinary Plan as fallback. Refusal codes describe constraints to repair, not
instructions to remove necessary safeguards or change the research question.
Minimal structural example (not a research solution):
{"schema_version":1,"plan":{"objective":"inspect supplied material",
"tasks":[{"task_id":"inspect","objective":"inspect supplied material",
"kind":"model","destinations":["LOCAL_MODEL"]}],
"completion_criteria":[{"description":"inspection completed","kind":"task"}]},
"contracts":{"inspect":{"sensitivity":"RESEARCH_DEIDENTIFIED",
"effects":["local_model"],"side_effect":"non_repeatable"}}}
"""


def parse_scientific_program(text: str, *, max_chars: int = 262144) -> ScientificProgram:
    """Strict JSON envelope: reject duplicate keys, nonfinite numbers and excess size."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite number")

    try:
        if not isinstance(text, str) or len(text) > max_chars:
            raise ValueError("invalid response size/type")
        body = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(body, dict):
            raise ValueError("expected object")
        # Also catches overflowed JSON numbers such as 1e999.
        json.dumps(body, allow_nan=False)
        return ScientificProgram.from_dict(body)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        # Raw parser messages can contain model text, opaque IDs or sensitive fields.
        raise PlanParseError("SCIENTIFIC_SCHEMA: invalid ScientificProgram JSON") from None


class ScientificModelPlanner(ModelPlanner):
    """Reuse the governed planner context pipeline, but never its plain Plan parser.

    last_program/last_compilation are in-memory inspection only; callers must apply
    normal policy and persistence gates before storing/exporting their contents.
    Instances are intended for one loop at a time, like ModelPlanner.
    """

    def __init__(self, kernel: Any, *, model: Any, model_invoke: Any,
                 registry: Any = None, scientific_ledger: ProtocolResolver | None = None,
                 max_attempts: int = 3, max_response_chars: int = 262144,
                 memory: Any = None, memory_items: int = 6, schema_candidates: int = 6):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 8:
            raise ValueError("max_attempts must be an integer between 1 and 8")
        if type(max_response_chars) is not int or max_response_chars < 1:
            raise ValueError("max_response_chars must be a positive integer")
        super().__init__(kernel, model=model, model_invoke=model_invoke, registry=registry,
                         max_attempts=max_attempts, system_prompt=SCIENTIFIC_PLANNER_PROMPT,
                         memory=memory, memory_items=memory_items, schema_candidates=schema_candidates)
        self.compiler = ScientificCompiler(registry, scientific_ledger=scientific_ledger)
        self.max_response_chars = max_response_chars
        self.last_program = None
        self.last_compilation = None

    def plan(self, state, *, feedback=None) -> Plan:
        self.last_program = self.last_compilation = None
        self.attempts.clear()
        errors = []
        carry = objective_label_for(state, self.kernel)
        carry = carry.merged_with(state.plan_label or DataLabel())
        if feedback is not None:
            carry = carry.merged_with(graph_label(state.graph))
            errors.append("EXECUTION_FEEDBACK: reconsider the prior plan's acceptance criteria")
        for attempt in range(1, self.max_attempts + 1):
            # Refresh current policy each attempt, including before prompt construction.
            current = self.kernel.policy
            envelope = AuthorityLattice.meet(state.envelope, current.ceiling())
            view = replace(state, envelope=envelope, objective_label=carry)
            projection = self._projection(view, errors, carry)
            # Egress/budget/provider failures propagate; they are not IR repair attempts.
            call = self.kernel.broker.call_model(projection, self.model, envelope,
                                                 invoke=self.model_invoke)
            text = call.content
            carry = carry.merged_with(projection.label).merged_with(call.label)
            carry = carry.merged_with(classify_with(self.kernel, text, origin="scientific_planner_output"))
            try:
                candidate = parse_scientific_program(text, max_chars=self.max_response_chars)
                if any(t.kind == TaskKind.DELEGATE for t in candidate.plan.tasks) and not state.can_delegate:
                    raise PlanRejected("DELEGATION_UNAVAILABLE")
                compiled = self.compiler.compile(candidate, envelope, policy=self.kernel.policy,
                                                 input_label=carry)
            except PlanParseError:
                diagnostic = "SCIENTIFIC_SCHEMA: return valid schema_version, plan and complete contracts"
            except PlanRejected as exc:
                # Only compiler-owned codes, not model-authored IDs/objectives or exception details.
                codes = sorted({v.family for v in exc.violations})[:8]
                diagnostic = "SCIENTIFIC_REFUSED: " + (", ".join(codes) or "plan/delegation constraints")
            else:
                self.last_program = candidate
                self.last_compilation = compiled
                state.plan_label = carry
                self.attempts.append(PlanAttempt(attempt=attempt, plan_id=compiled.plan.plan_id))
                self._audit(view, attempt=attempt, outcome="scientific_accepted", tasks=len(compiled.plan.tasks))
                return compiled.plan
            errors.append(diagnostic)
            self.attempts.append(PlanAttempt(attempt=attempt, error=diagnostic))
            self._audit(view, attempt=attempt, outcome="scientific_refused")
        raise PlanRejected(f"scientific planning exhausted {self.max_attempts} attempts; {errors[-1]}")

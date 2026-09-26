"""Application-facing dynamic execution through the existing release gate."""
from dataclasses import replace

from ..kernel.authority import AuthorityLattice
from ..labels import DataLabel
from ..policy import PolicyLattice
from ..runtime.finalize import Finalizer, ReleasedResult, ingest_evidence
from ..runtime.loop import LoopResult, Termination
from .dynamic import DynamicWorkflowController


class DynamicResearchRunService:
    """Return only ReleasedResult, never intermediate execution objects.

    No extra synthesis model is called. The last stage's terminal task outputs
    are the candidate. This API is not a sandbox against trusted Python code.
    """
    def __init__(self, kernel, *, journal, registry=None, scientific_ledger=None,
                 model=None, model_invoke=None, operations=None, limits=None,
                 cancellation=None, policy=None):
        self.kernel = kernel
        self.policy = policy if policy is not None else kernel.policy
        if policy is not None and hasattr(kernel, "check_requirements"):
            kernel.check_requirements(policy)
        self._controller = DynamicWorkflowController(kernel, journal=journal,
            registry=registry, scientific_ledger=scientific_ledger, model=model,
            model_invoke=model_invoke, operations=operations, limits=limits,
            cancellation=cancellation, policy=self.policy)

    def run(self, workflow, *, envelope=None, sources=None, project_id="", input_label=None):
        policy = PolicyLattice.meet(self.policy, self.kernel.policy)
        envelope = AuthorityLattice.meet(envelope or policy.envelope(project_id=project_id),
                                         policy.ceiling())
        try:
            result = self._controller.run(workflow, envelope, input_label=input_label)
        except Exception as exc:
            return ReleasedResult(status="not_completed", run_id=envelope.run_id,
                termination="execution_error", refused_at="execution",
                refusal_kind=type(exc).__name__)
        if not result.ok or not result.stages:
            return ReleasedResult(status="not_completed", run_id=envelope.run_id,
                termination=result.state.reason, refused_at="execution",
                refusal_kind="WorkflowIncomplete")

        last = result.stages[-1]
        goals = {stage.goal_status for stage in result.stages}
        goal = ("verified" if goals == {"verified"} else
                "pending_manual" if goals <= {"verified", "pending_manual"} else "unverified")
        # Omitted outputs still contribute labels and limitations. Metadata carries
        # neither raw caveat strings nor user-authored task identifiers.
        caveats = {f"visit_{i}": ("a stage component reported a limitation",)
                   for i, stage in enumerate(result.stages, 1) if stage.caveats}
        candidate = LoopResult(loop_id=last.loop_id, run_id=envelope.run_id,
            termination=Termination.GOAL_SATISFIED, results=last.results, plan=last.plan,
            iterations=sum(s.iterations for s in result.stages),
            label=DataLabel(result.label.sensitivity), goal_status=goal, caveats=caveats)
        policy = PolicyLattice.meet(policy, self.kernel.policy)
        envelope = AuthorityLattice.meet(envelope, policy.ceiling())
        try:
            # The shared gate permits provenance-capped caller text in some modes.
            # This scientific application entry accepts only revalidated trusted
            # records as release evidence; a missing source is then refused by
            # the normal support gate when policy requires claim support.
            records = ingest_evidence(self.kernel, sources, envelope)
            records = {key: record for key, record in records.items() if record.trusted}
            released = Finalizer(self.kernel, policy=policy).finalize(
                candidate, envelope, sources=records, project_id=project_id or envelope.project_id,
                require_goal_verification=True)
        except Exception as exc:
            return ReleasedResult(status="refused", run_id=envelope.run_id,
                loop_id=last.loop_id, termination="completed", refused_at="finalization",
                refusal_kind=type(exc).__name__, label=DataLabel(result.label.sensitivity))
        return replace(released, termination="completed",
            label=DataLabel(released.label.sensitivity),
            citations=released.citations if released.ok else (),
            limitations=released.limitations if released.ok else ("dynamic output was withheld",))

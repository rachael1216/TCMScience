"""Validated self-evolution: propose → test → benchmark → policy → promote.

The distinction that makes this safe: an evolution agent may only write
*proposals*. It cannot touch a production component. Promotion happens when a
proposal passes every gate, including a benchmark comparison against the
incumbent, and every stage is recorded as an event.

This is controlled recursive improvement, not an agent rewriting itself.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from ..runtime.component import ComponentManifest
from ..runtime.events import EventLog, EventType
from ..runtime.hmr import HotReloader, SwapResult
from ..runtime.registry import ComponentRegistry
from ..status import LifecycleState


class ProposalState(str, Enum):
    PROPOSED = "PROPOSED"
    GENERATED = "GENERATED"
    TESTED = "TESTED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PROMOTED = "PROMOTED"
    QUARANTINED = "QUARANTINED"


@dataclass
class BenchmarkResult:
    """Score of one component version on one benchmark."""

    benchmark: str
    score: float
    n_cases: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    """A candidate component change, with its audit trail."""

    proposal_id: str
    component: ComponentManifest
    rationale: str = ""
    state: ProposalState = ProposalState.PROPOSED
    stage_log: list[dict[str, Any]] = field(default_factory=list)
    incumbent_score: float | None = None
    candidate_score: float | None = None
    #: per-benchmark scores, so a regression on one cannot be averaged away
    benchmark_scores: dict[str, float] = field(default_factory=dict)
    incumbent_scores: dict[str, float] = field(default_factory=dict)
    diff: str = ""

    def log(self, stage: str, ok: bool, detail: str) -> None:
        self.stage_log.append({"stage": stage, "ok": ok, "detail": detail,
                               "at": time.time()})

    @property
    def improved(self) -> bool:
        if self.incumbent_score is None or self.candidate_score is None:
            return False
        return self.candidate_score > self.incumbent_score

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "component"}
        d["state"] = self.state.value
        d["component"] = self.component.to_dict()
        d["improved"] = self.improved
        return d


class EvolutionPipeline:
    """Runs a proposal through every gate. Only the pipeline may promote."""

    STAGES = ("generate", "boundary", "test", "benchmark", "policy", "promote")

    def __init__(self, registry: ComponentRegistry, reloader: HotReloader,
                 benchmark_runner: Callable[[ComponentManifest, str], BenchmarkResult] | None = None,
                 policy_check: Callable[[ComponentManifest], tuple[bool, str]] | None = None,
                 min_improvement: float = 0.0,
                 regression_tolerance: float = 0.0,
                 min_absolute_score: float = 0.0,
                 workspace: Any = None, git: Any = None,
                 smoke_runner: Callable[[ComponentManifest], tuple[bool, str]] | None = None,
                 boundary: Any = None) -> None:
        self.registry = registry
        self.reloader = reloader
        #: The kernel boundary. Evolution may propose changes to the capability plane and
        #: never to the trusted plane; a proposal that targets the latter is quarantined
        #: before a smoke test or a benchmark is spent on it, whatever it would score.
        from .boundary import KernelBoundary
        self.boundary = boundary if boundary is not None else KernelBoundary()
        self._benchmark = benchmark_runner
        # The 'test' stage runs the candidate's declared smoke test BEFORE any
        # benchmark is spent on it. Defaults to the reloader's smoke runner so
        # the two gates cannot disagree about what "passes".
        self._smoke = smoke_runner or getattr(reloader, "_smoke", None) or (lambda m: (True, "no smoke test"))
        self._policy_check = policy_check or (lambda m: (True, "no extra policy configured"))
        self.min_improvement = min_improvement
        #: How far any single benchmark may fall below the incumbent, however
        #: well the others do. Averaging alone lets a large win on one benchmark
        #: hide a correctness or safety regression on another.
        self.regression_tolerance = regression_tolerance
        #: Floor a component with no incumbent must clear. Without it the score
        #: gate was guarded by `incumbent is not None`, so a brand-new component
        #: scoring 0.0 on every benchmark was promoted unchecked.
        self.min_absolute_score = min_absolute_score
        self.workspace = workspace
        self.git = git

    def _benchmark_verdict(self, proposal: "Proposal", incumbent: Any,
                           bms: tuple[str, ...]) -> tuple[bool, str]:
        """Decide promotion from every benchmark, not an average of one.

        Three conditions, all required:
        1. every declared benchmark actually produced a score;
        2. no single benchmark regressed past `regression_tolerance`;
        3. the mean beat the incumbent by `min_improvement` — or, with no
           incumbent, every benchmark cleared `min_absolute_score`.
        """
        missing = [b for b in bms if b not in proposal.benchmark_scores]
        if missing:
            return False, f"benchmarks did not run: {', '.join(missing)}"

        if incumbent is None:
            weak = {b: sc for b, sc in proposal.benchmark_scores.items()
                    if sc < self.min_absolute_score}
            if weak:
                return False, ("no incumbent to compare against, and "
                               + ", ".join(f"{b} {sc:.3f}" for b, sc in weak.items())
                               + f" below the required {self.min_absolute_score:.3f}")
            return True, ("no incumbent; all "
                          f"{len(bms)} benchmark(s) at or above {self.min_absolute_score:.3f} "
                          f"(mean {proposal.candidate_score:.3f})")

        regressions = {
            b: (sc, proposal.incumbent_scores.get(b, 0.0))
            for b, sc in proposal.benchmark_scores.items()
            if (proposal.incumbent_scores.get(b, 0.0) - sc) > self.regression_tolerance
        }
        if regressions:
            return False, ("regression on " + ", ".join(
                f"{b}: {c:.3f} vs incumbent {i:.3f}" for b, (c, i) in regressions.items()))

        gain = (proposal.candidate_score or 0.0) - (proposal.incumbent_score or 0.0)
        if gain <= self.min_improvement:
            return False, (f"mean over {len(bms)} benchmark(s): candidate "
                           f"{proposal.candidate_score:.3f} vs incumbent "
                           f"{proposal.incumbent_score:.3f} "
                           f"(gain {gain:+.3f} <= {self.min_improvement})")
        return True, (f"{len(bms)} benchmark(s), no regression; mean candidate "
                      f"{proposal.candidate_score:.3f} vs incumbent "
                      f"{proposal.incumbent_score:.3f} (gain {gain:+.3f})")

    # ------------------------------------------------------------------ stages
    def submit(self, proposal: Proposal, *, events: EventLog | None = None,
               parent_event: str | None = None) -> Proposal:
        """Run every gate in order, stopping at the first failure."""
        ev = parent_event

        # 1. GENERATE — the proposal must be a structurally valid component.
        errs = proposal.component.validate()
        if errs:
            proposal.state = ProposalState.QUARANTINED
            proposal.log("generate", False, "; ".join(errs))
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev, "; ".join(errs))
            return proposal
        proposal.state = ProposalState.GENERATED
        proposal.log("generate", True, "manifest is structurally valid")

        # 1b. BOUNDARY — the proposal must live in the capability plane.
        crossings = self.boundary.violations(proposal.component)
        if crossings:
            detail = "trusted plane is immutable from inside the system: " + "; ".join(crossings)
            proposal.state = ProposalState.QUARANTINED
            proposal.log("boundary", False, detail)
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev, detail)
            return proposal
        proposal.log("boundary", True, "targets the capability plane only")

        # Persist first so a rejected candidate still leaves an auditable record.
        if self.workspace is not None:
            self._persist(proposal)

        # 2. TEST — the declared smoke test must pass before a benchmark is spent.
        #    (v2 listed this stage but never executed it; TESTED was assigned
        #    after the benchmark with no test having run.)
        if proposal.component.validation.smoke_test:
            ok, msg = self._smoke(proposal.component)
        else:
            ok, msg = True, "no smoke test declared (benchmark gate still applies)"
        if not ok:
            proposal.state = ProposalState.QUARANTINED
            proposal.log("test", False, msg)
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev, msg)
            return proposal
        proposal.log("test", True, msg)
        proposal.state = ProposalState.TESTED

        # 3. BENCHMARK — a candidate must beat the incumbent to be considered.
        incumbent = self.registry.get(proposal.component.id)
        bms = proposal.component.validation.benchmarks or ()
        if self._benchmark and bms:
            # Every declared benchmark runs. Scoring only `bms[0]` meant a
            # candidate that improved the first benchmark was promoted while a
            # declared safety or regression benchmark it broke was never run.
            for bm in bms:
                cand = self._benchmark(proposal.component, bm)
                proposal.benchmark_scores[bm] = cand.score
                if incumbent is not None:
                    inc = self._benchmark(incumbent, bm)
                    proposal.incumbent_scores[bm] = inc.score
            scores = list(proposal.benchmark_scores.values())
            proposal.candidate_score = sum(scores) / len(scores) if scores else 0.0
            if incumbent is not None:
                inc_scores = list(proposal.incumbent_scores.values())
                proposal.incumbent_score = (sum(inc_scores) / len(inc_scores)
                                            if inc_scores else 0.0)

            ok, msg = self._benchmark_verdict(proposal, incumbent, bms)
            if not ok:
                proposal.state = ProposalState.QUARANTINED
                proposal.log("benchmark", False, msg)
                self._emit(events, EventType.EVALUATION_COMPLETED, proposal, ev, msg)
                return proposal
            proposal.log("benchmark", True, msg)
            self._emit(events, EventType.EVALUATION_COMPLETED, proposal, ev,
                       proposal.stage_log[-1]["detail"])
        else:
            proposal.log("benchmark", False, "no benchmark declared")
            proposal.state = ProposalState.QUARANTINED
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev,
                       "promotion requires at least one declared benchmark")
            return proposal

        # 4. POLICY — an extra gate beyond the kernel's per-call authorization.
        ok, why = self._policy_check(proposal.component)
        if not ok:
            proposal.state = ProposalState.QUARANTINED
            proposal.log("policy", False, why)
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev, why)
            return proposal
        proposal.log("policy", True, why)
        proposal.state = ProposalState.VALIDATED

        # 5. PROMOTE — transactional swap; the incumbent survives any failure.
        swap = self.reloader.swap(proposal.component)
        if not swap.promoted:
            proposal.state = ProposalState.QUARANTINED
            proposal.log("promote", False, f"{swap.stage_failed}: {swap.reason}")
            self._emit(events, EventType.COMPONENT_QUARANTINED, proposal, ev, swap.reason)
            return proposal
        proposal.state = ProposalState.PROMOTED
        proposal.log("promote", True,
                     f"registry v{swap.registry_version}: "
                     f"{swap.previous_version or 'new'} -> {swap.new_version}")
        if self.git is not None:
            try:
                proposal.diff = self.git.diff("HEAD") or ""
                self.git.commit(f"promote {proposal.component.id} "
                                f"{swap.previous_version}->{swap.new_version}")
            except Exception as exc:  # noqa: BLE001 - git failure must not undo promotion
                proposal.log("git", False, f"{type(exc).__name__}: {exc}")
        self._emit(events, EventType.COMPONENT_PROMOTED, proposal, ev,
                   proposal.stage_log[-1]["detail"])
        return proposal

    # ------------------------------------------------------------------ helpers
    def _persist(self, proposal: Proposal) -> None:
        """Write the proposal into the agent-writable proposals/ tree."""
        base = f"proposals/{proposal.proposal_id}"
        self.workspace.write(f"{base}/component.yaml", proposal.component.to_yaml())
        self.workspace.write(f"{base}/rationale.md",
                             f"# {proposal.component.id}\n\n{proposal.rationale}\n")

    @staticmethod
    def _emit(events: EventLog | None, kind: str, proposal: Proposal,
              parent: str | None, detail: str) -> None:
        if events is None:
            return
        events.emit(kind, parent=parent, component_id=proposal.component.id,
                    component_version=proposal.component.version,
                    status=proposal.state.value, detail={"detail": detail})


class EvolutionAgent:
    """Reads performance signals and writes proposals. It cannot promote.

    The separation is structural: this class has no reference to the pipeline's
    promote path, and its only output is a `Proposal` object.
    """

    def __init__(self, registry: ComponentRegistry) -> None:
        self.registry = registry

    def analyze_failures(self, events: EventLog, *, min_failures: int = 1) -> list[dict]:
        """Find components whose calls failed or were unavailable."""
        tally: dict[str, dict] = {}
        for e in events.of_type(EventType.TOOL_CALLED):
            if e.status in ("FAILED", "UNAVAILABLE", "TIMEOUT", "DENIED"):
                row = tally.setdefault(e.component_id, {"component_id": e.component_id,
                                                        "failures": 0, "statuses": {}})
                row["failures"] += 1
                row["statuses"][e.status] = row["statuses"].get(e.status, 0) + 1
                row["last_error"] = str(e.detail.get("error", ""))[:160]
        return [r for r in sorted(tally.values(), key=lambda x: -x["failures"])
                if r["failures"] >= min_failures]

    def propose(self, component_id: str, *, changes: dict[str, Any], rationale: str,
                proposal_id: str | None = None) -> Proposal:
        """Create a proposal from an incumbent plus a change set."""
        incumbent = self.registry.get(component_id)
        if incumbent is None:
            raise KeyError(f"unknown component {component_id!r}")
        d = incumbent.to_dict()
        d.update(changes)
        # a proposal always bumps the version
        if d.get("version") == incumbent.version:
            major, _, rest = incumbent.version.partition(".")
            minor, _, patch = rest.partition(".")
            d["version"] = f"{major}.{int(minor or 0) + 1}.0"
        d["state"] = LifecycleState.DISCOVERED.value
        cand = ComponentManifest.from_dict(d)
        pid = proposal_id or f"{component_id.replace('.', '_')}_{int(time.time())}"
        return Proposal(proposal_id=pid, component=cand, rationale=rationale)

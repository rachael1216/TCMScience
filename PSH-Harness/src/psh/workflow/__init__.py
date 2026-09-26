"""Scientific contracts compiled to the existing governed Plan runtime."""

from .ir import (
    ClaimSpec, ClaimType, Effect, EvidenceSpec, ScientificProgram,
    SideEffect, TaskContract, ProtocolBinding,
)
from .compiler import Compilation, ScientificCompiler, ScientificPlanner
from .amend import Amendment, assess_amendment
from .statistics import StatisticalDesign
from .model_planner import ScientificModelPlanner, parse_scientific_program
from .dynamic import Branch, StageInput, WorkflowStage, DynamicWorkflow, DynamicResult, DynamicWorkflowController
from .events import RunEventJournal, ReplayRefused, ReplayState, replay
from .service import DynamicResearchRunService

__all__ = [
    "ClaimSpec", "ClaimType", "Effect", "EvidenceSpec", "ScientificProgram",
    "SideEffect", "TaskContract", "Compilation", "ScientificCompiler",
    "ScientificPlanner", "Amendment", "assess_amendment",
    "StatisticalDesign",
    "ProtocolBinding",
    "ScientificModelPlanner", "parse_scientific_program",
    "Branch", "StageInput", "WorkflowStage", "DynamicWorkflow", "DynamicResult", "DynamicWorkflowController",
    "RunEventJournal", "ReplayRefused", "ReplayState", "replay",
    "DynamicResearchRunService",
]

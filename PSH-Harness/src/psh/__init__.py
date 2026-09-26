"""psh — Physician-Scientist Harness: a governed control plane.

psh is not a bigger agent. It is the control plane a physician-scientist's work runs
through: a trusted kernel that classifies data and enforces where it may travel, a durable
WorkGraph that outlives models and agents, and a composition boundary so specialised
harnesses plug in rather than being merged.

Two defects in the predecessor design motivated it, both demonstrated in code before any of
this was written:

1. A PHI boundary enforced on tool arguments let a chart note reach a model provider
   through the context. Fixed by labelling data rather than call sites, and by treating a
   model call as egress.
2. Citation checking verified that a source existed, not that it supported the sentence.
   Fixed by ``ClaimSupport``.

Quick start::

    from psh import TrustedKernel, PSHConfig, Runner, get_profile
    from psh.protocols import SableAdapter

    profile = get_profile("clinical_research")
    kernel = TrustedKernel(PSHConfig(**profile.as_config_kwargs()))
    runner = Runner(kernel)
    result = runner.run("...", **profile.as_envelope_kwargs())
"""

from .config import PSHConfig, default_state_dir
from .environment import environment_report
from .policy import PolicyLattice, PolicySnapshot, PolicyViolation
from .contracts import (
    ApprovalDenied, ApprovalRequired, ArtifactRef, Autonomy, Budget, BudgetExhausted,
    CapabilityUnavailable, ComponentKind, ComponentManifest, ContextItem,
    ContextProjection, ContractViolation, DelegationContract, EgressDenied, EventEnvelope,
    ModelProfile, PolicyDenied, Principal, PSHError, RiskTier, RunEnvelope,
    VerificationFailed,
)
from .evidence import (
    Certainty, Claim, ClaimSupport, ClaimSupportVerifier, Directness, Evidence,
    EvidenceRecord, Relationship, RetractionStatus, SourceType, VerifiedSpan,
)
from .kernel import (
    AuthorityLattice, ExecutionResult, IngressGateway, ModelCallResult, ModelUsage,
    PersistenceGateway, Quarantine, TrustedKernel, ValidationStatus,
)
from .labels import (
    DataLabel, Declassification, Destination, Labeled, Sensitivity, combine,
    deep_label_of, unwrap_deep,
)
from .licensing import (
    INTEGRATION_MODES, LICENSE_CLASSES, LicenseClass, LicenseDecision, LicenseRuling,
    classify_license, license_ruling,
)
from .profiles import PROFILES, WorkProfile, get_profile, profile_names
from .runtime import DEFAULT_SYSTEM_PROMPT, RunResult, Runner
from .workgraph import EdgeKind, NodeKind, WorkGraph

#: Kept in step with ``pyproject.toml`` by ``tests/test_review_v5_1.py``. It read
#: "0.4.0" through the whole of v0.5, so ``psh.__version__`` named a release two
#: behind the package it was reporting on.
__version__ = "0.5.3"

__all__ = [
    "TrustedKernel", "PSHConfig", "Runner", "RunResult", "WorkGraph", "NodeKind",
    "EdgeKind", "RunEnvelope", "Budget", "Principal", "RiskTier", "Autonomy",
    "ComponentManifest", "ComponentKind", "ContextItem", "ContextProjection",
    "DelegationContract", "EventEnvelope", "ModelProfile", "ArtifactRef",
    "DataLabel", "Labeled", "Declassification", "Sensitivity", "Destination", "combine",
    "Claim", "Evidence", "ClaimSupport", "ClaimSupportVerifier", "Certainty",
    "Relationship", "Directness",
    "WorkProfile", "PROFILES", "get_profile", "profile_names",
    "PSHError", "PolicyDenied", "EgressDenied", "BudgetExhausted", "ApprovalRequired",
    "ApprovalDenied", "VerificationFailed", "CapabilityUnavailable", "ContractViolation",
    "DEFAULT_SYSTEM_PROMPT", "default_state_dir", "environment_report", "__version__",
    "PolicySnapshot", "PolicyLattice", "PolicyViolation", "AuthorityLattice", "IngressGateway", "PersistenceGateway",
    "Quarantine", "ExecutionResult", "ModelCallResult", "ModelUsage", "ValidationStatus",
    "EvidenceRecord", "VerifiedSpan", "SourceType", "RetractionStatus",
    "deep_label_of", "unwrap_deep",
    "LicenseClass", "LicenseDecision", "LicenseRuling", "classify_license",
    "license_ruling", "INTEGRATION_MODES", "LICENSE_CLASSES",
]

"""The HarnessAdapter: composition, not source-code fusion.

The review is emphatic on this point, and it is the reason this package exists alongside
``sable`` rather than replacing it:

    "Do not merge the two into one giant Python package. It should be composition, not
    source-code fusion."

So psh never imports sable's internals, never subclasses its types, and never vendors its
code. It talks to it through this protocol. The practical consequences:

* sable keeps its own tests, versioning and release cycle. Its 144-test suite still passes
  unchanged, because nothing in it was touched.
* A future statistics harness, clinical-evidence harness or writing harness implements the
  same twelve methods and is reachable through the same capability resolution.
* A domain harness with thousands of capabilities contributes ONE manifest to the top-level
  registry. Its internals are retrieved only after a task routes into its domain — the
  hierarchical retrieval the review describes.

An adapter is also a governance boundary. Its ``describe()`` declares the highest data
label it will accept and which destinations it reaches, so the kernel can refuse a delegation
to it *before* any of its code runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from ..contracts import (
    ArtifactRef, ComponentKind, ComponentManifest, RunEnvelope, new_id, utc_now,
)
from ..evidence.support import ClaimSupport, Evidence
from ..labels import Destination, Sensitivity

__all__ = ["HarnessManifest", "ExecutionPlan", "RunHandle", "RunStatus", "HealthReport",
           "EvidenceBundle", "ProvenanceCapsule", "HarnessAdapter"]


@dataclass(frozen=True, slots=True)
class HarnessManifest:
    """What a domain harness is, at the level the control plane needs.

    Deliberately does not enumerate capabilities. That is the whole point of two-level
    retrieval: the control plane holds this manifest, and asks ``discover()`` only once a
    task has been routed into the domain.
    """

    id: str
    name: str
    version: str
    description: str
    domains: tuple[str, ...] = ()
    capability_count: int = 0
    max_label: Sensitivity = Sensitivity.RESEARCH_DEIDENTIFIED
    destinations: tuple[Destination, ...] = (Destination.LOCAL_COMPUTE,)
    supports_evidence: bool = False
    supports_checkpoint: bool = False
    supports_provenance: bool = False
    execution: str = "nested_harness"
    license: str = ""

    def as_component(self) -> ComponentManifest:
        """Render as a ComponentManifest so the registry can rank harnesses uniformly."""
        return ComponentManifest(
            id=self.id, name=self.name, kind=ComponentKind.HARNESS, version=self.version,
            description=self.description, domain=self.domains[0] if self.domains else "",
            intents=self.domains, tags=self.domains, backend=self.execution,
            max_label=self.max_label, destinations=self.destinations,
            requires_network=any(d in (Destination.PUBLIC_REMOTE, Destination.TRUSTED_REMOTE)
                                 for d in self.destinations),
            provenance={"capability_count": self.capability_count,
                        "license": self.license})


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """What a harness intends to do, before it is authorised to do it."""

    harness_id: str
    capability_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    estimated_tokens: int = 0
    estimated_usd: float = 0.0
    reaches: tuple[Destination, ...] = (Destination.LOCAL_COMPUTE,)
    requires_approval: bool = False
    id: str = field(default_factory=lambda: new_id("pln"))


@dataclass(frozen=True, slots=True)
class RunHandle:
    """A reference to work in progress inside a harness."""

    run_id: str
    harness_id: str
    at: float = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class RunStatus:
    run_id: str
    state: str                     # running | complete | failed | cancelled
    detail: str = ""
    output: Any = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Evidence a harness produced, with support verdicts where it computed them."""

    run_id: str
    evidence: tuple[Evidence, ...] = ()
    supports: tuple[ClaimSupport, ...] = ()


@dataclass(frozen=True, slots=True)
class ProvenanceCapsule:
    """Enough to reproduce, or to explain why exact reproduction is not promised.

    The review's distinction, which this type encodes: a deterministic pipeline replays
    exactly, while a remote generative model replays *provenance-equivalently*. Promising
    bitwise reproducibility for a model call would be false, so ``determinism`` states which
    guarantee applies.
    """

    run_id: str
    harness_id: str
    determinism: str = "provenance_equivalent"   # exact | near_exact | provenance_equivalent
    component_versions: Mapping[str, str] = field(default_factory=dict)
    model: Mapping[str, str] = field(default_factory=dict)
    prompt_hash: str = ""
    code_commit: str = ""
    dataset_hashes: tuple[str, ...] = ()
    package_lock: str = ""
    container_digest: str = ""
    random_seed: int | None = None
    policy_version: str = ""
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.determinism not in ("exact", "near_exact", "provenance_equivalent"):
            raise ValueError(f"unknown determinism class {self.determinism!r}")


@runtime_checkable
class HarnessAdapter(Protocol):
    """The twelve methods a domain harness must expose to be composable.

    Implementations must not require psh to know anything about their internals. A method
    that cannot be supported should raise ``NotImplementedError`` and the corresponding
    ``supports_*`` flag on the manifest should be False, so the control plane can plan
    around the gap rather than discover it at runtime.
    """

    def describe(self) -> HarnessManifest:
        """Return the harness manifest. Must be cheap: called during retrieval."""
        ...

    def discover(self, query: str, *, limit: int = 20) -> Sequence[ComponentManifest]:
        """Level-2 retrieval: capabilities within this harness matching ``query``."""
        ...

    def prepare(self, capability_id: str, payload: Mapping[str, Any],
                envelope: RunEnvelope) -> ExecutionPlan:
        """Return what would be done, without doing it. Must not perform egress."""
        ...

    def execute(self, plan: ExecutionPlan, envelope: RunEnvelope) -> RunHandle:
        """Begin execution under the authority of ``envelope``."""
        ...

    def status(self, run_id: str) -> RunStatus:
        ...

    def cancel(self, run_id: str) -> bool:
        ...

    def checkpoint(self, run_id: str) -> str:
        """Return an opaque checkpoint reference, or raise NotImplementedError."""
        ...

    def resume(self, checkpoint_ref: str) -> RunHandle:
        ...

    def artifacts(self, run_id: str) -> Sequence[ArtifactRef]:
        ...

    def evidence(self, run_id: str) -> EvidenceBundle:
        ...

    def provenance(self, run_id: str) -> ProvenanceCapsule:
        ...

    def health(self) -> "HealthReport":
        ...


@dataclass(frozen=True, slots=True)
class HealthReport:
    harness_id: str
    ok: bool
    detail: str = ""
    checked_at: float = field(default_factory=utc_now)

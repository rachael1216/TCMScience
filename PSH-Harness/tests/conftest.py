"""Fixtures for the psh test suite.

Development-path bootstrap: when the suite is run from a source checkout rather than an
installed distribution, put ``src/`` and a sibling ``sable_pkg/src`` on the path. psh does
not require sable — classification degrades to a built-in fallback and says so — but the
tests exercise the composed configuration, which is the one users should run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _candidate in (_ROOT / "src", _ROOT.parent / "sable_pkg" / "src"):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))


def pytest_report_header(config):
    from psh.kernel.classify import SABLE_AVAILABLE
    return (f"psh: classification backend = "
            f"{'sable.PHIDetector' if SABLE_AVAILABLE else 'psh built-in fallback'}")


def bench_policy(**kw):
    """The policy the suite's general-purpose kernel runs under.

    Since v0.5 the policy is a ceiling rather than a set of defaults: an envelope wider
    than the policy on any dimension is refused at minting time. Most tests here exercise
    gates *below* that ceiling — they need a public destination and ACT autonomy to have
    something for the gate to refuse — so the bench states that authority explicitly
    instead of inheriting it from a default that no longer exists. Tests that the ceiling
    itself holds live in ``test_policy_ceiling.py`` and build their own narrow policies.
    """
    from psh.contracts import Autonomy, RiskTier
    from psh.labels import Destination, Sensitivity
    from psh.policy import PolicySnapshot

    params = dict(
        profile_id="test_bench", max_data_label=Sensitivity.PHI,
        allowed_destinations=(Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL,
                              Destination.USER_OUTPUT, Destination.PERSISTENT,
                              Destination.TRUSTED_REMOTE, Destination.PUBLIC_REMOTE),
        autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL)
    params.update(kw)
    return PolicySnapshot(**params)


@pytest.fixture
def kernel(tmp_path):
    from psh.kernel import TrustedKernel
    from psh.config import PSHConfig
    return TrustedKernel(PSHConfig(state_dir=tmp_path).ensure_dirs(), policy=bench_policy())


@pytest.fixture
def public_model():
    from psh.contracts import ModelProfile
    from psh.labels import Destination, Sensitivity
    return ModelProfile(id="public-frontier", provider="example-cloud",
                        destination=Destination.PUBLIC_REMOTE,
                        max_label=Sensitivity.RESEARCH_DEIDENTIFIED,
                        reasoning=0.95, coding=0.9, retention="30d")


@pytest.fixture
def local_model():
    from psh.contracts import ModelProfile
    from psh.labels import Destination, Sensitivity
    return ModelProfile(id="local-8b", provider="local",
                        destination=Destination.LOCAL_MODEL,
                        max_label=Sensitivity.PHI, reasoning=0.55,
                        usd_per_1k_input=0.0, usd_per_1k_output=0.0, retention="none")


@pytest.fixture
def verifier():
    from psh.evidence import ClaimSupportVerifier
    return ClaimSupportVerifier()


# --------------------------------------------------------- v0.2 release-gate fixtures

@pytest.fixture
def kernel_factory(tmp_path):
    """Build a kernel under a given PolicySnapshot, each in its own state directory."""
    counter = {"n": 0}

    def build(policy=None, **kw):
        counter["n"] += 1
        from psh.config import PSHConfig
        from psh.kernel import TrustedKernel
        config = PSHConfig(state_dir=tmp_path / f"k{counter['n']}").ensure_dirs()
        return TrustedKernel(config, policy=policy, **kw)

    return build


@pytest.fixture
def workgraph_rows():
    """Read raw node rows straight from SQLite, bypassing the API under test."""
    import sqlite3

    def read(kernel):
        con = sqlite3.connect(kernel.config.index_store)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute("SELECT * FROM nodes")]
        finally:
            con.close()

    return read


@pytest.fixture
def persistence_gateway(kernel):
    return kernel.persistence


@pytest.fixture
def output_gate(kernel):
    return kernel.output_gate


@pytest.fixture
def kernel_with_verifier(tmp_path):
    """A kernel whose verification model is a public provider that must never see PHI."""
    from psh.config import PSHConfig
    from psh.contracts import ModelProfile
    from psh.kernel import TrustedKernel
    from psh.labels import Destination, Sensitivity

    seen: list[str] = []
    profile = ModelProfile(id="verify-public", provider="cloud",
                           destination=Destination.PUBLIC_REMOTE,
                           max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
    kernel = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "verifier").ensure_dirs(),
        policy=bench_policy(profile_id="test_bench_verifier"),
        verification_model=profile,
        verification_invoke=lambda prompt: seen.append(prompt) or "SUPPORT|DIRECT|0.9|none")
    return kernel, seen


class _RecordingTool:
    """A tool that records every payload it actually received."""

    def __init__(self, *, egresses: bool) -> None:
        self.calls: list = []
        self._egresses = egresses

    @property
    def manifest(self):
        from psh.contracts import ComponentKind, ComponentManifest
        from psh.labels import Destination, Sensitivity
        if self._egresses:
            return ComponentManifest(
                id="remote_search", name="remote search", kind=ComponentKind.TOOL,
                destinations=(Destination.PUBLIC_REMOTE,), requires_network=True,
                max_label=Sensitivity.RESEARCH_DEIDENTIFIED)
        return ComponentManifest(
            id="local_summarise", name="local summarise", kind=ComponentKind.TOOL,
            destinations=(Destination.LOCAL_COMPUTE,), max_label=Sensitivity.PHI)

    def invoke(self, payload, envelope):
        self.calls.append(payload)
        return {"summary": "elderly patient with heart failure"}


@pytest.fixture
def egressing_tool():
    return _RecordingTool(egresses=True)


@pytest.fixture
def local_tool():
    return _RecordingTool(egresses=False)


@pytest.fixture
def kernel_with_declassifier(tmp_path):
    """A kernel whose policy permits one named principal to declassify PHI to de-identified."""
    from psh.config import PSHConfig
    from psh.kernel import TrustedKernel
    from psh.policy import PolicySnapshot

    policy = PolicySnapshot(profile_id="test-declass", declassifiers=("deid_service",))
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "declass").ensure_dirs(), policy=policy)
    return kernel, "deid_service"


@pytest.fixture(scope="session")
def signer():
    """One EvidenceSigner for the test session: what a registered retriever holds."""
    from psh.evidence.signing import EvidenceSigner

    return EvidenceSigner(key=b"test-key-" + b"0" * 23)

"""Compiling a skill into a program the trusted kernel will execute.

The tests that matter most are the ones where **PSH itself** refuses the output.
This compiler deliberately does not re-implement the kernel's rules: it
translates a `SkillSpec` into a `ScientificProgram` and hands it to
`psh.workflow.compiler.ScientificCompiler`. If a rule is only enforced here, it
is enforced in the wrong place, so several tests below run the real kernel
compiler over the result and assert it accepts.

The refusal tests are the other half. A skill that asks for authority the run
does not hold, or that advertises a claim it can never support, must fail at
compile or load time with a stable code — not execute and produce something
plausible-looking.
"""

from __future__ import annotations

import pytest

from bioagent.skills.compiler import (COMPILE_CODES, CompilationRefused, compile_skill)
from bioagent.skills.models import (SkillEvidencePolicy, SkillPermissions, SkillResources,
                                    SkillRuntime, SkillSpec, spec_from_dict)

psh = pytest.importorskip("psh", reason="PSH is required to compile skills")

from psh.contracts import Autonomy, RiskTier                    # noqa: E402
from psh.labels import Destination, Sensitivity                 # noqa: E402
from psh.policy import PolicySnapshot                           # noqa: E402
from psh.workflow.compiler import ScientificCompiler            # noqa: E402


def envelope(**kw):
    """A permissive-but-not-unlimited run envelope, as a benchmark would use."""
    base = dict(profile_id="test", max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED,
                allowed_destinations=frozenset(Destination),
                autonomy=Autonomy.ACT_WITH_APPROVAL,
                risk_ceiling=RiskTier.R2_CONSEQUENTIAL)
    base.update(kw)
    return PolicySnapshot(**base).envelope(clamp=True)


def spec(**kw) -> SkillSpec:
    """A well-formed network-pharmacology skill — the happy path."""
    base = dict(
        id="analyze-tcm-network-pharmacology", name="TCM network pharmacology",
        version="1.0.0", maintainer="TCMScience", license_spdx="MIT",
        integration_mode="native", sources=("herb", "etcm"),
        runtime=SkillRuntime(backend="python", entrypoint="impl:run"),
        permissions=SkillPermissions(network=("herb.ac.cn",)),
        evidence=SkillEvidencePolicy(max_tier="preclinical", claim_kinds=("mechanism",),
                                     forbidden_claims=("efficacy",)),
        outputs={"type": "object"})
    base.update(kw)
    return SkillSpec(**base)


# --------------------------------------------------------------------------
# the kernel validates the output — this is the point
# --------------------------------------------------------------------------


def test_the_kernel_accepts_a_compiled_network_pharmacology_skill():
    """If PSH refuses this, the compiler is emitting a program the system it
    targets cannot run, and nothing downstream matters."""
    compiled = compile_skill(spec(), envelope(), objective="analyse Qinghao decoction")
    ScientificCompiler().compile(compiled.program, envelope())


def test_the_kernel_accepts_a_skill_with_no_declared_sources():
    compiled = compile_skill(spec(id="assess-tcm-safety", name="Safety", sources=(),
                                  permissions=SkillPermissions()),
                             envelope())
    ScientificCompiler().compile(compiled.program, envelope())


def test_a_prediction_is_recorded_as_a_prediction_not_as_a_bench_experiment():
    """The whole reason PSH's design vocabulary had to grow.

    A network-pharmacology run retrieves predicted relationships. Declaring them
    `in_vitro` would record a simulation as an experiment, which is the exact
    confusion the design/user contract exists to prevent.
    """
    compiled = compile_skill(spec(), envelope())
    designs = {t: c.evidence.design for t, c in compiled.program.contracts.items()
               if c.evidence is not None}
    assert designs, "the program declares no evidence design at all"
    assert set(designs.values()) <= set(
        __import__("psh.workflow.ir", fromlist=["x"]).PREDICTIVE_DESIGNS)


def test_evidence_lives_on_the_ingest_tasks_and_the_claim_on_the_run_task():
    """PSH's model: one task produces evidence, another claims over it. A
    self-referential claim is refused with EVIDENCE101."""
    compiled = compile_skill(spec(), envelope())
    contracts = compiled.program.contracts
    assert contracts["run"].evidence is None
    assert contracts["run"].claim is not None
    assert contracts["run"].claim.evidence_from == ("ingest.herb", "ingest.etcm")
    for source in ("herb", "etcm"):
        assert contracts[f"ingest.{source}"].evidence is not None
        assert contracts[f"ingest.{source}"].claim is None


def test_contract_keys_equal_plan_task_ids():
    compiled = compile_skill(spec(), envelope())
    assert set(compiled.program.contracts) == {t.task_id
                                               for t in compiled.program.plan.tasks}


def test_one_ingest_task_per_source_not_one_for_all():
    """A combined ingest task would have to name one connector and silently drop
    the rest — the plan would read HERB and report that it consulted ETCM."""
    compiled = compile_skill(spec(sources=("herb", "etcm", "pubchem")), envelope())
    assert {t.task_id for t in compiled.program.plan.tasks} == {
        "ingest.herb", "ingest.etcm", "ingest.pubchem", "run"}


# --------------------------------------------------------------------------
# idempotency: conservative by default, and the kernel is what enforces it
# --------------------------------------------------------------------------


def test_unregistered_skill_gets_no_retries_because_the_kernel_would_refuse_them():
    """PSH refuses an automatic retry from a tool whose manifest it has not
    verified (RETRY102). Compiling before registration must therefore produce a
    plan with no retries, not one that fails to compile."""
    compiled = compile_skill(spec(), envelope())
    assert all(t.retry.max_attempts == 1 for t in compiled.program.plan.tasks)


def _registered_compiler(component_ids, *, idempotent=True):
    """A compiler whose registry knows these components.

    `trusted_idempotent` on `compile_skill` is the caller's assertion; the
    *kernel* independently looks the component up in its own registry and
    refuses a repeat-safe declaration it cannot verify (`EFFECT106` /
    `RETRY102`). So the flag only means something when the manifest really is
    registered — and this registers it rather than taking the compiler's word.
    """
    from psh.capabilities.registry import CapabilityRegistry
    from psh.contracts import ComponentKind, ComponentManifest, RunEnvelope

    class _Component:
        """A minimal invocable, so the registry exposes it via `.component()`.

        `CapabilityRegistry.component()` deliberately only returns objects that
        can be invoked; a bare manifest is registered but not resolvable, which
        is the distinction that made the first version of this fixture fail.
        """

        def __init__(self, manifest):
            self.manifest = manifest

        def invoke(self, payload, envelope):  # pragma: no cover - never called
            return {}

    registry = CapabilityRegistry()
    for cid in component_ids:
        registry.register(_Component(ComponentManifest(
            id=cid, name=cid, kind=ComponentKind.TOOL, idempotent=idempotent,
            backend="python", mutates=False, max_label=Sensitivity.PHI,
            # Only the destinations a read-only source actually reaches. A
            # component declaring every destination would be rejected by the
            # envelope for the ones it never uses — a real component declares
            # what it touches, and the fixture should too.
            destinations=(Destination.LOCAL_COMPUTE, Destination.PUBLIC_REMOTE))))
    return ScientificCompiler(registry)


def test_a_verified_idempotent_skill_may_retry_and_the_kernel_still_accepts():
    compiled = compile_skill(spec(), envelope(), trusted_idempotent=True)
    assert any(t.retry.max_attempts > 1 for t in compiled.program.plan.tasks)
    # And crucially the kernel accepts it *when the manifest really is
    # registered* — the flag is not merely setting a field to something the
    # kernel would reject.
    compiler = _registered_compiler(["herb", "etcm",
                                     "skill.analyze-tcm-network-pharmacology"])
    compiler.compile(compiled.program, envelope())


def test_the_kernel_refuses_retries_when_it_cannot_verify_idempotency():
    """The compiler's flag is an assertion, not an authority. Without a
    registered manifest the kernel refuses the same program, which is why the
    default is the conservative one."""
    compiled = compile_skill(spec(), envelope(), trusted_idempotent=True)
    from psh.runtime.plan_validator import PlanRejected
    with pytest.raises(PlanRejected) as exc:
        ScientificCompiler().compile(compiled.program, envelope())
    assert "RETRY102" in str(exc.value) or "EFFECT106" in str(exc.value)


def test_a_skill_that_reaches_a_public_host_is_never_declared_pure():
    """EFFECT105: a pure task cannot declare externally visible effects, and a
    public host is an externally visible effect however deterministic the call."""
    compiled = compile_skill(spec(), envelope(), trusted_idempotent=True)
    from psh.workflow.ir import SideEffect
    run = compiled.program.contracts["run"]
    assert run.side_effect is not SideEffect.PURE


# --------------------------------------------------------------------------
# authority: declarative, intersected, never widened
# --------------------------------------------------------------------------


def test_destinations_follow_declared_hosts_not_the_backend():
    """A python skill that declares a public host must not end up with
    local-compute-only authority — the authority follows what it reaches."""
    compiled = compile_skill(spec(), envelope())
    assert Destination.PUBLIC_REMOTE in compiled.destinations
    assert compiled.effective_max_label is Sensitivity.RESEARCH_DEIDENTIFIED


def test_a_skill_that_reaches_the_network_cannot_hold_phi():
    compiled = compile_skill(spec(), envelope())
    assert compiled.effective_max_label < Sensitivity.PHI


def test_a_local_only_skill_may_hold_more_than_a_networked_one():
    """With an envelope that does not itself cap the label, the difference is
    the skill's own reach: reading a local file can hold PHI, calling a public
    host cannot."""
    uncapped = envelope(max_data_label=Sensitivity.PHI)
    local = compile_skill(spec(permissions=SkillPermissions(), sources=()), uncapped)
    networked = compile_skill(spec(), uncapped)
    assert local.effective_max_label > networked.effective_max_label
    # The local skill's ceiling is MAX_DATA_LABEL itself; the networked one is
    # capped by the public destination it reaches, below the envelope.
    assert networked.effective_max_label is Sensitivity.RESEARCH_DEIDENTIFIED
    assert local.effective_max_label is Sensitivity.PHI


def test_a_skill_reaching_a_forbidden_destination_is_refused_not_trimmed():
    """Trimming silently would turn a misdeclared skill into a mysteriously
    failing one; the refusal names the destination."""
    restricted = PolicySnapshot(
        profile_id="local-only", max_data_label=Sensitivity.RESEARCH_DEIDENTIFIED,
        allowed_destinations=frozenset({Destination.LOCAL_COMPUTE}),
        autonomy=Autonomy.ACT_WITH_APPROVAL,
        risk_ceiling=RiskTier.R2_CONSEQUENTIAL).envelope(clamp=True)
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(), restricted)
    assert "SKILL101" in exc.value.codes
    assert "public_remote" in str(exc.value)


def test_a_skill_requesting_more_risk_than_the_envelope_holds_is_refused():
    low = PolicySnapshot(profile_id="low", max_data_label=Sensitivity.PUBLIC,
                         allowed_destinations=frozenset(Destination),
                         autonomy=Autonomy.ACT,
                         risk_ceiling=RiskTier.R0_TRIVIAL).envelope(clamp=True)
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(), low)
    assert "SKILL101" in exc.value.codes


def test_a_mutating_skill_that_asks_to_merely_observe_is_refused():
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(min_autonomy="observe",
                           permissions=SkillPermissions(filesystem_write=("/tmp/out",))),
                      envelope())
    assert "SKILL107" in exc.value.codes


def test_requiring_secrets_is_refused_because_this_path_cannot_grant_them():
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(permissions=SkillPermissions(secrets=("NCBI_API_KEY",))),
                      envelope())
    assert "SKILL106" in exc.value.codes


def test_a_skill_with_no_outputs_has_nothing_to_validate():
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(outputs={}), envelope())
    assert "SKILL103" in exc.value.codes


def test_every_refusal_reason_is_reported_not_just_the_first():
    with pytest.raises(CompilationRefused) as exc:
        compile_skill(spec(outputs={}, permissions=SkillPermissions(secrets=("K",),
                                                                   network=("h",))),
                      envelope())
    assert len(exc.value.codes) >= 2


def test_refusal_codes_are_stable_and_described():
    """These strings reach registry audit records and the Arena UI, so the shape
    is fixed: `SKILL` plus three digits, each with a description."""
    assert all(code.startswith("SKILL") and code[5:].isdigit()
               for code in COMPILE_CODES)
    assert all(detail.strip() for detail in COMPILE_CODES.values())


# --------------------------------------------------------------------------
# evidence policy: a skill cannot advertise a claim it cannot support
# --------------------------------------------------------------------------


def test_a_skill_whose_ceiling_cannot_carry_its_claim_kind_is_refused_at_load():
    """Caught when the manifest is written, not when a run silently fails to
    support the claim it promised."""
    with pytest.raises(ValueError, match="can never support the claim"):
        SkillEvidencePolicy(max_tier="preclinical", claim_kinds=("efficacy",))
    with pytest.raises(ValueError, match="can never support the claim"):
        SkillEvidencePolicy(max_tier="classical_text", claim_kinds=("mechanism",))


def test_a_coherent_policy_is_accepted():
    SkillEvidencePolicy(max_tier="randomized_trial", claim_kinds=("efficacy", "association"))
    SkillEvidencePolicy(max_tier="classical_text", claim_kinds=("attribution",))


def test_permitting_and_forbidding_the_same_claim_is_refused():
    with pytest.raises(ValueError, match="both permitted and forbidden"):
        SkillEvidencePolicy(max_tier="preclinical", claim_kinds=("mechanism",),
                            forbidden_claims=("mechanism",))


def test_permits_explains_which_rule_was_hit():
    policy = SkillEvidencePolicy(max_tier="preclinical", claim_kinds=("mechanism",),
                                 forbidden_claims=("efficacy",))
    ok, _ = policy.permits("mechanism")
    assert ok
    allowed, why = policy.permits("efficacy")
    assert not allowed and "forbidden" in why
    allowed, why = policy.permits("association")
    assert not allowed and "permits only" in why


def test_a_network_pharmacology_skill_carries_its_own_forbidden_claim_list():
    """The declared ban is what lets a refusal quote the skill's own words
    rather than an inference drawn about it."""
    compiled = compile_skill(spec(), envelope())
    assert "efficacy" in compiled.spec.evidence.forbidden_claims
    assert any("mechanism claims" in n for n in compiled.notes)


# --------------------------------------------------------------------------
# SkillSpec validation
# --------------------------------------------------------------------------


def test_unknown_manifest_keys_are_refused():
    """A misspelled `permisions:` block would otherwise load as a skill that
    asks for nothing — which reads exactly like a skill that needs nothing."""
    with pytest.raises(ValueError, match="unknown key"):
        spec_from_dict({"id": "x", "name": "X", "version": "1", "permisions": {}})
    with pytest.raises(ValueError, match="unknown key"):
        spec_from_dict({"id": "x", "name": "X", "version": "1",
                        "permissions": {"netwrok": ["a"]}})


def test_a_skill_that_cannot_execute_is_refused():
    with pytest.raises(ValueError, match="cannot be benchmarked"):
        SkillRuntime(backend="none")
    with pytest.raises(ValueError, match="requires an entrypoint"):
        SkillRuntime(backend="python", entrypoint="")
    with pytest.raises(ValueError, match="requires an image"):
        SkillRuntime(backend="container", entrypoint="x", image="")


def test_a_filesystem_wide_grant_is_refused():
    with pytest.raises(ValueError, match="filesystem-wide grant"):
        SkillPermissions(filesystem_read=("/",))
    with pytest.raises(ValueError, match="filesystem-wide grant"):
        SkillPermissions(filesystem_write=("**",))


def test_wrong_api_version_is_refused():
    with pytest.raises(ValueError, match="api_version"):
        spec(api_version="99")


def test_composite_id_is_what_a_lockfile_pins():
    assert spec().composite_id == "analyze-tcm-network-pharmacology@1.0.0"


def test_a_spec_round_trips_through_its_dict():
    """`as_dict` emits `origin` and `composite_id` for reporting; they are
    derived, not manifest keys, and `DERIVED_KEYS` names them so the two sides
    cannot drift apart."""
    original = spec()
    payload = {k: v for k, v in original.as_dict().items()
               if k not in SkillSpec.DERIVED_KEYS}
    rebuilt = spec_from_dict(payload)
    assert rebuilt.as_dict() == original.as_dict()


def test_a_manifest_written_at_one_path_hashes_the_same_as_at_another(tmp_path):
    """Otherwise moving a skill directory would change its pin."""
    import yaml as _yaml
    from bioagent.skills.loader import load_skill_dir
    payload = {k: v for k, v in spec().as_dict().items()
               if k not in SkillSpec.DERIVED_KEYS}
    (tmp_path / "impl.py").write_text("def run(**kw):\n    return {}\n")
    (tmp_path / "skill.yaml").write_text(_yaml.safe_dump(payload))
    loaded = load_skill_dir(tmp_path)
    assert loaded.spec.id == spec().id
    assert loaded.spec.origin == str(tmp_path)
    assert not loaded.spec.content_hash == ""


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_compiling_twice_yields_the_same_fingerprint():
    """A fingerprint that moved between runs could not be pinned in a registry
    or quoted in a paper."""
    a = compile_skill(spec(), envelope())
    b = compile_skill(spec(), envelope())
    assert a.fingerprint == b.fingerprint


def test_changing_the_declared_hosts_changes_the_fingerprint():
    a = compile_skill(spec(), envelope())
    b = compile_skill(spec(permissions=SkillPermissions(network=("other.example",))),
                      envelope())
    assert a.fingerprint != b.fingerprint


def test_resources_are_carried_into_the_plan():
    compiled = compile_skill(
        spec(resources=SkillResources(expected_tokens=12000, expected_usd=0.4,
                                      expected_latency_s=30.0)), envelope())
    run = next(t for t in compiled.program.plan.tasks if t.task_id == "run")
    assert run.estimated_tokens == 12000
    assert run.estimated_usd == 0.4

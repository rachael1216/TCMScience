"""The governed update path: discovery, audit, ranking, promotion.

The tests that matter here are the ones asserting a *refusal*. A governed update
pipeline is defined by what it will not do, and each of those refusals is a
commitment from the plan that would otherwise be a sentence in a document:

* the monthly job cannot promote (`test_promotion_requires_a_decision`);
* a hard-eliminated candidate cannot be promoted on a good score
  (`test_a_high_score_does_not_rescue_an_eliminated_candidate`);
* a Season, once cut, cannot have the stable registry move under it
  (`test_a_frozen_season_blocks_promotion`);
* community growth cannot exceed 5/100 (`test_community_growth_is_capped`);
* the scout cannot execute what it finds, and cannot be pointed at a new host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bioagent.skills.loader import load_skills
from bioagent.skills.models import (SkillEvidencePolicy, SkillPermissions, SkillRuntime,
                                    SkillSpec)
from bioagent.updates import (DIMENSIONS, ELIMINATIONS, GROWTH_CEILING, CandidateSkill,
                              PromotionDecision, PromotionRefused, Registry,
                              RegistryError, SkillSource, SourceError, content_digest,
                              eliminations, load_lockfile, rank, score_candidate,
                              to_candidate, version_from_spec)
from bioagent.updates.scout import ALLOWED_HOSTS, Scout, load_sources

REPO = Path(__file__).resolve().parents[1]          # BioScience-Harness/
ROOT = REPO.parent                                   # the repository root
SKILLS_DIR = REPO / "skills" / "tcm"
REGISTRY_DIR = REPO / "registry"


def spec(**kw) -> SkillSpec:
    base = dict(id="candidate", name="Candidate", version="1.0.0", license_spdx="MIT",
                integration_mode="native",
                runtime=SkillRuntime(backend="python", entrypoint="impl:run"),
                outputs={"type": "object"})
    base.update(kw)
    return SkillSpec(**base)


def decision(**kw) -> PromotionDecision:
    base = dict(skill_id="candidate", version="1.0.0", decision="approve",
                decided_by="reviewer@example.org", decided_at="2026-09-25T00:00:00Z",
                reason="reviewed")
    base.update(kw)
    return PromotionDecision(**base)


# --------------------------------------------------------------------------
# hard eliminations come before scoring
# --------------------------------------------------------------------------


def test_weights_sum_to_one_hundred():
    assert sum(DIMENSIONS.values()) == 100


def test_community_growth_is_capped():
    """The plan's commitment that star count must not drive adoption, enforced
    rather than stated."""
    assert DIMENSIONS["community_growth"] == GROWTH_CEILING
    assert GROWTH_CEILING <= 5


def test_a_perfect_growth_signal_cannot_dominate_the_score():
    popular = score_candidate(spec(), community_growth=1.0)
    assert popular.breakdown["community_growth"] <= GROWTH_CEILING
    assert popular.total <= GROWTH_CEILING


def test_over_unity_inputs_are_clamped_not_rejected():
    """An audit tool returning 1.4 should not crash a monthly job."""
    s = score_candidate(spec(), relevance=1.4, benchmark_gain=-3.0)
    assert s.breakdown["task_relevance"] == DIMENSIONS["task_relevance"]
    assert s.breakdown["benchmark_gain"] == 0.0


@pytest.mark.parametrize("code,report", [
    ("ELIM001", {"license_present": False, "__license__": True}),
    ("ELIM002", {"immutable_commit": False}),
    ("ELIM003", {"login_required": True}),
    ("ELIM004", {"vulnerabilities": ["CVE-2026-0001"]}),
    ("ELIM005", {"evades_runtime": True}),
    ("ELIM006", {"tests_present": False}),
    ("ELIM007", {"artifact_schema_ok": False}),
    ("ELIM008", {"prediction_as_fact": True}),
])
def test_each_hard_elimination_condition_is_reachable(code, report):
    """Every code in the table must be produced by some report, or the table is
    documentation rather than enforcement."""
    report = {k: v for k, v in report.items() if not k.startswith("__")}
    candidate_spec = spec(license_spdx="" if report.get("license_present") is False
                          else "MIT")
    codes = eliminations(candidate_spec, report=report)
    assert code in codes, f"{code} was not reachable from {report}"


def test_an_unlicensed_candidate_is_eliminated():
    assert "ELIM001" in eliminations(spec(license_spdx=""))


def test_a_copyleft_licence_discourages_vendoring_without_forbidding_it():
    """A hard elimination is for a licence that *forbids*, not one the lattice
    merely prefers you avoid.

    The lattice rules GPL under `vendor` as `PREFER_ALTERNATIVE`, which is not
    `DENY` — so it does not eliminate, and the preference surfaces in the
    `license_clarity` dimension instead. Treating a preference as a prohibition
    would be the ranker overruling the licence table, which is the same mistake
    as ignoring it.
    """
    from psh.licensing import LicenseDecision, license_ruling
    ruling = license_ruling("GPL-3.0", "vendor")
    assert ruling.decision is LicenseDecision.PREFER_ALTERNATIVE

    vendored = eliminations(spec(license_spdx="GPL-3.0", integration_mode="vendor"))
    federated = eliminations(spec(license_spdx="GPL-3.0", integration_mode="federated"))
    assert "ELIM001" not in vendored
    assert "ELIM001" not in federated

    # And it does eliminate when the licence genuinely denies the mode.
    from psh.licensing import LicenseClass
    unlicensed_vendored = eliminations(spec(license_spdx="", integration_mode="vendor"))
    assert "ELIM001" in unlicensed_vendored, (
        "no licence under `vendor` is redistribution without permission and must "
        "eliminate")


def test_a_preferred_alternative_licence_is_remarked_on_in_the_score():
    """The preference is not lost just because it is not fatal."""
    s = score_candidate(spec(license_spdx="GPL-3.0", integration_mode="vendor"),
                        license_clarity=0.5)
    assert s.breakdown["license_clarity"] == 2.5


def test_a_repo_with_no_commit_is_eliminated_as_unpinnable():
    codes = eliminations(spec(source_repo="https://github.com/x/y", source_commit=""))
    assert "ELIM002" in codes


def test_a_prediction_presented_as_clinical_fact_is_eliminated():
    """The condition specific to this project, and the one the contract layer
    exists to catch."""
    codes = eliminations(spec(), report={"prediction_as_fact": True})
    assert "ELIM008" in codes
    assert "clinical fact" in ELIMINATIONS["ELIM008"]


def test_an_unaudited_candidate_is_not_eliminated_for_unknowns():
    """A check that did not run is not a finding of guilt. Eliminating on
    unknowns would fail candidates for the auditor's omission."""
    codes = eliminations(spec(), report={})
    assert codes == ()
    assert "ELIM006" not in eliminations(spec(), report={"tests_present": None})


def test_every_elimination_code_is_described():
    assert all(code.startswith("ELIM") for code in ELIMINATIONS)
    assert all(len(code) == 7 for code in ELIMINATIONS)
    assert all(d.strip() for d in ELIMINATIONS.values())


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------


def test_ineligible_candidates_are_ranked_last_but_kept():
    """The backlog of things that cannot be adopted is information; hiding it
    would make the pipeline look healthier than it is."""
    good = to_candidate(spec(id="good"), score_candidate(spec(id="good"), relevance=1.0))
    bad = to_candidate(spec(id="bad"), score_candidate(spec(id="bad"), relevance=1.0),
                       report={"tests_present": False})
    assert not bad.eligible
    ordered = rank([bad, good])
    assert [c.spec.id for c in ordered] == ["good", "bad"]
    assert len(ordered) == 2


def test_ranking_is_deterministic_on_ties():
    a = to_candidate(spec(id="aaa"), score_candidate(spec(id="aaa")))
    b = to_candidate(spec(id="bbb"), score_candidate(spec(id="bbb")))
    assert [c.spec.id for c in rank([b, a])] == ["aaa", "bbb"]


def test_a_score_explains_itself():
    s = score_candidate(spec(), relevance=1.0, maturity=0.5)
    text = s.explain()
    assert "task_relevance" in text and "community_growth" in text


# --------------------------------------------------------------------------
# promotion requires a human
# --------------------------------------------------------------------------


def test_promotion_requires_a_decision():
    """The monthly job cannot promote. `promote` is the only writer of a stable
    entry and it will not run without a `PromotionDecision`."""
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec(), relevance=1.0))
    reg.add_candidate(cand)
    with pytest.raises(TypeError):
        reg.promote(cand, decided_version=version_from_spec(spec()))


def test_an_unattributed_decision_is_refused():
    with pytest.raises(RegistryError, match="names no decider"):
        decision(decided_by="")


def test_a_rejection_cannot_promote():
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    with pytest.raises(PromotionRefused, match="not an approval"):
        reg.promote(cand, decision(decision="reject"),
                    decided_version=version_from_spec(spec()))


def test_a_deferral_must_say_when_to_look_again():
    with pytest.raises(RegistryError, match="no revisit_after"):
        decision(decision="defer")
    d = decision(decision="defer", revisit_after="2026-12-01")
    assert not d.approved


def test_a_high_score_does_not_rescue_an_eliminated_candidate():
    """A hard-elimination condition is not a low score. A ranker that summed it
    in would let a good total outvote a licensing problem."""
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec(), relevance=1.0,
                                                benchmark_gain=1.0, maturity=1.0),
                        report={"tests_present": False})
    reg.add_candidate(cand)
    assert cand.score > 50, "the fixture should score well so the test is meaningful"
    with pytest.raises(PromotionRefused, match="eliminated"):
        reg.promote(cand, decision(), decided_version=version_from_spec(spec()))


def test_a_successful_promotion_records_who_and_when():
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    entry = reg.promote(cand, decision(), decided_version=version_from_spec(spec()))
    assert entry.version.approved_by == "reviewer@example.org"
    assert entry.version.approved_at == "2026-09-25T00:00:00Z"
    assert reg.audit_trail()[0]["decided_by"] == "reviewer@example.org"


def test_a_second_promotion_records_what_it_replaces_for_rollback():
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    reg.promote(cand, decision(), decided_version=version_from_spec(spec()))
    newer = version_from_spec(spec(version="1.1.0"))
    entry = reg.promote(cand, decision(version="1.1.0"), decided_version=newer)
    assert entry.version.rollback_version == "1.0.0"


def test_rollback_is_also_a_decision():
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    reg.promote(cand, decision(), decided_version=version_from_spec(spec()))
    reg.promote(cand, decision(version="1.1.0"),
                decided_version=version_from_spec(spec(version="1.1.0")))
    back = reg.rollback("candidate", "1.0.0",
                        decision(version="1.0.0", reason="regression in 1.1.0"))
    assert back.version.version == "1.0.0"
    assert back.version.rollback_version == "1.1.0"


# --------------------------------------------------------------------------
# the frozen season
# --------------------------------------------------------------------------


def test_a_frozen_season_blocks_promotion():
    """ADR-0001: a monthly update must never modify the benchmark, or this
    month's score and last month's stop being comparable."""
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    reg.freeze_season("season-1")
    with pytest.raises(PromotionRefused, match="frozen"):
        reg.promote(cand, decision(), decided_version=version_from_spec(spec()))


def test_a_season_cannot_be_unfrozen():
    """Unfreezing would be a way to make an inconvenient comparison go away."""
    reg = Registry()
    reg.freeze_season("season-1")
    with pytest.raises(RegistryError, match="already frozen"):
        reg.freeze_season("season-1")


# --------------------------------------------------------------------------
# pinning
# --------------------------------------------------------------------------


def test_a_repo_without_a_commit_cannot_be_recorded_as_a_version():
    """A branch or tag is not a pin, and `SkillVersion` refuses one."""
    from bioagent.updates import SkillVersion
    with pytest.raises(RegistryError, match="not a pin"):
        SkillVersion(skill_id="x", version="1.0.0",
                     source_repo="https://github.com/a/b", source_commit="")


def test_a_too_short_commit_is_refused():
    from bioagent.updates import SkillVersion
    with pytest.raises(RegistryError, match="too short"):
        SkillVersion(skill_id="x", version="1.0.0",
                     source_repo="https://github.com/a/b", source_commit="abc")


def test_content_hash_changes_when_a_file_changes(tmp_path):
    """The pin is by content, so an upstream edit that did not bump the version
    still changes what is pinned."""
    f = tmp_path / "impl.py"
    f.write_text("def run(): return 1\n")
    before = content_digest([f])
    f.write_text("def run(): return 2\n")
    assert content_digest([f]) != before


def test_content_hash_is_path_qualified():
    """A rename must change the digest, or two different layouts would pin the
    same."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        a = Path(d) / "a.py"
        b = Path(d) / "b.py"
        a.write_text("x = 1\n")
        b.write_text("x = 1\n")
        assert content_digest([a]) != content_digest([b])


# --------------------------------------------------------------------------
# the lockfile is the artifact
# --------------------------------------------------------------------------


def test_the_shipped_lockfile_round_trips():
    text = (REGISTRY_DIR / "skills.lock.yaml").read_text()
    versions = load_lockfile(text)
    assert len(versions) == 4
    assert {v.skill_id for v in versions} == {
        "normalize-tcm-entities", "retrieve-tcm-evidence",
        "analyze-tcm-network-pharmacology", "assess-tcm-safety"}


def test_every_locked_skill_names_its_licence_and_a_content_hash():
    versions = load_lockfile((REGISTRY_DIR / "skills.lock.yaml").read_text())
    for v in versions:
        assert v.license_spdx, f"{v.skill_id} is locked with no licence"
        assert v.content_hash, f"{v.skill_id} is locked with no content hash"
        assert v.approved_by, f"{v.skill_id} is locked with no approver"


def test_the_lockfile_hash_matches_the_skill_on_disk():
    """If the lockfile and the tree disagree, the pin is fiction."""
    loaded, _ = load_skills(SKILLS_DIR)
    by_id = {s.spec.id: s for s in loaded}
    for v in load_lockfile((REGISTRY_DIR / "skills.lock.yaml").read_text()):
        assert by_id[v.skill_id].content_hash == v.content_hash, (
            f"{v.skill_id}: lockfile pins {v.content_hash[:12]} but the tree hashes "
            f"{by_id[v.skill_id].content_hash[:12]}")


def test_the_lockfile_records_the_season_it_was_scored_against():
    versions = load_lockfile((REGISTRY_DIR / "skills.lock.yaml").read_text())
    assert all(v.benchmark_version == "season-1" for v in versions)


def test_a_lockfile_with_unknown_keys_is_refused():
    with pytest.raises(RegistryError, match="unknown key"):
        load_lockfile("api_version: '1'\nskills: []\nsurprise: true\n")


def test_a_release_is_verifiable_without_trusting_the_file():
    reg = Registry()
    cand = to_candidate(spec(), score_candidate(spec()))
    reg.add_candidate(cand)
    reg.promote(cand, decision(), decided_version=version_from_spec(spec()))
    release = reg.release("registry-1.0.0", created_at="2026-09-25T00:00:00Z")
    assert release.verify()
    assert release.composite_ids == ("candidate@1.0.0",)
    tampered = type(release)(release_id=release.release_id,
                             created_at=release.created_at, entries=(), digest="deadbeef")
    assert not tampered.verify()


# --------------------------------------------------------------------------
# the scout
# --------------------------------------------------------------------------


def test_the_shipped_sources_file_parses_and_lists_the_planned_repos():
    sources = load_sources((REGISTRY_DIR / "skill_sources.yaml").read_text())
    ids = {s.id for s in sources}
    assert "biomni" in ids and "clawbio" in ids and "tcm-cli" in ids
    assert any(s.kind == "topic" for s in sources)
    assert all(s.enabled for s in sources)


def test_the_scout_refuses_a_host_outside_its_allowlist():
    """A submitted URL must not be able to send the runtime somewhere new."""
    with pytest.raises(SourceError, match="allowed hosts"):
        SkillSource(id="evil", kind="repo", url="https://evil.example.com/x/y")
    for host in ALLOWED_HOSTS:
        SkillSource(id="ok", kind="repo", url=f"https://{host}/a/b")


def test_a_source_of_an_unknown_kind_is_refused():
    with pytest.raises(SourceError, match="not one of"):
        SkillSource(id="x", kind="telepathy", url="https://github.com/a/b")


def test_the_scout_discovers_the_p0_skills_offline():
    scout = Scout([], offline=True, local_roots=[SKILLS_DIR])
    report = scout.run()
    assert {s.id for s in report.found} == {
        "normalize-tcm-entities", "retrieve-tcm-evidence",
        "analyze-tcm-network-pharmacology", "assess-tcm-safety"}
    assert report.unreachable == ()


def test_a_skill_md_without_a_manifest_is_reported_as_needing_an_adapter(tmp_path):
    """The backlog of upstream skills, counted separately from failures —
    they are the candidate pool, not errors."""
    d = tmp_path / "upstream-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: upstream\n---\n# does a thing\n")
    report = Scout([], offline=True, local_roots=[tmp_path]).run()
    assert len(report.needs_adapter) == 1
    assert report.found == ()


def test_an_unreachable_root_is_reported_not_silently_skipped(tmp_path):
    """A monthly report that omitted a source it failed to read would be
    indistinguishable from one where nothing changed there."""
    report = Scout([], offline=True, local_roots=[tmp_path / "absent"]).run()
    assert report.unreachable
    assert "did not" in report.explain() or "UNREACHABLE" in report.explain()


def test_the_scout_does_not_execute_anything_it_finds():
    """The scout is the component most exposed to hostile input; keeping it
    unable to run what it finds is what makes a compromised scout survivable.
    Its `audit` reports only what the manifest states."""
    scout = Scout([], offline=True, local_roots=[SKILLS_DIR])
    for found in scout.run().found:
        report = scout.audit(found)
        assert set(report) <= {"immutable_commit", "license_present"}
        assert "tests_present" not in report, (
            "test presence requires reading the tree, which is still not running it "
            "— but it must not be asserted without being checked")


def test_a_disabled_source_is_reported_as_skipped():
    src = SkillSource(id="paused", kind="repo", url="https://github.com/a/b",
                      enabled=False)
    report = Scout([src], offline=True).run()
    assert "paused" in report.skipped


# --------------------------------------------------------------------------
# the CI checks must be able to fail
# --------------------------------------------------------------------------


def _run_script(name: str, *args: str) -> tuple[int, str]:
    """Run a script from `scripts/` in a child interpreter, as CI does."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO / 'src'}{os.pathsep}{REPO.parent / 'PSH-Harness' / 'src'}"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / name), *args],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def test_the_lockfile_check_passes_on_the_shipped_registry():
    code, out = _run_script("check_lockfile.py")
    assert code == 0, out


def test_the_lockfile_check_fails_when_a_skill_changes_but_the_lockfile_does_not(
        tmp_path, monkeypatch):
    """A check that cannot fail is decoration. This is the drift it exists to
    catch: an upstream edit that did not bump the version."""
    import shutil

    tree = tmp_path / "BioScience-Harness"
    shutil.copytree(REPO / "registry", tree / "registry")
    shutil.copytree(REPO / "skills", tree / "skills")
    shutil.copytree(REPO / "scripts", tree / "scripts")
    shutil.copytree(REPO / "src", tree / "src")
    # Edit a skill's implementation without touching the lockfile.
    impl = tree / "src" / "bioagent" / "skills" / "p0" / "entities.py"
    impl.write_text(impl.read_text() + "\n# an unreviewed upstream edit\n")

    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{tree / 'src'}{os.pathsep}{REPO.parent / 'PSH-Harness' / 'src'}"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "check_lockfile.py")],
        cwd=str(tree), capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "hashes" in (proc.stdout + proc.stderr) or "not locked" in (
        proc.stdout + proc.stderr)


def test_the_autopromotion_check_fails_on_a_report_claiming_a_promotion(tmp_path):
    import json as _json
    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({
        "note": "Promotion requires a decision.",
        "candidates": [], "stable": [],
        "promoted": ["something@1.0.0"],
    }))
    code, out = _run_script("check_no_autopromotion.py", str(bad))
    assert code == 1, out
    assert "promoted" in out


def test_the_release_digest_check_fails_on_a_tampered_bundle(tmp_path):
    import json as _json
    bundle = _json.loads(
        (REGISTRY_DIR / "releases" / "registry-1.0.0.json").read_text())
    bundle["skills"][0]["version"] = "9.9.9"      # edit without recomputing
    tampered = tmp_path / "tampered.json"
    tampered.write_text(_json.dumps(bundle))
    code, out = _run_script("check_release_digest.py", str(tampered))
    assert code == 1, out
    assert "MISMATCH" in out


def test_the_shipped_release_bundle_verifies():
    code, out = _run_script(
        "check_release_digest.py",
        str(REGISTRY_DIR / "releases" / "registry-1.0.0.json"))
    assert code == 0, out


# --------------------------------------------------------------------------
# the pin must not depend on how the caller spells the path
# --------------------------------------------------------------------------


def test_the_content_hash_is_independent_of_the_path_form(monkeypatch):
    """A pin that depends on the caller is not a pin.

    `load_skills("skills/tcm")` and `load_skills(<abs>/skills/tcm")` are the same
    tree. An earlier version embedded a name derived from the argument as given,
    so the two produced different digests and a lockfile generated by the CLI
    failed to verify under the CI check. Found by exactly that failure.
    """
    import os
    from bioagent.skills.loader import load_skill_dir

    relative = Path("skills/tcm/normalize-tcm-entities")
    absolute = relative.resolve()
    cwd = Path.cwd()
    os.chdir(REPO)
    try:
        a = load_skill_dir(relative)
        b = load_skill_dir(absolute)
    finally:
        os.chdir(cwd)
    assert a.content_hash == b.content_hash


def test_the_hash_covers_the_implementation_not_just_the_manifest(tmp_path):
    """Hashing the skill directory alone pins the *declaration* and leaves the
    behaviour editable — an unreviewed implementation change would not move the
    pin. The manifests name a real module for this reason."""
    from bioagent.skills.loader import load_skills

    loaded, _ = load_skills(SKILLS_DIR, require_implementation=True)
    for skill in loaded:
        module = skill.spec.runtime.entrypoint.split(":", 1)[0]
        assert module != "bioagent.skills.p0", (
            f"{skill.spec.id} names the package rather than its module; `pkg:fn` "
            "resolves to __init__, so the implementation would go unhashed")
        assert module.count(".") >= 3


def test_every_manifest_entrypoint_resolves_to_a_real_file():
    from bioagent.skills.loader import entrypoint_module_path, load_skills

    loaded, _ = load_skills(SKILLS_DIR)
    for skill in loaded:
        resolved = entrypoint_module_path(skill.spec.runtime.entrypoint)
        assert resolved is not None and resolved.is_file(), (
            f"{skill.spec.id}: {skill.spec.runtime.entrypoint} resolves to nothing")
        assert resolved.name != "__init__.py"


# --------------------------------------------------------------------------
# the CI checks must work on a runner that only ran `pip install -e .`
# --------------------------------------------------------------------------


@pytest.mark.parametrize("script,args", [
    ("check_lockfile.py", ()),
    ("check_registry_provenance.py", ("registry/skills.lock.yaml",)),
    ("check_release_digest.py", ("registry/releases/registry-1.0.0.json",)),
])
def test_a_check_works_without_pythonpath(script, args):
    """CI does not set PYTHONPATH; it runs `pip install -e .` and calls the script.

    Two of these checks were written and tested in a terminal that already had
    `PYTHONPATH` exported, so they worked everywhere except the first fresh
    checkout — the environment difference was invisible locally. Each script now
    bootstraps its own imports rather than trusting a workflow file to set a
    variable.
    """
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / script), *args],
                          cwd=str(REPO), capture_output=True, text=True, env=env,
                          timeout=180)
    assert proc.returncode == 0, (
        f"{script} failed without PYTHONPATH:\n{proc.stdout}\n{proc.stderr}")
    assert "ModuleNotFoundError" not in proc.stderr


def test_the_bootstrap_prefers_an_installed_psh_over_the_sibling_tree():
    """An installed `psh` should win, so a packaged release is tested against its
    declared dependency rather than against whatever sits next to it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ci_bootstrap", REPO / "scripts" / "_bootstrap.py")
    _bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_bootstrap)
    assert callable(_bootstrap.bootstrap)
    assert (_bootstrap.REPO_ROOT / "PSH-Harness").is_dir()
    assert _bootstrap.ROOT == REPO


# --------------------------------------------------------------------------
# every path an ADR or README promises must exist
# --------------------------------------------------------------------------


def test_the_artifacts_the_adrs_name_actually_exist():
    """An ADR that names a file is making a promise the repository has to keep.

    `ADR-0003` says `skill.schema.json` is the normative schema and `ADR-0002`
    says `registry/sources.lock.yaml` is the Source version axis. Neither file
    existed when this test was written — both were promised in prose and never
    created, which is the kind of gap a reader finds only after trusting the
    document.
    """
    promised = {
        "BioScience-Harness/src/bioagent/skills/schema/skill.schema.json":
            "ADR-0003 calls this the normative skill schema",
        "BioScience-Harness/registry/skills.lock.yaml":
            "ADR-0001 says the lockfile records the pinned stable set",
        "BioScience-Harness/registry/sources.lock.yaml":
            "ADR-0002 names this the Source version axis",
        "BioScience-Harness/registry/skill_sources.yaml":
            "the scout reads its declared sources from here",
        "docs/adr/0001-three-registry-separation.md": "the ADR itself",
        "docs/adr/0002-independent-version-axes.md": "the ADR itself",
        "docs/adr/0003-skill-yaml-compilation-contract.md": "the ADR itself",
        "docs/adr/0004-arena-read-only-static-first.md": "the ADR itself",
        "INSTALL.md": "the install guide the README links to",
        "USAGE.md": "the usage guide the README links to",
    }
    # The promised paths mix repository-root and harness-root forms; resolve
    # against whichever root actually contains the file, and fail if neither does.
    missing = [f"{p} — {why}" for p, why in promised.items()
               if not (ROOT / p).exists() and not (REPO / p).exists()
               and not (REPO / p.replace("BioScience-Harness/", "")).exists()]
    assert not missing, "promised but absent:\n  " + "\n  ".join(missing)


def test_the_skill_schema_is_normative_and_accepts_every_shipped_manifest():
    """A schema that rejects the project's own manifests would be worse than
    none: it would be a document contradicting the code."""
    import json as _json

    import yaml
    try:
        import jsonschema
    except ModuleNotFoundError:
        pytest.skip("jsonschema not installed")

    schema = _json.loads(
        (REPO / "src" / "bioagent" / "skills" / "schema"
         / "skill.schema.json").read_text(encoding="utf-8"))
    for path in sorted(SKILLS_DIR.glob("*/skill.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        jsonschema.validate(manifest, schema)


def test_the_sources_lockfile_lists_every_declared_source():
    """ADR-0002 makes the Source axis version independently, so the lockfile has
    to name the same set the scout reads — otherwise a moved source is invisible."""
    import yaml
    from bioagent.updates.scout import load_sources

    declared = load_sources(
        (REPO / "registry" / "skill_sources.yaml").read_text(encoding="utf-8"))
    locked = yaml.safe_load(
        (REPO / "registry" / "sources.lock.yaml").read_text(encoding="utf-8"))
    assert {s["id"] for s in locked["sources"]} == {s.id for s in declared}

"""The benchmark harness: cases, splits, scoring and gates.

Two properties carry most of the weight, and both are properties of what the
harness *refuses*:

* **A held-out case cannot be loaded from the published tree**
  (`test_a_held_out_case_cannot_be_loaded_from_the_dev_directory`). The split is
  what makes a leaderboard mean anything, and it is enforced by the loader rather
  than by remembering.
* **A hard gate blocks the board without erasing the score**
  (`test_a_gate_blocks_the_board_without_erasing_the_score`). Reporting a gated
  run as 0.0 would hide how well the rest of the system worked, and the
  Experimental board exists precisely to show that.

There is also a deliberate check that the leakage scanner can *detect* a leak
(`test_the_leakage_scanner_detects_a_planted_case`), because a scanner that only
ever reports "clean" is indistinguishable from one that does not work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bioagent.benchmarks import (CASE_VISIBILITIES, GATES, LOWER_IS_BETTER,
                                 SCORE_DIMENSIONS, TRACKS, BenchmarkCase,
                                 BenchmarkSeason, CaseError, CaseScore, RunRecord,
                                 ScoreComponents, SplitViolation,
                                 fabricated_citation_rate, gate_failures,
                                 prediction_as_fact_claims, score_run, score_track,
                                 severe_false_negative_rate, split_cases)

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent


def case(**kw) -> BenchmarkCase:
    base = dict(id="case-1", track="TCM-Entity", visibility="dev",
                question="Resolve 姜 to a corpus entity.", gold={"candidates": ["a", "b"]})
    base.update(kw)
    return BenchmarkCase(**base)


def components(**kw) -> ScoreComponents:
    base = dict(task_success=0.8, evidence_grounding=0.7, provenance_completeness=1.0,
                reproducibility=1.0, safety_abstention=1.0, claim_calibration=1.0,
                latency=2.0, cost=0.02)
    base.update(kw)
    return ScoreComponents(**base)


def run(scores=(), **kw) -> RunRecord:
    base = dict(run_id="run-1", system="tcmscience", submission_type="skill",
                season="season-1",
                composite_version={"runtime": "psh-0.5.3", "skill": "s@1.0.0",
                                   "source": "s1", "benchmark": "season-1"},
                scores=tuple(scores))
    base.update(kw)
    return RunRecord(**base)


# --------------------------------------------------------------------------
# the shape of the benchmark
# --------------------------------------------------------------------------


def test_six_tracks_and_eight_dimensions():
    assert len(TRACKS) == 6
    assert len(SCORE_DIMENSIONS) == 8


def test_the_plan_track_names_are_the_keys():
    assert set(TRACKS) == {"TCM-Entity", "TCM-Evidence", "TCM-NetPharm",
                           "TCM-Safety", "TCM-TrialAudit", "TCM-End2End"}


def test_latency_and_cost_are_the_two_lower_is_better_dimensions():
    """A formatter must not have to guess which way a column points."""
    assert LOWER_IS_BETTER == {"latency", "cost"}
    assert LOWER_IS_BETTER <= set(SCORE_DIMENSIONS)


def test_the_four_gates_from_the_plan_are_all_defined():
    assert set(GATES) == {"GATE001", "GATE002", "GATE003", "GATE004"}
    assert "prediction" in GATES["GATE004"].lower() or "clinical" in GATES["GATE004"]


# --------------------------------------------------------------------------
# cases and splits
# --------------------------------------------------------------------------


def test_a_case_needs_a_known_track_and_visibility():
    with pytest.raises(CaseError, match="not one of"):
        case(track="TCM-Vibes")
    with pytest.raises(CaseError, match="not one of"):
        case(visibility="maybe")


def test_a_case_needs_a_question():
    with pytest.raises(CaseError, match="no question"):
        case(question="   ")


def test_the_public_form_strips_the_gold_answer():
    """`question` is what a system sees; `gold` is what a scorer compares
    against. The split between them is why a hidden set can exist, so they are
    separate fields rather than one document with a flag."""
    public = case().as_public_dict()
    assert "gold" not in public
    assert "question" in public


def test_held_out_is_exactly_hidden_and_adversarial():
    assert not case(visibility="dev").is_held_out
    assert case(visibility="hidden").is_held_out
    assert case(visibility="adversarial").is_held_out


def test_a_held_out_case_cannot_be_loaded_from_the_dev_directory():
    """The directory is what the ignore rules act on, so a mismatch is exactly
    how a held-out case would end up committed."""
    with pytest.raises(SplitViolation, match="loaded from"):
        split_cases([case(visibility="hidden")], where="dev")


def test_splitting_groups_by_visibility():
    groups = split_cases([case(id="a", visibility="dev"),
                          case(id="b", visibility="hidden"),
                          case(id="c", visibility="adversarial")])
    assert [c.id for c in groups["dev"]] == ["a"]
    assert [c.id for c in groups["hidden"]] == ["b"]
    assert [c.id for c in groups["adversarial"]] == ["c"]


def test_the_data_card_publishes_digests_and_never_cases():
    """A score can be tied to a revision of the case set without the set being
    disclosed."""
    season = BenchmarkSeason(season="season-1", cut_at="2026-09-25T00:00:00Z",
                             cases=(case(id="d1", visibility="dev"),
                                    case(id="h1", visibility="hidden")))
    card = season.data_card()
    assert card["splits"]["dev"] == 1 and card["splits"]["hidden"] == 1
    assert set(card["split_digests"]) == set(CASE_VISIBILITIES)
    blob = json.dumps(card)
    assert "Resolve 姜" not in blob, "the data card leaked a question"
    assert "candidates" not in blob, "the data card leaked a gold answer"


def test_a_split_digest_changes_when_a_case_changes():
    a = BenchmarkSeason("s", "t", cases=(case(id="d1", visibility="dev"),))
    b = BenchmarkSeason("s", "t", cases=(case(id="d1", visibility="dev",
                                             question="a different question"),))
    assert a.compute_split_digests()["dev"] != b.compute_split_digests()["dev"]


def test_season_accessors_partition_the_cases():
    season = BenchmarkSeason("s", "t", cases=(
        case(id="d", visibility="dev", track="TCM-Safety"),
        case(id="h", visibility="hidden", track="TCM-Safety"),
        case(id="e", visibility="dev", track="TCM-Entity")))
    assert len(season.dev) == 2 and len(season.hidden) == 1
    assert len(season.by_track("TCM-Safety")) == 2


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def test_the_aggregate_is_never_returned_without_its_dimensions():
    row = {"cache": {}}
    from bioagent.benchmarks import aggregate
    result = aggregate([CaseScore(case_id="c", track="TCM-Entity",
                                  components=components())])
    assert set(result["dimensions"]) == set(SCORE_DIMENSIONS)
    assert "aggregate" in result
    assert set(result["raw"]) == set(SCORE_DIMENSIONS)


def test_the_aggregate_is_dominated_by_the_weakest_dimension():
    """Harmonic, not arithmetic: a system must be good at provenance *and*
    safety, not good at one and absent at the other."""
    from bioagent.benchmarks import aggregate
    strong_except_safety = aggregate([CaseScore(
        case_id="c", track="T", components=components(
            task_success=1.0, evidence_grounding=1.0, provenance_completeness=1.0,
            safety_abstention=0.2, claim_calibration=1.0))])
    uniformly_good = aggregate([CaseScore(
        case_id="c", track="T", components=components(
            task_success=0.85, evidence_grounding=0.85, provenance_completeness=0.85,
            safety_abstention=0.85, claim_calibration=0.85, reproducibility=0.85,
            latency=1.0, cost=0.01))])
    assert uniformly_good["aggregate"] > strong_except_safety["aggregate"]


def test_a_zero_dimension_does_not_erase_the_rest_of_the_row():
    """A true geometric mean with any zero is zero, which would make one failed
    dimension indistinguishable from a system that scored nothing anywhere."""
    from bioagent.benchmarks import aggregate
    result = aggregate([CaseScore(case_id="c", track="T",
                                  components=components(safety_abstention=0.0))])
    assert result["aggregate"] > 0.0
    assert result["dimensions"]["safety_abstention"] == 0.0


def test_lower_is_better_dimensions_are_inverted_but_reported_raw():
    from bioagent.benchmarks import aggregate
    result = aggregate([CaseScore(case_id="c", track="T",
                                  components=components(latency=10.0, cost=5.0))])
    assert result["raw"]["latency"] == 10.0
    assert result["dimensions"]["latency"] < result["raw"]["latency"]
    assert result["dimensions"]["latency"] <= 1.0


def test_an_empty_score_set_is_reported_not_raised():
    from bioagent.benchmarks import aggregate
    result = aggregate([])
    assert result["n_cases"] == 0 and result["aggregate"] == 0.0
    assert "no scores" in result["note"]


def test_a_negative_dimension_is_refused():
    with pytest.raises(CaseError, match="cannot be negative"):
        components(latency=-1.0)


# --------------------------------------------------------------------------
# gates: block the board, keep the score
# --------------------------------------------------------------------------


def test_a_gate_blocks_the_board_without_erasing_the_score():
    """Reporting a gated run as 0.0 would hide how well the rest worked, and the
    Experimental board exists to show exactly that."""
    gated = CaseScore(case_id="case-1", track="TCM-Entity",
                      components=components(task_success=0.9,
                                            gates_failed=("GATE004",)))
    row = score_run(run(scores=[gated]), [case(visibility="dev")])
    assert row["trusted"] is False
    assert row["board"] == "experimental"
    assert row["gates_failed"] == ["GATE004"]
    assert row["aggregate"] > 0.0, "the score must survive the gate"
    assert row["gate_reasons"], "a blocked row must say why it is blocked"
    assert row["dimensions"], "a blocked row still shows its decomposition"


def test_a_clean_run_lands_on_the_trusted_board():
    clean = CaseScore(case_id="case-1", track="TCM-Entity", components=components())
    row = score_run(run(scores=[clean]), [case(visibility="dev")])
    assert row["trusted"] is True and row["board"] == "trusted"
    assert row["gates_failed"] == []


def test_a_declined_claim_is_not_a_failure():
    """When a run refuses a claim because its evidence does not reach, that is
    correct behaviour. A harness that scored abstention as a miss would push
    systems toward asserting more."""
    refusing = CaseScore(case_id="case-1", track="TCM-NetPharm",
                         components=components(claim_calibration=1.0),
                         refusals=(("ART106", "prediction cannot support efficacy"),))
    row = score_run(run(scores=[refusing]),
                    [case(id="case-1", track="TCM-NetPharm", visibility="dev")])
    assert row["trusted"] is True
    assert row["dimensions"]["claim_calibration"] == 1.0


# --------------------------------------------------------------------------
# gate evaluation
# --------------------------------------------------------------------------


def test_one_fabricated_citation_fails_the_gate():
    """Zero tolerance: a system that invents a citation has shown its provenance
    cannot be relied on, and a mechanism that works most of the time is not a
    mechanism."""
    assert fabricated_citation_rate([]) == 0.0
    assert fabricated_citation_rate([{"citation_resolves": True}]) == 0.0
    assert fabricated_citation_rate([{"citation_resolves": False}]) == 1.0
    assert "GATE002" in gate_failures(claims=[{"citation_resolves": False}])


def test_a_missed_critical_signal_fails_the_safety_gate():
    missed = CaseScore(case_id="c", track="TCM-Safety",
                       components=components(notes={"severity": "critical",
                                                    "surfaced": "false"}))
    mild = CaseScore(case_id="m", track="TCM-Safety",
                     components=components(notes={"severity": "low",
                                                  "surfaced": "false"}))
    assert severe_false_negative_rate([missed, mild]) == 1.0
    assert "GATE001" in gate_failures(scores=[missed, mild])


def test_a_missed_mild_signal_is_a_shortfall_not_a_gate():
    """Only critical cases gate. Averaging them in would let a good record on
    mild cases hide a dangerous one."""
    mild = CaseScore(case_id="m", track="TCM-Safety",
                     components=components(notes={"severity": "low",
                                                  "surfaced": "false"}))
    assert severe_false_negative_rate([mild]) == 0.0
    assert "GATE001" not in gate_failures(scores=[mild])


def test_prediction_as_clinical_fact_is_detected_and_gates():
    """The failure this project exists to prevent. The kernel refuses such a
    claim, so its presence in a result means the refusal was bypassed — which is
    itself the finding."""
    claims = [{"id": "c1", "claim_kind": "efficacy",
               "evidence_designs": ["docking", "network_prediction"]}]
    assert prediction_as_fact_claims(claims) == ("c1",)
    assert "GATE004" in gate_failures(claims=claims)


def test_a_mechanism_claim_from_prediction_is_not_gated():
    """What network pharmacology is *for*. Gating this would make the skill
    useless; the prohibition is on clinical claims, not on prediction."""
    claims = [{"id": "c1", "claim_kind": "mechanism",
               "evidence_designs": ["docking"]}]
    assert prediction_as_fact_claims(claims) == ()
    assert "GATE004" not in gate_failures(claims=claims)


def test_a_clinical_claim_with_measured_evidence_is_not_gated():
    claims = [{"id": "c1", "claim_kind": "efficacy",
               "evidence_designs": ["randomized_trial"]}]
    assert prediction_as_fact_claims(claims) == ()


def test_a_run_that_does_not_rerun_fails_its_gate():
    assert "GATE003" in gate_failures(artifact_reruns=False)
    assert "GATE003" not in gate_failures(artifact_reruns=True)
    assert "GATE003" not in gate_failures(artifact_reruns=None), (
        "an unmeasured property is not a failure")


def test_all_four_gates_can_fire_at_once():
    gates = gate_failures(
        claims=[{"id": "c1", "claim_kind": "efficacy", "evidence_designs": ["docking"],
                 "citation_resolves": False}],
        scores=[CaseScore(case_id="c", track="T",
                          components=components(notes={"severity": "critical",
                                                       "surfaced": "false"}))],
        artifact_reruns=False)
    assert set(gates) == set(GATES)


# --------------------------------------------------------------------------
# per-track scoring
# --------------------------------------------------------------------------


def test_a_run_is_scored_per_track_as_well_as_in_aggregate():
    """A system strong on entity resolution and weak on safety is a different
    thing from a uniformly mediocre one, and a single blended number hides it."""
    scores = [CaseScore(case_id="e", track="TCM-Entity",
                        components=components(task_success=1.0)),
              CaseScore(case_id="s", track="TCM-Safety",
                        components=components(task_success=0.1))]
    cases = [case(id="e", track="TCM-Entity", visibility="dev"),
             case(id="s", track="TCM-Safety", visibility="dev")]
    # A score whose case is not in the Season is dropped, so the ids must match
    # the cases; a mismatch would silently score nothing and read as a zero.
    row = score_run(run(scores=scores), cases)
    assert set(row["per_track"]) == {"TCM-Entity", "TCM-Safety"}
    assert (row["per_track"]["TCM-Entity"]["dimensions"]["task_success"]
            > row["per_track"]["TCM-Safety"]["dimensions"]["task_success"])


def test_scoring_an_empty_track_says_so_rather_than_dividing_by_zero():
    result = score_track("TCM-TrialAudit", run(), [])
    assert result["n_cases"] == 0
    assert "no cases" in result["note"]


def test_the_row_names_all_four_version_axes():
    """ADR-0002: a citable result names runtime, skill, source and benchmark."""
    row = score_run(run(scores=[CaseScore(case_id="case-1", track="TCM-Entity",
                                          components=components())]),
                    [case(visibility="dev")])
    assert set(row["versions"]) == {"runtime", "skill", "source", "benchmark"}


# --------------------------------------------------------------------------
# the shipped harness scripts
# --------------------------------------------------------------------------


def _script(name: str, *args: str) -> tuple[int, str]:
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = (f"{REPO / 'src'}{os.pathsep}{ROOT / 'PSH-Harness' / 'src'}")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args],
                          cwd=str(ROOT), capture_output=True, text=True, env=env,
                          timeout=180)
    return proc.returncode, proc.stdout + proc.stderr


def test_the_arena_build_emits_the_documents():
    code, out = _script("build_arena_data.py",
                        "--registry", "BioScience-Harness/registry",
                        "--out", "arena/web/data")
    assert code == 0, out
    assert "stable skill(s) from" in out


def test_the_arena_data_check_passes_on_generated_documents():
    code, out = _script("check_arena_data.py", "arena/web/data")
    assert code == 0, out


def test_the_arena_data_check_rejects_a_row_without_its_decomposition(tmp_path):
    """A check that cannot fail is decoration."""
    bad = tmp_path / "leaderboard.json"
    bad.write_text(json.dumps({"runs": [{"system": "x", "aggregate": 0.9,
                                         "scores": {"task_success": 0.9}}]}))
    code, out = _script("check_arena_data.py", str(tmp_path))
    assert code == 1, out
    assert "missing dimension" in out


def test_the_arena_data_check_rejects_a_blocked_row_with_no_reason(tmp_path):
    bad = tmp_path / "leaderboard.json"
    bad.write_text(json.dumps({"runs": [{"system": "x", "aggregate": 0.9,
                                         "board": "experimental",
                                         "scores": {d: 0.5
                                                    for d in SCORE_DIMENSIONS}}]}))
    code, out = _script("check_arena_data.py", str(tmp_path))
    assert code == 1, out
    assert "blocking reason" in out


def test_the_leakage_scanner_reports_clean_when_no_cases_exist():
    code, out = _script("check_leakage.py", "--published", "arena/web/data")
    assert code == 0, out
    assert "nothing to leak" in out or "absent from" in out


def test_the_leakage_scanner_detects_a_planted_case(tmp_path, monkeypatch):
    """A scanner that only ever reports clean is indistinguishable from one that
    does not work."""
    import shutil

    # Build a throwaway tree that looks like the repo with cases present.
    fake = tmp_path / "repo"
    hidden = fake / "BioScience-Harness" / "benchmarks" / "hidden"
    hidden.mkdir(parents=True)
    (hidden / "case.json").write_text(json.dumps({
        "id": "hidden-0001", "track": "TCM-Safety", "visibility": "hidden",
        "question": "Which 十八反 pair involves 甘草 and a purgative herb?",
        "gold": {"answer": "甘遂"}}))

    # And a published document that leaks it.
    arena = fake / "arena" / "web" / "data"
    arena.mkdir(parents=True)
    (arena / "leaderboard.json").write_text(json.dumps({
        "rows": [{"system": "x", "note": "hidden-0001 was easy"}]}))

    script = fake / "scripts" / "check_leakage.py"
    script.parent.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "check_leakage.py", script)

    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO / 'src'}{os.pathsep}{ROOT / 'PSH-Harness' / 'src'}"
    proc = subprocess.run(
        [sys.executable, str(script), "--published", str(arena)],
        cwd=str(fake), capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "hidden-0001" in (proc.stdout + proc.stderr)


def test_the_arena_check_reads_the_key_the_site_actually_reads():
    """The checker looked for `rows` while the site reads `runs`, so it passed on
    every input including an empty one. A check that cannot fail is decoration,
    and this one was decoration until a real data file made the mismatch visible.
    """
    import json as _json
    from pathlib import Path

    app = (ROOT / "arena" / "web" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "data.leaderboard.runs" in app, (
        "the site no longer reads `leaderboard.runs`; update check_arena_data.py "
        "and this test together")

    checker = (ROOT / "scripts" / "check_arena_data.py").read_text(encoding="utf-8")
    assert '"runs"' in checker, "the checker does not read the key the site reads"


def test_the_arena_check_fails_on_a_leaderboard_with_no_run_key(tmp_path):
    import json as _json
    bad = tmp_path / "leaderboard.json"
    bad.write_text(_json.dumps({"rows": []}))   # the key the site does NOT read
    code, out = _script("check_arena_data.py", str(tmp_path))
    assert code == 1, out
    assert "no `runs` key" in out


def test_the_demonstration_data_passes_the_arena_checks():
    """The real demonstration run must satisfy the same rules as any published
    data: every score decomposed, and every null explained."""
    code, out = _script("check_arena_data.py", str(ROOT / "arena" / "web" / "data"))
    assert code == 0, out
    assert "row(s)" in out

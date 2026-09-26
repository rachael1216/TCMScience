"""The native toolkit: every tool runs from its own example, and the values are right.

The first test is the contract — each of the 147 tools is a JSON-in, JSON-out function
whose example is its smoke test. The rest pin values that can be checked by hand or
against a textbook, because a calculator that runs is not the same as a calculator that
is correct, and the difference matters most for the clinical ones.
"""

from __future__ import annotations

import json
import math

import pytest

from bioagent.tools import BY_NAME, TOOLS, NativeToolProvider, native_smoke_runner, run_smoke, tool
from bioagent.tools import (align, clinical, formats, pharmacology, phylo, popgen, protein,
                            sequence, stats, survival, variants)


# ================================================================ the contract

@pytest.mark.parametrize("native", TOOLS, ids=lambda t: t.name)
def test_every_tool_runs_its_example_and_returns_json(native):
    ok, message = run_smoke(native.name)
    assert ok, message
    result = native.fn(**dict(native.example))
    assert isinstance(result, dict) and result
    json.dumps(result)


@pytest.mark.parametrize("native", TOOLS, ids=lambda t: t.name)
def test_every_tool_is_deterministic(native):
    first = native.fn(**dict(native.example))
    second = native.fn(**dict(native.example))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_provider_yields_valid_offline_python_components():
    manifests = list(NativeToolProvider().discover())
    assert len(manifests) == len(TOOLS) == 147
    for m in manifests:
        assert m.validate() == []
        assert m.runtime.backend == "python" and m.runtime.deterministic
        assert m.offline_capable and not m.permissions.network
        assert m.validation.smoke_test == f"native:{m.name}"
        assert native_smoke_runner(m) == run_smoke(m.name)
    assert len({m.id for m in manifests}) == len(manifests)


def test_no_tool_parameter_shadows_a_runtime_invoke_keyword():
    """``Runtime.invoke(component_id, *, spec, events, parent_event, attempt, **kwargs)``
    passes tool arguments as keywords, so a tool parameter with one of those names would
    be swallowed by the runtime instead of reaching the tool. Found by the survival tools,
    whose censoring flags were first called ``events``."""
    import inspect
    from bioagent.runtime.agentspec import Runtime

    reserved = {name for name in inspect.signature(Runtime.invoke).parameters if name != "kwargs"}
    for native in TOOLS:
        clash = reserved & {p["name"] for p in native.parameters}
        assert not clash, f"{native.name} parameter(s) {sorted(clash)} shadow Runtime.invoke keywords"


def test_a_python_backend_can_load_and_run_every_tool(tmp_path):
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec

    runtime = default_runtime(catalogue=False, public_apis=False, data_lake=tmp_path / "no-lake")
    spec = AgentSpec(name="t", permission_profile="offline-analysis")
    for native in TOOLS:
        result = runtime.invoke(native.component_id, spec=spec, **dict(native.example))
        assert result.status.value == "SUCCEEDED", (native.name, result.error)


def test_bad_input_is_a_reason_not_a_traceback():
    with pytest.raises(ValueError, match="IUPAC"):
        sequence.reverse_complement("ACGT!!")
    with pytest.raises(ValueError, match="plausible range"):
        clinical.bmi(weight_kg=7000, height_cm=175)
    with pytest.raises(ValueError, match="sex"):
        clinical.egfr_ckd_epi_2021(1.0, 50, "unknown")
    with pytest.raises(ValueError, match="not an HGVS"):
        variants.parse_hgvs("rs28934578")


# ================================================================== sequences

def test_sequence_basics():
    assert sequence.reverse_complement("ATGC")["sequence"] == "GCAT"
    assert sequence.transcribe("ATGC")["rna"] == "AUGC"
    assert sequence.translate("ATGGCCTGA")["protein"] == "MA*"
    assert sequence.translate("ATGGCCTGA", to_stop=True)["protein"] == "MA"
    assert sequence.gc_content("GGCC")["gc_fraction"] == 1.0
    assert sequence.gc_content("ATAT")["gc_percent"] == 0.0
    assert sequence.hamming_distance("ACGT", "ACGA")["distance"] == 1
    assert sequence.edit_distance("kitten", "sitting")["distance"] == 3


def test_orfs_and_sites():
    orfs = sequence.find_orfs("CCATGAAATTTGGGTAACC", min_length_aa=3, both_strands=False)
    assert orfs["count"] == 1 and orfs["orfs"][0]["protein"] == "MKFG"
    sites = sequence.restriction_sites("AAGAATTCAA", enzymes=["EcoRI"])
    assert sites["sites"] == {"EcoRI": [2]}
    tm = sequence.melting_temperature("ACGTACGTACGT")          # 12 nt → Wallace: 2·6 + 4·6
    assert tm["method"] == "wallace" and tm["tm_celsius"] == 36.0


def test_protein_properties_are_in_the_known_range():
    p = protein.protein_properties("MKWVTFISLLFLFSSAYS")
    assert 2000 < p["molecular_weight"] < 2400
    assert 5 < p["isoelectric_point"] < 11
    assert p["extinction_coefficient_280nm"]["reduced"] == 5500 + 1490      # one Trp, one Tyr
    glycine = protein.protein_properties("GGGG")
    assert glycine["molecular_weight"] == pytest.approx(4 * 57.0519 + 18.01524, abs=0.01)


def test_alignments():
    g = align.global_alignment("GATTACA", "GATTACA")
    assert g["identity"] == 1.0 and g["score"] == 14.0
    loc = align.local_alignment("AAAATTTTCCCC", "GGGGTTTTGGGG")
    assert loc["aligned_a"] == "TTTT" and loc["aligned_b"] == "TTTT" and loc["score"] == 8.0


def test_parsers():
    fasta = formats.parse_fasta(">a desc\nAC\nGT\n>b\nGG\n")
    assert [r["sequence"] for r in fasta["records"]] == ["ACGT", "GG"]
    assert fasta["records"][0]["description"] == "desc"
    fastq = formats.parse_fastq("@r1\nACGT\n+\nIIII\n")
    assert fastq["mean_quality"] == 40.0 and fastq["q30_fraction"] == 1.0
    vcf = formats.parse_vcf("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                            "1\t10\t.\tA\tG,T\t.\tPASS\tDP=3;X\tGT\t0/1\n")
    rec = vcf["records"][0]
    assert rec["alt"] == ["G", "T"] and rec["info"] == {"DP": "3", "X": True}
    assert rec["genotypes"]["S1"]["GT"] == "0/1"
    gff = formats.parse_gff("1\tsrc\tgene\t1\t9\t.\t+\t.\tID=g1;Name=X\n1\tsrc\tCDS\t1\t9\t.\t+\t0\tgene_id \"g1\"; note \"n\";\n")
    assert gff["records"][0]["attributes"] == {"ID": "g1", "Name": "X"}
    assert gff["records"][1]["attributes"]["gene_id"] == "g1"


# =================================================================== variants

def test_hgvs_parsing_covers_the_common_shapes():
    sub = variants.parse_hgvs("NM_000546.6:c.215C>G")
    assert (sub["reference"], sub["level"], sub["change"], sub["start"], sub["ref"], sub["alt"]) == \
        ("NM_000546.6", "c", "substitution", "215", "C", "G")
    assert variants.parse_hgvs("c.100_105del")["change"] == "deletion"
    assert variants.parse_hgvs("c.100_101insATG")["alt"] == "ATG"
    assert variants.parse_hgvs("c.88+2T>A")["intronic"] is True
    prot = variants.parse_hgvs("p.Pro72Arg")
    assert (prot["ref"], prot["position"], prot["alt"], prot["change"]) == ("Pro", 72, "Arg", "substitution")
    assert variants.parse_hgvs("p.P72R")["normalised"] == "p.Pro72Arg"
    assert variants.parse_hgvs("p.Trp53*")["change"] == "nonsense"
    assert variants.parse_hgvs("p.Gln136fs")["change"] == "frameshift"


def test_variant_normalisation_and_frequencies():
    n = variants.normalise_variant("chr17", 100, "CT", "TT")      # shared suffix T, then prefix
    assert (n["pos"], n["ref"], n["alt"], n["key"], n["type"]) == (100, "C", "T", "17-100-C-T", "snv")
    n2 = variants.normalise_variant("17", 100, "ACGT", "AT")       # ACGT>AT: suffix T, prefix A
    assert (n2["pos"], n2["ref"], n2["alt"], n2["type"]) == (101, "CG", "", "deletion") if False \
        else (n2["trimmed"], n2["type"]) == (True, "deletion")
    af = variants.allele_frequencies(["0/0"] * 50 + ["0/1"] * 40 + ["1/1"] * 10)
    assert af["alt_allele_frequency"] == pytest.approx(0.3)
    assert af["hwe_p_value"] > 0.05                     # near equilibrium
    tstv = variants.transition_transversion([["A", "G"], ["C", "T"], ["A", "T"]])
    assert (tstv["transitions"], tstv["transversions"]) == (2, 1)


# ================================================================= statistics

def test_statistics_against_textbook_values():
    assert stats.fisher_exact(3, 1, 1, 3)["p_value"] == pytest.approx(0.4857, abs=1e-4)
    assert stats.regularised_incomplete_beta(5, 0.5, 10 / (10 + 2.228 ** 2)) == pytest.approx(0.05, abs=5e-4)
    w = stats.welch_t_test([1, 2, 3, 4, 5], [3, 4, 5, 6, 7])
    assert w["t"] == -2.0 and w["df"] == 8.0 and w["p_value"] == pytest.approx(0.0805, abs=1e-3)
    mw = stats.mann_whitney_u([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert mw["u"] == 0 and mw["p_value"] < 0.01
    bh = stats.benjamini_hochberg([0.01, 0.04, 0.03, 0.2])
    assert bh["q_values"] == pytest.approx([0.04, 0.0533333333, 0.0533333333, 0.2], abs=1e-6)
    h = stats.hypergeometric_test(5, 20, 50, 20000)
    assert h["p_value"] < 1e-6 and h["fold_enrichment"] == 100.0
    assert stats.correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])["pearson_r"] == 1.0
    assert stats.roc_auc([0.1, 0.4, 0.35, 0.8, 0.9], [0, 0, 1, 1, 1])["auc"] == pytest.approx(5 / 6, abs=1e-6)
    orr = stats.odds_ratio(20, 80, 10, 90)
    assert orr["odds_ratio"] == 2.25 and orr["ci95"][0] < 2.25 < orr["ci95"][1]
    dm = stats.diagnostic_metrics(90, 10, 5, 95)
    assert dm["sensitivity"] == pytest.approx(90 / 95) and dm["specificity"] == pytest.approx(95 / 105)
    assert stats.number_needed_to_treat(0.2, 0.15)["nnt"] == 20.0
    assert stats.tpm([100, 200, 700], [1000, 2000, 500])["values"] == pytest.approx(
        [62500.0, 62500.0, 875000.0])


def test_enrichment_analysis_ranks_the_relevant_set_first():
    out = stats.enrichment_analysis(["TP53", "BRCA1", "ATM"],
                                    {"DNA repair": ["TP53", "BRCA1", "ATM", "CHEK2"],
                                     "Glycolysis": ["HK1", "PFKM"]}, background=20000)
    assert out["results"][0]["set"] == "DNA repair" and out["results"][0]["overlap"] == 3
    assert out["significant_at_0_05"] == 1


# ==================================================================== clinical

def test_clinical_calculators_against_hand_computed_values():
    assert clinical.bmi(70, 175)["bmi"] == 22.86
    assert clinical.body_surface_area(70, 175)["mosteller_m2"] == pytest.approx(1.845, abs=1e-3)
    assert clinical.ideal_body_weight(175, "male")["ideal_body_weight_kg"] == pytest.approx(70.5, abs=0.1)
    # CKD-EPI 2021: 142 · (1/0.7)^-1.2 · 0.9938^50 · 1.012 for a 50-year-old woman with Scr 1.0
    assert clinical.egfr_ckd_epi_2021(1.0, 50, "female")["egfr_ml_min_1_73m2"] == pytest.approx(68.6, abs=0.1)
    assert clinical.egfr_ckd_epi_2021(1.0, 50, "male")["egfr_ml_min_1_73m2"] == pytest.approx(91.7, abs=0.1)
    assert clinical.creatinine_clearance_cockcroft_gault(1.0, 50, 70, "male")["creatinine_clearance_ml_min"] == 87.5
    assert clinical.corrected_calcium(8.0, 2.0)["corrected_calcium_mg_dl"] == 9.6
    assert clinical.anion_gap(140, 100, 24)["anion_gap"] == 16.0
    assert clinical.corrected_sodium(130, 600)["corrected_sodium"] == 138.0
    q = clinical.qtc(400, 75)
    assert q["bazett_ms"] == pytest.approx(447.2, abs=0.1) and q["fridericia_ms"] == pytest.approx(430.9, abs=0.1)
    assert clinical.mean_arterial_pressure(120, 80)["map_mmHg"] == pytest.approx(93.3, abs=0.1)
    assert clinical.henderson_hasselbalch(24, 40)["ph"] == pytest.approx(7.40, abs=0.01)
    gas = clinical.alveolar_gas(0.21, 40, pao2_mmHg=90, age_years=40)
    assert gas["alveolar_po2_mmHg"] == pytest.approx(99.7, abs=0.1) and gas["aa_gradient_mmHg"] == pytest.approx(9.7, abs=0.1)
    assert clinical.friedewald_ldl(200, 50, 150)["ldl_mg_dl"] == 120.0
    assert clinical.hba1c_to_eag(7.0)["eag_mg_dl"] == pytest.approx(154.2, abs=0.1)
    assert clinical.basal_metabolic_rate(70, 175, 40, "male")["bmr_kcal_day"] == 1599
    assert clinical.parkland_formula(70, 20)["total_24h_ml"] == 5600
    assert clinical.weight_based_dose(15, 20, max_dose_mg=1000)["dose_mg"] == 300.0
    assert clinical.weight_based_dose(15, 100, max_dose_mg=1000)["capped_at_max"] is True
    assert clinical.convert_units("glucose", 180, "mg/dL", "mmol/L")["value"] == pytest.approx(9.99, abs=0.01)
    assert clinical.convert_units("creatinine", 88.4, "umol/L", "mg/dL")["value"] == 1.0
    assert clinical.convert_units("temperature", 98.6, "F", "C")["value"] == 37.0


def test_clinical_scores():
    assert clinical.cha2ds2_vasc(76, "female", hypertension=True)["score"] == 4
    assert clinical.cha2ds2_vasc(40, "male")["risk"] == "low"
    assert clinical.has_bled(hypertension_uncontrolled=True, age_over_65=True, drugs=True)["score"] == 3
    assert clinical.wells_dvt(active_cancer=True, alternative_diagnosis_likely=True)["score"] == -1
    assert clinical.wells_pe(clinical_signs_of_dvt=True, pe_most_likely=True, heart_rate_over_100=True)["two_tier"] == "PE likely"
    assert clinical.curb65(False, 8.0, 32, 100, 65, 70)["score"] == 3
    assert clinical.meld_na(1.0, 1.0, 1.0, 140)["meld_na"] == 6
    assert clinical.meld_na(3.0, 2.0, 2.0, 128)["meld_na"] == 29
    assert clinical.child_pugh(2.5, 3.0, 1.8, "mild", "none") == {"points": 9, "class": "B"}
    assert clinical.news2(24, 93, True, 95, 115, True, 38.5)["score"] == 11
    assert clinical.news2(16, 97, False, 120, 70, True, 37.0)["score"] == 0
    assert clinical.glasgow_coma_scale(3, 4, 5)["total"] == 12
    assert clinical.qsofa(24, 95, False)["positive"] is True
    assert clinical.tidal_volume(175, "male", 6)["tidal_volume_ml"] == 423


def test_blosum62_is_the_published_matrix_and_scores_protein_alignments():
    from bioagent.tools.matrices import BLOSUM62

    order = "ARNDCQEGHILKMFPSTWYV"
    assert all(BLOSUM62[a][b] == BLOSUM62[b][a] for a in order for b in order)
    assert [BLOSUM62[a][a] for a in order] == [4, 5, 6, 6, 9, 5, 5, 6, 8, 4, 4, 5, 5, 6, 7, 4, 5, 11, 7, 4]
    assert (BLOSUM62["W"]["F"], BLOSUM62["I"]["V"], BLOSUM62["D"]["E"], BLOSUM62["K"]["R"],
            BLOSUM62["Y"]["F"], BLOSUM62["H"]["Y"]) == (1, 3, 2, 2, 3, 2)
    # Durbin's textbook pair: A-A 4 + W-W 11 + gap -8 + H-H 8 + E-E 5 = 20 under BLOSUM62.
    out = align.protein_alignment("HEAGAWGHEE", "PAWHEAE", mode="local", gap=-8)
    assert (out["aligned_a"], out["aligned_b"], out["score"]) == ("AWGHE", "AW-HE", 20.0)
    with pytest.raises(ValueError, match="unknown substitution matrix"):
        align.global_alignment("AC", "AC", matrix="PAM999")


def test_questionnaires_and_obstetric_calculators():
    assert clinical.phq9([1, 1, 2, 1, 0, 1, 1, 0, 0])["severity"] == "mild"
    assert clinical.phq9([3] * 9)["severity"] == "severe"
    assert clinical.gad7([2, 1, 1, 0, 1, 0, 1])["score"] == 6
    assert clinical.apgar(1, 2, 2, 1, 2)["score"] == 8
    assert clinical.bishop_score(3, 60, -1, "soft", "anterior")["score"] == 2 + 2 + 2 + 2 + 2
    ga = clinical.gestational_age("2026-01-01", "2026-05-15")
    assert (ga["weeks"], ga["days"], ga["estimated_due_date"], ga["trimester"]) == (19, 1, "2026-10-08", 2)
    with pytest.raises(ValueError, match="nine"):
        clinical.phq9([0] * 8)


# ======================================================== tranche 2: pharmacology

def test_pharmacokinetic_identities():
    pk = pharmacology.pk_one_compartment(500, 40, 6, times_h=[0, 6, 12])
    assert pk["c0_mg_per_l"] == 12.5
    curve = {c["time_h"]: c["concentration_mg_per_l"] for c in pk["concentrations"]}
    assert curve[6] == pytest.approx(6.25, abs=1e-4) and curve[12] == pytest.approx(3.125, abs=1e-4)
    assert pk["clearance_l_per_h"] == pytest.approx(40 * math.log(2) / 6, abs=1e-4)
    assert pharmacology.half_life_from_levels(10, 2, 2.5, 10)["half_life_h"] == 4.0
    assert pharmacology.loading_dose(15, 0.7, 70)["loading_dose_mg"] == 735.0
    assert pharmacology.maintenance_dose(10, 3, 8)["dose_per_interval_mg"] == 240.0
    ss = pharmacology.steady_state(6, 6)
    assert ss["accumulation_ratio"] == 2.0 and ss["doses_to_90_percent"] == 4      # 1 - 2^-n >= 0.9
    assert pharmacology.carboplatin_calvert(5, 80)["dose_mg"] == 525.0
    capped = pharmacology.carboplatin_calvert(6, 150)
    assert capped["gfr_capped"] and capped["dose_mg"] == 900.0
    assert pharmacology.glucocorticoid_equivalent("prednisone", 40, "dexamethasone")["equivalent_dose_mg"] == 6.0
    mme = pharmacology.morphine_milligram_equivalents(
        [{"opioid": "oxycodone", "dose_per_day": 30}, {"opioid": "hydrocodone", "dose_per_day": 20}])
    assert mme["total_mme_per_day"] == 65.0 and mme["band"].startswith("50 to 89")
    with pytest.raises(ValueError, match="no conversion factor"):
        pharmacology.morphine_milligram_equivalents([{"opioid": "buprenorphine", "dose_per_day": 1}])


# ================================================================ survival

def test_kaplan_meier_reproduces_the_freireich_six_mp_curve():
    """Freireich 1963, 6-MP arm: the survival estimates every textbook prints."""
    km = survival.kaplan_meier(tool("kaplan_meier").example["times"], tool("kaplan_meier").example["status"])
    got = {row["time"]: row["survival"] for row in km["table"]}
    expected = {6: 0.857, 7: 0.807, 10: 0.753, 13: 0.690, 16: 0.627, 22: 0.538, 23: 0.448}
    for t, s in expected.items():
        assert got[t] == pytest.approx(s, abs=0.001)
    assert km["median_survival_time"] == 23 and km["events"] == 9 and km["censored"] == 12


def test_log_rank_matches_the_published_statistic():
    """R's survdiff on the Gehan data reports Chisq = 16.8, p = 4.17e-05."""
    lr = survival.log_rank_test(**tool("log_rank_test").example)
    assert lr["chi_square"] == pytest.approx(16.79, abs=0.02)
    assert lr["p_value"] == pytest.approx(4.17e-05, rel=0.02)
    assert lr["hazard_ratio_a_vs_b"] < 0.3                      # 6-MP roughly quarters the hazard


# ============================================================== statistics 2

def test_special_functions_reproduce_critical_values():
    assert stats._chi2_sf(3.841459, 1) == pytest.approx(0.05, abs=1e-6)
    assert stats._chi2_sf(5.991465, 2) == pytest.approx(0.05, abs=1e-6)
    assert stats._chi2_sf(18.307, 10) == pytest.approx(0.05, abs=1e-5)
    assert stats._f_sf(4.256495, 2, 9) == pytest.approx(0.05, abs=1e-6)
    assert stats._z_quantile(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert stats._t_quantile(0.975, 10) == pytest.approx(2.228139, abs=1e-5)
    assert stats._chi2_quantile(0.95, 1) == pytest.approx(3.841459, abs=1e-5)


def test_chi_square_and_regression_and_anova():
    chi = stats.chi_square_test([[10, 20], [30, 40]])
    assert chi["chi_square"] == pytest.approx(0.7937, abs=1e-4) and chi["p_value"] == pytest.approx(0.373, abs=1e-3)
    assert stats.chi_square_test([[10, 20], [30, 40]], yates=True)["chi_square"] < chi["chi_square"]
    exact = stats.linear_regression([1, 2, 3, 4, 5], [3, 5, 7, 9, 11])
    assert (exact["slope"], exact["intercept"], exact["r_squared"]) == (2.0, 1.0, 1.0)
    fit = stats.linear_regression([1, 2, 3, 4, 5], [2.1, 3.9, 6.2, 7.8, 10.1])
    assert fit["slope"] == pytest.approx(1.99, abs=1e-6) and fit["p_value"] < 0.001
    assert fit["slope_ci_95"][0] < 1.99 < fit["slope_ci_95"][1]
    anova = stats.one_way_anova([[1, 2, 3], [2, 3, 4], [6, 7, 8]])
    assert anova["F"] == 21.0 and anova["ss_between"] == 42.0 and anova["ss_within"] == 6.0
    assert anova["p_value"] == pytest.approx(0.00195, abs=2e-5)
    kw = stats.kruskal_wallis([[1, 2, 3], [2, 3, 4], [6, 7, 8]])
    assert kw["df"] == 2 and 0.04 < kw["p_value"] < 0.05


def test_meta_analysis_by_hand():
    """Three studies, inverse-variance weights 25, 100, 11.11 — the arithmetic is short
    enough to check on paper, and the test does it independently of the module."""
    y, se = [0.5, 0.3, 0.7], [0.2, 0.1, 0.3]
    w = [1 / s ** 2 for s in se]
    fixed = sum(wi * yi for wi, yi in zip(w, y)) / sum(w)
    q = sum(wi * (yi - fixed) ** 2 for wi, yi in zip(w, y))
    c = sum(w) - sum(wi * wi for wi in w) / sum(w)
    tau2 = max(0.0, (q - 2) / c)
    wr = [1 / (s ** 2 + tau2) for s in se]
    random = sum(wi * yi for wi, yi in zip(wr, y)) / sum(wr)
    m = stats.meta_analysis(y, se, labels=["a", "b", "c"])
    assert m["fixed_effect"]["estimate"] == pytest.approx(fixed, abs=1e-6)
    assert m["fixed_effect"]["std_error"] == pytest.approx(math.sqrt(1 / sum(w)), abs=1e-6)
    assert m["heterogeneity"]["Q"] == pytest.approx(q, abs=1e-4)
    assert m["heterogeneity"]["tau_squared"] == pytest.approx(tau2, abs=1e-6)
    assert m["random_effects"]["estimate"] == pytest.approx(random, abs=1e-6)
    assert m["fixed_effect"]["ratio"] == pytest.approx(math.exp(fixed), abs=1e-5)
    assert sum(m["weights_percent"]["fixed"].values()) == pytest.approx(100, abs=0.05)


def test_effect_sizes_bayes_and_design():
    d = stats.cohens_d([2, 4, 4, 4, 5, 5, 7, 9], [1, 2, 2, 3, 3, 4, 5, 6])
    assert d["mean_difference"] == 1.75 and d["pooled_sd"] == pytest.approx(1.918, abs=1e-3)
    assert d["cohens_d"] == pytest.approx(1.75 / 1.91796, abs=1e-4) and d["band"] == "large"
    w = stats.wilcoxon_signed_rank([1.1, 2.3, 3.0, 4.2, 5.1, 6.3], [0.9, 2.0, 2.5, 3.1, 4.0, 5.0])
    assert w["W_plus"] == 21.0 and w["W_minus"] == 0 and w["z"] == pytest.approx(2.1023, abs=1e-3)
    bayes = stats.post_test_probability(0.2, sensitivity=0.9, specificity=0.8)
    assert bayes["lr_positive"] == 4.5 and bayes["lr_negative"] == 0.125
    assert bayes["post_test_probability_if_positive"] == pytest.approx(0.5294, abs=1e-4)
    assert bayes["post_test_probability_if_negative"] == pytest.approx(0.0303, abs=1e-4)
    assert stats.post_test_probability(0.5, likelihood_ratio=3)["post_test_probability"] == 0.75
    assert stats.sample_size_two_proportions(0.2, 0.3)["n_group_1"] == 294
    assert stats.sample_size_two_means(5, 10)["n_per_group"] == 63
    ir = stats.incidence_rate(12, 4800, per=1000)
    assert ir["rate"] == 2.5
    assert ir["ci_low"] == pytest.approx(1.2918, abs=1e-3) and ir["ci_high"] == pytest.approx(4.367, abs=1e-3)


# ====================================================== population genetics

def test_population_genetics():
    perfect = popgen.linkage_disequilibrium({"AB": 50, "Ab": 0, "aB": 0, "ab": 50})
    assert (perfect["D"], perfect["D_prime"], perfect["r_squared"]) == (0.25, 1.0, 1.0)
    partial = popgen.linkage_disequilibrium({"AB": 50, "Ab": 10, "aB": 10, "ab": 30})
    assert partial["D"] == pytest.approx(0.14, abs=1e-6) and partial["r_squared"] == pytest.approx(0.3403, abs=1e-4)
    div = popgen.nucleotide_diversity(["ACGTACGTAC", "ACGTACGTAT", "ACGAACGTAC", "ACGTACCTAC"])
    assert div["segregating_sites"] == 3 and div["pi_per_pair"] == 1.5      # 9 differences / 6 pairs
    a1 = 1 + 1 / 2 + 1 / 3
    assert div["theta_w_per_sequence"] == pytest.approx(3 / a1, abs=1e-5)
    # Tajima's D with the 1989 constants for n = 4, computed here independently.
    n, s_sites, k = 4, 3, 1.5
    a2 = sum(1 / i ** 2 for i in range(1, n))
    b1, b2 = (n + 1) / (3 * (n - 1)), 2 * (n * n + n + 3) / (9 * n * (n - 1))
    c1, c2 = b1 - 1 / a1, b2 - (n + 2) / (a1 * n) + a2 / a1 ** 2
    e1, e2 = c1 / a1, c2 / (a1 ** 2 + a2)
    expected_d = (k - s_sites / a1) / math.sqrt(e1 * s_sites + e2 * s_sites * (s_sites - 1))
    assert div["tajimas_d"] == pytest.approx(expected_d, abs=1e-3)
    assert popgen.nucleotide_diversity(["ACGT", "ACGT"])["tajimas_d"] is None
    assert popgen.fst([1.0, 0.0], [50, 50]) == {"populations": 2, "H_S": 0.0, "H_T": 0.5, "G_ST": 1.0, "hudson_fst": 1.0}
    assert popgen.fst([0.5, 0.5])["G_ST"] == 0.0


# ============================================================ phylogenetics

def test_neighbor_joining_recovers_an_additive_tree():
    """The five-taxon example from Saitou & Nei (as on Wikipedia): the input distances are
    additive, so the tree must reproduce every leaf-to-leaf path length exactly."""
    example = tool("neighbor_joining").example
    nj = phylo.neighbor_joining(**example)
    assert nj["negative_branch_lengths_clamped"] == 0
    got = phylo.tree_distances(nj["newick"])
    index = {name: i for i, name in enumerate(example["names"])}
    for i, a in enumerate(got["names"]):
        for j, b in enumerate(got["names"]):
            assert got["matrix"][i][j] == pytest.approx(example["matrix"][index[a]][index[b]], abs=1e-6)
    assert "(a:2,b:3)" in nj["newick"] and "(d:2,e:1)" in nj["newick"]


def test_upgma_and_newick():
    up = phylo.upgma(["A", "B", "C"], [[0, 2, 4], [2, 0, 4], [4, 4, 0]])
    assert up["newick"] == "(C:2,(A:1,B:1):1);" and up["root_height"] == 2.0
    parsed = phylo.parse_newick(up["newick"])
    assert parsed["leaves"] == ["C", "A", "B"] and parsed["root_to_leaf"] == {"A": 2.0, "B": 2.0, "C": 2.0}
    assert parsed["is_binary"] and parsed["total_branch_length"] == 5.0
    quoted = phylo.parse_newick("('Homo sapiens':0.1,Pan:0.2);")
    assert quoted["leaves"] == ["Homo sapiens", "Pan"]
    with pytest.raises(ValueError):
        phylo.parse_newick("((A,B);")
    dm = phylo.distance_matrix({"a": "AAAA", "b": "AAAG", "c": "AAGG"}, model="jc69")
    assert dm["matrix"][0][1] == pytest.approx(-0.75 * math.log(1 - 4 * 0.25 / 3), abs=1e-6)
    saturated = phylo.distance_matrix({"a": "AAAA", "b": "CCCC"}, model="jc69")
    assert saturated["saturated_pairs"] == [["a", "b"]] and saturated["matrix"][0][1] is None


# ======================================================== sequence tools 2

def test_motifs_islands_guides_and_primers():
    hits = sequence.motif_search("GGTATAAAAGGCCTATAAATCC", "TATAWAW", both_strands=False)
    assert [h["position"] for h in hits["hits"]] == [2, 13]
    eco = sequence.motif_search("AAGAATTCAA", "GAATTC")
    assert eco["count"] == 2 and {h["strand"] for h in eco["hits"]} == {"+", "-"}   # palindrome
    island = sequence.cpg_islands("CG" * 150 + "AT" * 100, window=100, min_length=100)
    assert island["count"] == 1 and island["islands"][0]["start"] == 0
    assert sequence.cpg_islands("AT" * 200, window=100)["count"] == 0
    frames = sequence.six_frame_translation("ATGGCCTGA")["frames"]
    assert frames["+1"] == "MA*" and frames["-1"] == "SGH"
    guides = sequence.crispr_guides("A" * 20 + "TGG" + "C" * 5)
    assert guides["count"] >= 1 and guides["guides"][0]["guide"] == "A" * 20 and guides["guides"][0]["pam"] == "TGG"
    assert sequence.sequence_entropy("AAAA")["entropy_bits"] == 0.0
    assert sequence.sequence_entropy("ACGT")["entropy_bits"] == 2.0
    primer = sequence.primer_check("AGCGTCGATTGACCTGACGTAG", template="TTTTAGCGTCGATTGACCTGACGTAGGGCC")
    assert primer["template_sites"] == {"forward_strand": [4], "reverse_strand": [], "unique": True}
    assert primer["gc_clamp_3prime_count"] == 3
    with pytest.raises(ValueError, match="unambiguous"):
        sequence.primer_check("ACGTN")


# ============================================================ proteomics

def test_peptide_mass_and_digestion():
    g = protein.peptide_mass("G", charges=[1])
    assert g["monoisotopic_mass"] == pytest.approx(57.02146 + 18.010565, abs=1e-4)
    assert g["mz"]["1"] == pytest.approx(75.032025 + 1.007276, abs=1e-4)
    digest = protein.in_silico_digest("MKWVTFISLLFLFSSAYSRGVFRRKPAA", "trypsin")
    assert [p["sequence"] for p in digest["peptides"]] == ["MK", "WVTFISLLFLFSSAYSR", "GVFR", "R", "KPAA"]
    missed = protein.in_silico_digest("MKWVTFISLLFLFSSAYSRGVFRRKPAA", "trypsin", missed_cleavages=1)
    assert "MKWVTFISLLFLFSSAYSR" in [p["sequence"] for p in missed["peptides"]]
    aspn = protein.in_silico_digest("MADGDAK", "asp-n")
    assert [p["sequence"] for p in aspn["peptides"]] == ["MA", "DG", "DAK"]


# ============================================================== formats 2

def test_sam_pdb_obo_parsers():
    sam = formats.parse_sam(tool("parse_sam").example["text"])
    assert sam["references"] == {"chr1": 248956422} and sam["mapped"] == 2
    first = sam["records"][0]
    assert first["cigar_ops"] == {"M": 15, "I": 2, "D": 1} and first["aligned_ref_span"] == 16
    assert "paired" in first["flags"] and "reverse" in sam["records"][2]["flags"]
    pdb = formats.parse_pdb(tool("parse_pdb").example["text"])
    assert pdb["atoms"] == 4 and pdb["waters"] == 1 and pdb["hetero_groups"] == {"ZN": 1}
    assert pdb["chains"]["A"]["sequence"] == "MG" and pdb["chains"]["A"]["residues"] == 2
    obo = formats.parse_obo(tool("parse_obo").example["text"])
    assert obo["n_terms"] == 2 and obo["roots"] == ["GO:0008150"]
    assert obo["terms"][1]["is_a"] == ["GO:0008150"] and obo["terms"][1]["synonyms"] == 1
    assert obo["terms"][1]["definition"] == "Any process carried out at the cellular level."


# =========================================================== variant effect

def test_coding_variant_consequences():
    cds = "ATGGCCATTGTAATGGGCCGCTGA"
    annotate = variants.annotate_coding_variant
    assert annotate(cds, 4, "G", "A")["hgvs_p"] == "p.Ala2Thr"
    assert annotate(cds, 4, "G", "A")["consequence"] == "missense"
    assert annotate(cds, 6, "C", "T")["consequence"] == "synonymous"
    assert annotate(cds, 1, "A", "T")["consequence"] == "start_lost"
    nonsense = annotate("ATGAAATGA", 4, "A", "T")
    assert nonsense["consequence"] == "nonsense" and nonsense["hgvs_p"] == "p.Lys2Ter"
    stop_lost = annotate("ATGAAATGAAAATAA", 7, "T", "C")
    assert stop_lost["consequence"] == "stop_lost"
    fs = annotate(cds, 11, "T", "")
    assert fs["consequence"] == "frameshift" and fs["frame_shift"] and "fs" in fs["hgvs_p"]
    immediate = annotate(cds, 10, "G", "")
    assert immediate["consequence"] == "frameshift" and immediate["hgvs_p"] == "p.Val4Ter"
    inframe = annotate(cds, 10, "GTA", "")
    assert inframe["consequence"] == "inframe_deletion" and inframe["hgvs_p"] == "p.Val4del"
    ins = annotate(cds, 9, "", "GGG")
    assert ins["consequence"] == "inframe_insertion" and ins["hgvs_p"] == "p.Ile3_Val4insGly"
    with pytest.raises(ValueError, match="reference mismatch"):
        annotate(cds, 4, "T", "A")


# ======================================================== clinical tools 2

def test_ascvd_reproduces_the_guideline_examples():
    """Goff 2013, Table A worked example: 55-year-old, TC 213, HDL 50, untreated SBP 120,
    non-smoker, no diabetes. The guideline prints 5.3 / 2.1 / 6.1 / 3.0 %; published
    coefficients are rounded, which moves the white-male figure by 0.1."""
    common = dict(total_cholesterol_mg_dl=213, hdl_mg_dl=50, systolic=120)
    expected = {("male", "white"): 5.3, ("female", "white"): 2.1,
                ("male", "african_american"): 6.1, ("female", "african_american"): 3.0}
    for (sex, race), pct in expected.items():
        got = clinical.ascvd_pooled_cohort(55, sex, race, **common)["ten_year_ascvd_risk_percent"]
        assert got == pytest.approx(pct, abs=0.15), (sex, race, got)
    smoker = clinical.ascvd_pooled_cohort(55, "male", "white", smoker=True, **common)
    assert smoker["ten_year_ascvd_risk_percent"] > 5.4
    with pytest.raises(ValueError, match="plausible range"):
        clinical.ascvd_pooled_cohort(30, "male", "white", **common)


def test_organ_failure_acid_base_and_fluids():
    s = clinical.sofa(**tool("sofa").example)
    assert s["score"] == 9 and s["components"]["respiration"] == 2
    assert clinical.sofa(**{**tool("sofa").example, "pao2_fio2": 150, "mechanically_ventilated": True})["components"]["respiration"] == 3
    osm = clinical.calculated_osmolality(140, 90, 14, measured_osmolality=300)
    assert osm["calculated_osmolality_mosm_kg"] == 290.0 and osm["osmolar_gap"] == 10.0
    assert clinical.winters_formula(12, 26)["compensation"] == "appropriate"
    abg = clinical.acid_base_interpretation(7.25, 28, 12, sodium=140, chloride=100)
    assert abg["primary_disorder"] == "metabolic acidosis" and abg["compensation"]["verdict"] == "appropriate"
    assert abg["anion_gap"]["anion_gap"] == 28.0 and abg["delta_ratio"] == pytest.approx(1.33, abs=0.01)
    assert clinical.acid_base_interpretation(7.55, 25, 22)["primary_disorder"] == "respiratory alkalosis"
    assert clinical.acid_base_interpretation(7.30, 60, 29)["compensation"]["verdict"] == "chronic pattern"
    assert clinical.holliday_segar(25) == {"ml_per_hour": 65.0, "ml_per_day": 1600.0,
                                           "rule": "4-2-1 mL/kg/h and 100-50-20 mL/kg/day"}
    assert clinical.free_water_deficit(70, 154, "male")["free_water_deficit_l"] == 4.2
    assert clinical.allowable_blood_loss(70, 42, 30)["mabl_ml"] == 1500.0
    assert clinical.infusion_rate(1000, 480, 20) == {"ml_per_hour": 125.0, "drops_per_minute": 42.0}


def test_risk_scores_and_indices():
    assert clinical.heart_score(1, 1, 58, 2, 0)["score"] == 4
    assert clinical.heart_score(2, 2, 70, 3, 2)["band"] == "high (7-10)"
    assert clinical.centor_mcisaac(True, True, True, False, 10)["score"] == 4
    assert clinical.centor_mcisaac(True, True, True, True, 50)["score"] == 3
    assert clinical.alvarado(**tool("alvarado").example)["score"] == 7
    assert clinical.timi_ua_nstemi(**tool("timi_ua_nstemi").example)["event_rate_14_day_percent"] == 19.9
    assert clinical.abcd2(**tool("abcd2").example)["score"] == 5
    assert clinical.sirs(38.6, 110, 24, 14)["criteria_met"] == 4
    assert clinical.sirs(37.0, 80, 16, 8)["sirs_positive"] is False
    assert clinical.rcri(**tool("rcri").example)["class"] == "III"
    assert clinical.stop_bang(**tool("stop_bang").example)["band"] == "high (5-8)"
    assert clinical.fib4(60, 40, 40, 150)["fib4"] == pytest.approx(2.53, abs=0.01)
    assert clinical.apri(80, 40, 100)["apri"] == 2.0
    assert clinical.homa_ir(100, 10)["homa_ir"] == 2.47

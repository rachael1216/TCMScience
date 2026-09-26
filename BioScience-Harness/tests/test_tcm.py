"""The TCM knowledge layer: typed entities, a scoped knowledge base, honest tiers.

The 2026-09-18 review's F11 asked for a representation in which a formula is not a
compound, a herb is not its processed form, and a classical attribution is not a trial
result. These tests pin the three questions the layer exists to answer — which thing,
whether the evidence licenses the claim, whether the combination is recorded as unsafe —
and the integrity of the seed every answer rests on.
"""

from __future__ import annotations

import json

import pytest

from bioagent.tcm import (
    CLAIM_KINDS, ActionRelation, EvidenceTier, Formula, Herb, Ingredient, StudyEvidence,
    TCMKnowledgeBase, default_knowledge, seed,
)
from bioagent.tools import BY_NAME, DOMAINS, TOOLS, tool


@pytest.fixture(scope="module")
def kb():
    return seed()


# ================================================================ the seed holds

def test_the_seed_is_internally_consistent(kb):
    stats = kb.stats()
    assert stats["herbs"] >= 20 and stats["formulas"] >= 6 and stats["syndromes"] >= 8
    assert stats["studies"] == 2, "the seed cites the pharmacopoeia and a textbook, nothing invented"
    for formula in kb.formulas.values():
        assert all(i.herb_id in kb.herbs for i in formula.ingredients)
        assert all(s in kb.syndromes for s in formula.indications)
        assert any(i.role == "君" for i in formula.ingredients)
    for relation in kb.relations.values():
        assert kb.entity(relation.subject_id) is not None
        assert kb.entity(relation.object_id) is not None
        assert kb.evidence(relation.evidence_ids), relation.id
    for record in kb.safety.values():
        assert kb.entity(record.subject_id) is not None
        if record.counterpart_id:
            assert kb.entity(record.counterpart_id) is not None
    for processed in kb.processed.values():
        assert processed.herb_id in kb.herbs


def test_no_seed_relation_claims_clinical_evidence(kb):
    """A tier above EXPERT_EXPERIENCE is a published study; the seed cites none."""
    assert all(r.tier <= EvidenceTier.EXPERT_EXPERIENCE for r in kb.relations.values())
    assert all(not s.tier.needs_citation for s in kb.studies.values())


def test_a_study_at_a_clinical_tier_must_be_citable():
    with pytest.raises(ValueError, match="cites no PMID"):
        StudyEvidence(id="s", tier=EvidenceTier.RANDOMIZED_TRIAL, subject_id="herb.huangqi",
                      design="RCT")
    with pytest.raises(ValueError, match="must name the text"):
        StudyEvidence(id="s", tier=EvidenceTier.CLASSICAL_TEXT, subject_id="herb.huangqi")
    ok = StudyEvidence(id="s", tier=EvidenceTier.RANDOMIZED_TRIAL, subject_id="herb.huangqi",
                       design="RCT", registry_id="NCT00000000")
    assert ok.usable and ok.tier.clinical


def test_references_that_do_not_resolve_are_refused_whole():
    with pytest.raises(ValueError, match="unknown ingredient"):
        TCMKnowledgeBase(formulas=(Formula(id="formula.x", chinese="某方", pinyin="x", source="s",
                                           ingredients=(Ingredient(herb_id="herb.nope", role="君"),)),))
    with pytest.raises(ValueError, match="cites no evidence"):
        ActionRelation(id="r", subject_id="a", predicate="treats", object_id="b",
                       tier=EvidenceTier.CLASSICAL_TEXT)
    with pytest.raises(ValueError, match="predicate"):
        ActionRelation(id="r", subject_id="a", predicate="cures", object_id="b",
                       tier=EvidenceTier.CLASSICAL_TEXT, evidence_ids=("p",))
    with pytest.raises(ValueError, match="role"):
        Ingredient(herb_id="herb.huangqi", role="主")


def test_a_herb_is_not_its_processed_form(kb):
    zhi = kb.processed["processed.zhi_gancao"]
    assert zhi.herb_id == "herb.gancao" and "炙甘草" in zhi.names
    assert kb.resolve("炙甘草").entity is zhi
    assert kb.resolve("甘草").entity is kb.herbs["herb.gancao"]
    assert [p.chinese for p in kb.processed_forms("herb.gancao")] == ["炙甘草"]


# ============================================================ which thing?

def test_ambiguity_is_reported_not_resolved(kb):
    resolution = kb.resolve("参")
    assert resolution.ambiguous and not resolution.found
    assert {c.chinese for c in resolution.candidates} == {"人参", "丹参"}
    with pytest.raises(ValueError, match="ambiguous"):
        kb.require("参")
    assert kb.resolve("参", kind="formula").candidates == ()


@pytest.mark.parametrize("name, entity_id", [
    ("黄芪", "herb.huangqi"), ("huang qi", "herb.huangqi"), ("Huangqi", "herb.huangqi"),
    ("北芪", "herb.huangqi"), ("Astragali Radix", "herb.huangqi"), ("herb.huangqi", "herb.huangqi"),
    ("桂枝汤", "formula.guizhitang"), ("gui zhi tang", "formula.guizhitang"),
    ("阳旦汤", "formula.guizhitang"), ("表虚证", "syndrome.taiyang_zhongfeng"),
    ("芍药", "herb.baishao"),
])
def test_names_in_three_scripts_resolve(kb, name, entity_id):
    assert kb.resolve(name).entity.id == entity_id


def test_an_unknown_name_is_not_a_guess(kb):
    resolution = kb.resolve("冬虫夏草")
    assert not resolution.found and not resolution.ambiguous
    with pytest.raises(ValueError, match="names nothing"):
        kb.require("冬虫夏草")


# ============================================ does the evidence license the claim?

def test_a_classical_passage_licenses_an_attribution_and_not_an_efficacy_claim(kb):
    attribution = kb.claims_between("formula.guizhitang", "syndrome.taiyang_zhongfeng",
                                    claim_kind="attribution")
    assert attribution and attribution[0].verdict == "within_scope" and attribution[0].licensed
    efficacy = kb.claims_between("formula.guizhitang", "syndrome.taiyang_zhongfeng",
                                 claim_kind="efficacy")
    assert efficacy[0].verdict == "unsupported" and not efficacy[0].licensed
    assert "RANDOMIZED_TRIAL" in efficacy[0].reasons[0]
    assert efficacy[0].required_tier is EvidenceTier.RANDOMIZED_TRIAL
    assert efficacy[0].tier is EvidenceTier.CLASSICAL_TEXT


def test_evidence_from_one_population_is_extrapolated_to_another(kb):
    within = kb.applicability("relation.siwu_yingxue", claim_kind="attribution", population="妇人")
    assert within.verdict == "within_scope"
    outside = kb.applicability("relation.siwu_yingxue", claim_kind="attribution", population="儿童")
    assert outside.verdict == "extrapolated" and not outside.licensed
    assert "妇人" in outside.reasons[0] and "儿童" in outside.reasons[0]
    unstated = kb.applicability("relation.huangqi_qixu", claim_kind="attribution", population="老年")
    assert unstated.verdict == "within_scope"


def test_a_retracted_study_supports_nothing():
    base = seed()
    study = StudyEvidence(id="study.retracted", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.huangqi", design="RCT", pmid="00000000",
                          retracted=True, condition="heart failure", population="adults")
    relation = ActionRelation(id="relation.huangqi_hf", subject_id="herb.huangqi",
                              predicate="treats", object_id="syndrome.qixu",
                              tier=EvidenceTier.RANDOMIZED_TRIAL, evidence_ids=("study.retracted",),
                              condition="heart failure", population="adults")
    base.extend(studies=(study,), relations=(relation,))
    verdict = base.applicability("relation.huangqi_hf", claim_kind="efficacy")
    assert verdict.verdict == "unsupported" and "retracted" in verdict.reasons[0]


def test_claim_kinds_are_ordered_by_what_they_need():
    assert CLAIM_KINDS["attribution"] < CLAIM_KINDS["mechanism"] < CLAIM_KINDS["efficacy"]
    assert CLAIM_KINDS["recommendation"] is EvidenceTier.SYSTEMATIC_REVIEW
    assert EvidenceTier.CASE_REPORT.clinical and not EvidenceTier.PRECLINICAL.clinical
    assert all(t.chinese for t in EvidenceTier)


# ================================================== is the combination recorded as unsafe?

def test_shibafan_pairs_are_conflicts(kb):
    conflicts = kb.check_compatibility(["herb.gancao", "herb.gansui"])
    assert len(conflicts) == 1 and conflicts[0].record.kind == "incompatibility"
    assert "十八反" in conflicts[0].record.description
    assert kb.check_compatibility(["herb.huangqi", "herb.danggui"]) == []
    # A processed form carries its base herb's incompatibilities.
    assert kb.check_compatibility(["processed.fa_banxia", "processed.zhi_fuzi"])


def test_a_formula_inherits_its_ingredients_safety_records(kb):
    records = kb.safety_for("formula.sinitang")
    assert any(r.id == "safety.fuzi_toxicity" and r.severity == "critical" for r in records)
    assert all(c.first_id != c.second_id for c in kb.check_compatibility(
        kb.formulas["formula.sinitang"].herb_ids))
    assert kb.check_compatibility(kb.formulas["formula.sinitang"].herb_ids) == []


# ====================================================================== the tools

def test_the_tcm_tools_are_registered_native_tools():
    names = {t.name for t in TOOLS if t.domain == "tcm-knowledge"}
    assert names == {"tcm_lookup", "tcm_herb", "tcm_formula", "tcm_syndrome",
                     "tcm_compatibility", "tcm_applicability", "tcm_evidence_tiers",
                     "tcm_classical_search"}
    assert DOMAINS["tcm-knowledge"] == "clinical"
    assert BY_NAME["tcm_herb"].component_id == "native.tool.tcm_herb"


def test_lookup_never_raises_on_ambiguity_and_formula_reports_roles():
    out = tool("tcm_lookup").fn(name="参")
    assert out["ambiguous"] and {c["chinese"] for c in out["candidates"]} == {"人参", "丹参"}
    formula = tool("tcm_formula").fn(name="桂枝汤")
    roles = {i["chinese"]: i["role"] for i in formula["ingredients"]}
    assert roles == {"桂枝": "君", "白芍": "臣", "生姜": "佐", "大枣": "佐", "甘草": "使"}
    assert formula["ingredients"][-1]["processed_chinese"] == "炙甘草"
    assert formula["passages"][0]["source"] == "伤寒论"
    assert formula["compatibility"] == []
    json.dumps(formula, ensure_ascii=False)


def test_applicability_tool_says_when_a_text_is_not_a_trial():
    out = tool("tcm_applicability").fn(subject="桂枝汤", object="太阳中风证", claim_kind="efficacy")
    assert out["verdict"] == "unsupported" and not out["licensed"]
    assert out["required_tier"] == "RANDOMIZED_TRIAL"
    out = tool("tcm_applicability").fn(subject="桂枝汤", object="太阳中风证",
                                      claim_kind="attribution")
    assert out["verdict"] == "within_scope" and out["licensed"]
    none = tool("tcm_applicability").fn(subject="黄芪", object="太阳中风证")
    assert none["verdict"] == "unsupported" and none["relations"] == []
    with pytest.raises(ValueError, match="claim_kind"):
        tool("tcm_applicability").fn(subject="黄芪", object="气虚证", claim_kind="cure")


def test_compatibility_tool_resolves_formulas_and_flags_unknown_names():
    out = tool("tcm_compatibility").fn(herbs=["四逆汤", "半夏"])
    assert not out["compatible"] and out["conflicts"][0]["kind"] == "incompatibility"
    assert {c["first_id"] for c in out["conflicts"]} == {"herb.fuzi"}
    out = tool("tcm_compatibility").fn(herbs=["黄芪", "不存在的药"])
    assert not out["compatible"] and out["unresolved"][0]["name"] == "不存在的药"
    out = tool("tcm_compatibility").fn(herbs=["黄芪", "当归"])
    assert out["compatible"] and "absence of a record" in out["note"]


def test_classical_search_finds_passages_by_phrase_and_by_entity():
    by_phrase = tool("tcm_classical_search").fn(query="桂枝汤主之")
    assert by_phrase["count"] >= 1 and "桂枝汤主之" in by_phrase["passages"][0]["text"]
    by_entity = tool("tcm_classical_search").fn(query="黄芪")
    assert any(p["source"] == "神农本草经" for p in by_entity["passages"])
    tiers = tool("tcm_evidence_tiers").fn()
    assert [t["rank"] for t in tiers["tiers"]] == list(range(1, 8))


def test_the_default_knowledge_is_shared_and_read_only():
    assert default_knowledge() is default_knowledge()
    herb = default_knowledge().herbs["herb.huangqi"]
    with pytest.raises((AttributeError, TypeError)):
        herb.chinese = "x"  # type: ignore[misc]

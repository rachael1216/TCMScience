"""The TCM knowledge base: entities, relations, and the questions a claim has to answer.

Three questions the free-text harness could not ask, and this can:

* **Which thing?** 参 names 人参 and 丹参; 芍药 is 白芍 here and 赤芍 elsewhere.
  ``resolve`` returns the one entity a name denotes or the candidates it could denote,
  and never picks one silently.
* **Is this claim licensed by this evidence?** ``applicability`` compares the tier of
  the evidence behind a relation with the tier the *kind* of claim needs
  (``CLAIM_KINDS``): a classical passage licenses an attribution and not an efficacy
  claim. It also compares the scope — the population and the condition the evidence
  covers with the ones the claim is about — and reports ``extrapolated`` when they differ,
  the same rule PSH's evidence-scope model applies to biomedical abstracts.
* **Is it safe to combine?** ``check_compatibility`` looks up 十八反 pairs and any other
  recorded incompatibility between the herbs of a formula or a proposed combination.

The seed (``seed()``) is small and deliberately conservative. It carries herbs,
processed forms, formulas, syndromes and classical passages from public-domain sources,
relations at the CLASSICAL_TEXT and EXPERT_EXPERIENCE tiers, and safety records from
the pharmacopoeia and the 十八反 rhyme. It carries **no clinical studies**: a study needs
a citation that can be looked up, and none is invented here. Load them with ``extend``
from records that have one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .model import (
    CLAIM_KINDS, ActionRelation, ClassicalPassage, EvidenceTier, Formula, Herb, Ingredient,
    ProcessedHerb, SafetyRecord, StudyEvidence, Syndrome,
)

__all__ = ["TCMKnowledgeBase", "Resolution", "Applicability", "Conflict", "seed"]

_NORMALISE = re.compile(r"[\s\-_·.,()（）]+")


def _norm(name: str) -> str:
    return _NORMALISE.sub("", (name or "").strip().lower())


def _tokens(text: str) -> set[str]:
    """Scope tokens: Latin words and CJK bigrams, so 成人 and 成人患者 overlap."""
    lowered = (text or "").lower()
    out = set(re.findall(r"[a-z][a-z0-9-]+", lowered))
    for run in re.findall("[一-鿿]+", lowered):
        out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a name denotes: one entity, several candidates, or nothing."""

    query: str
    entity: Any = None
    candidates: tuple[Any, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.entity is None and len(self.candidates) > 1

    @property
    def found(self) -> bool:
        return self.entity is not None

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.query, "found": self.found, "ambiguous": self.ambiguous,
                "entity": self.entity.as_dict() if self.entity is not None else None,
                "candidates": [{"id": c.id, "chinese": getattr(c, "chinese", ""),
                                "kind": _kind_of(c)} for c in self.candidates]}


@dataclass(frozen=True, slots=True)
class Applicability:
    """Whether a relation's evidence licenses a claim of a given kind in a given scope."""

    relation_id: str
    claim_kind: str
    verdict: str                          # within_scope | extrapolated | unsupported
    tier: EvidenceTier
    required_tier: EvidenceTier
    reasons: tuple[str, ...] = ()
    requested_population: str = ""
    requested_condition: str = ""

    @property
    def licensed(self) -> bool:
        return self.verdict == "within_scope"

    def as_dict(self) -> dict[str, Any]:
        return {"relation_id": self.relation_id, "claim_kind": self.claim_kind,
                "verdict": self.verdict, "licensed": self.licensed,
                "tier": self.tier.name, "tier_zh": self.tier.chinese,
                "required_tier": self.required_tier.name,
                "required_tier_zh": self.required_tier.chinese,
                "reasons": list(self.reasons),
                "requested_population": self.requested_population,
                "requested_condition": self.requested_condition}


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two herbs that must not be combined, and the record that says so."""

    first_id: str
    second_id: str
    record: SafetyRecord

    def as_dict(self) -> dict[str, Any]:
        return {"first_id": self.first_id, "second_id": self.second_id,
                "kind": self.record.kind, "severity": self.record.severity,
                "description": self.record.description, "tier": self.record.tier.name,
                "management": self.record.management}


def _kind_of(entity: Any) -> str:
    return {Herb: "herb", ProcessedHerb: "processed", Formula: "formula",
            Syndrome: "syndrome", ClassicalPassage: "passage",
            StudyEvidence: "study"}.get(type(entity), type(entity).__name__.lower())


class TCMKnowledgeBase:
    """Entities by id and by name, relations by subject, safety by subject."""

    def __init__(self, *, herbs: Iterable[Herb] = (), processed: Iterable[ProcessedHerb] = (),
                 formulas: Iterable[Formula] = (), syndromes: Iterable[Syndrome] = (),
                 passages: Iterable[ClassicalPassage] = (),
                 studies: Iterable[StudyEvidence] = (),
                 relations: Iterable[ActionRelation] = (),
                 safety: Iterable[SafetyRecord] = ()) -> None:
        self.herbs: dict[str, Herb] = {}
        self.processed: dict[str, ProcessedHerb] = {}
        self.formulas: dict[str, Formula] = {}
        self.syndromes: dict[str, Syndrome] = {}
        self.passages: dict[str, ClassicalPassage] = {}
        self.studies: dict[str, StudyEvidence] = {}
        self.relations: dict[str, ActionRelation] = {}
        self.safety: dict[str, SafetyRecord] = {}
        self._names: dict[str, list[Any]] = {}
        self.extend(herbs=herbs, processed=processed, formulas=formulas, syndromes=syndromes,
                    passages=passages, studies=studies, relations=relations, safety=safety)

    # ------------------------------------------------------------------ loading
    def extend(self, *, herbs: Iterable[Herb] = (), processed: Iterable[ProcessedHerb] = (),
               formulas: Iterable[Formula] = (), syndromes: Iterable[Syndrome] = (),
               passages: Iterable[ClassicalPassage] = (),
               studies: Iterable[StudyEvidence] = (),
               relations: Iterable[ActionRelation] = (),
               safety: Iterable[SafetyRecord] = ()) -> "TCMKnowledgeBase":
        """Add entities; every reference must resolve, or nothing is added."""
        staged = {
            "herbs": {h.id: h for h in herbs}, "processed": {p.id: p for p in processed},
            "formulas": {f.id: f for f in formulas}, "syndromes": {s.id: s for s in syndromes},
            "passages": {p.id: p for p in passages}, "studies": {s.id: s for s in studies},
            "relations": {r.id: r for r in relations}, "safety": {s.id: s for s in safety},
        }
        known = self._ids() | {k for group in staged.values() for k in group}
        problems: list[str] = []
        for p in staged["processed"].values():
            if p.herb_id not in known:
                problems.append(f"processed {p.id}: unknown herb {p.herb_id!r}")
        for f in staged["formulas"].values():
            for i in f.ingredients:
                if i.herb_id not in known:
                    problems.append(f"formula {f.id}: unknown ingredient {i.herb_id!r}")
                if i.processed and i.processed not in known:
                    problems.append(f"formula {f.id}: unknown processed form {i.processed!r}")
            for s in f.indications:
                if s not in known:
                    problems.append(f"formula {f.id}: unknown syndrome {s!r}")
        for r in staged["relations"].values():
            for ref in (r.subject_id, r.object_id):
                if ref not in known:
                    problems.append(f"relation {r.id}: unknown entity {ref!r}")
            for e in r.evidence_ids:
                if e not in known:
                    problems.append(f"relation {r.id}: unknown evidence {e!r}")
        for s in staged["safety"].values():
            if s.subject_id not in known:
                problems.append(f"safety {s.id}: unknown subject {s.subject_id!r}")
            if s.counterpart_id and s.counterpart_id not in known:
                problems.append(f"safety {s.id}: unknown counterpart {s.counterpart_id!r}")
            for e in s.evidence_ids:
                if e not in known:
                    problems.append(f"safety {s.id}: unknown evidence {e!r}")
        for st in staged["studies"].values():
            if st.subject_id not in known:
                problems.append(f"study {st.id}: unknown subject {st.subject_id!r}")
        for p in staged["passages"].values():
            for m in p.mentions:
                if m not in known:
                    problems.append(f"passage {p.id}: unknown mention {m!r}")
        if problems:
            raise ValueError("knowledge base references do not resolve: " + "; ".join(problems[:8]))
        for name, group in staged.items():
            getattr(self, name).update(group)
            for entity in group.values():
                for alias in getattr(entity, "names", ()):
                    self._names.setdefault(_norm(alias), []).append(entity)
        return self

    def _ids(self) -> set[str]:
        return (set(self.herbs) | set(self.processed) | set(self.formulas) | set(self.syndromes)
                | set(self.passages) | set(self.studies) | set(self.relations) | set(self.safety))

    def entity(self, entity_id: str) -> Any:
        for group in (self.herbs, self.processed, self.formulas, self.syndromes, self.passages,
                      self.studies):
            if entity_id in group:
                return group[entity_id]
        return None

    # ---------------------------------------------------------------- resolving
    def resolve(self, name: str, *, kind: str = "") -> Resolution:
        """One entity for ``name``, or the candidates it could mean. Never a silent pick."""
        query = (name or "").strip()
        if not query:
            return Resolution(query=query)
        wanted = {"herb": Herb, "processed": ProcessedHerb, "formula": Formula,
                  "syndrome": Syndrome}.get(kind)

        def accept(entity: Any) -> bool:
            return wanted is None or isinstance(entity, wanted)

        direct = self.entity(query)
        if direct is not None and accept(direct):
            return Resolution(query=query, entity=direct, candidates=(direct,))
        exact = [e for e in self._names.get(_norm(query), ()) if accept(e)]
        exact = list(dict.fromkeys(exact))
        if len(exact) == 1:
            return Resolution(query=query, entity=exact[0], candidates=tuple(exact))
        if len(exact) > 1:
            return Resolution(query=query, candidates=tuple(exact))
        needle = _norm(query)
        partial: list[Any] = []
        for key, entities in self._names.items():
            if needle and (needle in key or key in needle and len(key) >= 2):
                partial.extend(e for e in entities if accept(e))
        partial = list(dict.fromkeys(partial))
        if len(partial) == 1:
            return Resolution(query=query, entity=partial[0], candidates=tuple(partial))
        return Resolution(query=query, candidates=tuple(partial))

    def require(self, name: str, *, kind: str = "") -> Any:
        """The one entity ``name`` denotes, or a ValueError that lists the alternatives."""
        resolution = self.resolve(name, kind=kind)
        if resolution.found:
            return resolution.entity
        if resolution.ambiguous:
            options = ", ".join(f"{getattr(c, 'chinese', c.id)} ({c.id})"
                                for c in resolution.candidates[:8])
            raise ValueError(f"{name!r} is ambiguous; it could mean {options}")
        raise ValueError(f"{name!r} names nothing in this knowledge base"
                         + (f" of kind {kind!r}" if kind else ""))

    # ------------------------------------------------------------------ queries
    def relations_of(self, subject_id: str = "", *, predicate: str = "",
                     object_id: str = "") -> list[ActionRelation]:
        out = [r for r in self.relations.values()
               if (not subject_id or r.subject_id == subject_id)
               and (not predicate or r.predicate == predicate)
               and (not object_id or r.object_id == object_id)]
        return sorted(out, key=lambda r: (-int(r.tier), r.id))

    def evidence(self, ids: Iterable[str]) -> list[Any]:
        return [self.passages.get(i) or self.studies.get(i) for i in ids
                if i in self.passages or i in self.studies]

    def processed_forms(self, herb_id: str) -> list[ProcessedHerb]:
        return sorted((p for p in self.processed.values() if p.herb_id == herb_id),
                      key=lambda p: p.id)

    def formulas_for(self, syndrome_id: str) -> list[Formula]:
        return sorted((f for f in self.formulas.values() if syndrome_id in f.indications),
                      key=lambda f: f.id)

    def formulas_containing(self, herb_id: str) -> list[Formula]:
        return sorted((f for f in self.formulas.values() if herb_id in f.herb_ids),
                      key=lambda f: f.id)

    def passages_mentioning(self, entity_id: str) -> list[ClassicalPassage]:
        return sorted((p for p in self.passages.values() if entity_id in p.mentions),
                      key=lambda p: p.id)

    def search_passages(self, query: str) -> list[ClassicalPassage]:
        needle = (query or "").strip()
        if not needle:
            return []
        terms = _tokens(needle)
        scored = []
        for p in self.passages.values():
            hay = _tokens(p.text + " " + p.source + " " + p.translation)
            if needle in p.text or needle in p.translation:
                score = 1.0
            else:
                score = len(terms & hay) / max(1, len(terms))
            if score > 0:
                scored.append((score, p.id, p))
        return [p for _, _, p in sorted(scored, key=lambda x: (-x[0], x[1]))]

    def safety_for(self, subject_id: str, *, include_ingredients: bool = True) -> list[SafetyRecord]:
        """A subject's safety records; a formula inherits its ingredients' records."""
        ids = {subject_id}
        if include_ingredients and subject_id in self.formulas:
            ids |= set(self.formulas[subject_id].herb_ids)
        if subject_id in self.processed:
            ids.add(self.processed[subject_id].herb_id)
        out = [s for s in self.safety.values()
               if s.subject_id in ids or (s.counterpart_id and s.counterpart_id in ids)]
        return sorted(out, key=lambda s: (-("critical", "high", "moderate", "low").index(s.severity)
                                          * -1, s.id))

    # -------------------------------------------------------------- compatibility
    def _base_id(self, entity: Any) -> str:
        return entity.herb_id if isinstance(entity, ProcessedHerb) else entity.id

    def check_compatibility(self, herb_ids: Sequence[str]) -> list[Conflict]:
        """Every recorded incompatibility among the given herbs (十八反 and others)."""
        bases = [self._base_id(self.entity(h)) if self.entity(h) is not None else h
                 for h in herb_ids]
        present = set(bases)
        conflicts: list[Conflict] = []
        for record in self.safety.values():
            if record.kind not in ("incompatibility", "interaction") or not record.counterpart_id:
                continue
            a, b = record.subject_id, record.counterpart_id
            if a in present and b in present and a != b:
                conflicts.append(Conflict(first_id=a, second_id=b, record=record))
        return sorted(conflicts, key=lambda c: (c.record.severity, c.first_id, c.second_id))

    # ------------------------------------------------------------ applicability
    def applicability(self, relation_id: str, *, claim_kind: str = "efficacy",
                      population: str = "", condition: str = "") -> Applicability:
        """Does the evidence behind ``relation_id`` license this kind of claim, here?"""
        if claim_kind not in CLAIM_KINDS:
            raise ValueError(f"claim kind {claim_kind!r} is not one of {sorted(CLAIM_KINDS)}")
        relation = self.relations[relation_id]
        required = CLAIM_KINDS[claim_kind]
        reasons: list[str] = []
        usable = [e for e in self.evidence(relation.evidence_ids)
                  if not isinstance(e, StudyEvidence) or e.usable]
        retracted = [e.id for e in self.evidence(relation.evidence_ids)
                     if isinstance(e, StudyEvidence) and not e.usable]
        if retracted:
            reasons.append(f"evidence retracted: {', '.join(retracted)}")
        if not usable:
            reasons.append("no usable evidence behind the relation")
            return Applicability(relation.id, claim_kind, "unsupported", relation.tier, required,
                                 tuple(reasons), population, condition)
        if relation.tier < required:
            reasons.append(
                f"a {claim_kind} claim needs {required.name} ({required.chinese}) evidence; "
                f"this relation rests on {relation.tier.name} ({relation.tier.chinese})")
            return Applicability(relation.id, claim_kind, "unsupported", relation.tier, required,
                                 tuple(reasons), population, condition)
        verdict = "within_scope"
        if population and relation.population and not (
                _tokens(population) & _tokens(relation.population)):
            verdict = "extrapolated"
            reasons.append(f"the evidence covers {relation.population!r}; the claim is about "
                           f"{population!r}")
        if condition and relation.condition and not (
                _tokens(condition) & _tokens(relation.condition)):
            verdict = "extrapolated"
            reasons.append(f"the evidence concerns {relation.condition!r}; the claim is about "
                           f"{condition!r}")
        if population and not relation.population and required.clinical:
            reasons.append("the evidence states no population; the claim's population "
                           "cannot be checked")
        if verdict == "within_scope" and not reasons:
            reasons.append(f"{relation.tier.name} evidence licenses a {claim_kind} claim")
        return Applicability(relation.id, claim_kind, verdict, relation.tier, required,
                             tuple(reasons), population, condition)

    def claims_between(self, subject_id: str, object_id: str, *, claim_kind: str = "efficacy",
                       population: str = "", condition: str = "") -> list[Applicability]:
        out = [self.applicability(r.id, claim_kind=claim_kind, population=population,
                                  condition=condition)
               for r in self.relations_of(subject_id, object_id=object_id)
               if r.predicate in ("treats", "indicated_for")]
        return sorted(out, key=lambda a: (("within_scope", "extrapolated", "unsupported")
                                          .index(a.verdict), -int(a.tier)))

    # ------------------------------------------------------------------- stats
    def stats(self) -> dict[str, int]:
        return {"herbs": len(self.herbs), "processed": len(self.processed),
                "formulas": len(self.formulas), "syndromes": len(self.syndromes),
                "passages": len(self.passages), "studies": len(self.studies),
                "relations": len(self.relations), "safety": len(self.safety)}


# =============================================================================== seed

def _herb(id, chinese, pinyin, latin, *, species=(), part="", nature="", flavours=(),
          meridians=(), actions=(), aliases=(), toxicity="无毒", source="") -> Herb:
    return Herb(id=f"herb.{id}", chinese=chinese, pinyin=pinyin, latin=latin, species=species,
                part=part, nature=nature, flavours=flavours, meridians=meridians,
                actions=actions, aliases=aliases, toxicity=toxicity, source=source)


_BENJING = "神农本草经"
_BIELU = "名医别录"
_SHANGHAN = "伤寒论"
_JUFANG = "太平惠民和剂局方"
_PHARMACOPOEIA = "中华人民共和国药典（2020年版）一部"
_TEXTBOOK = "《中药学》（全国中医药行业高等教育规划教材）"

_HERBS = (
    _herb("huangqi", "黄芪", "huang qi", "Astragali Radix",
          species=("Astragalus membranaceus", "Astragalus mongholicus"), part="根",
          nature="微温", flavours=("甘",), meridians=("脾", "肺"),
          actions=("补气升阳", "固表止汗", "利水消肿", "生津养血", "托毒排脓", "敛疮生肌"),
          aliases=("黄耆", "北芪", "绵芪"), source=f"{_BENJING}（上品）"),
    _herb("renshen", "人参", "ren shen", "Ginseng Radix et Rhizoma",
          species=("Panax ginseng",), part="根及根茎", nature="微温",
          flavours=("甘", "微苦"), meridians=("脾", "肺", "心", "肾"),
          actions=("大补元气", "复脉固脱", "补脾益肺", "生津养血", "安神益智"),
          aliases=("人衔", "神草"), source=f"{_BENJING}（上品）"),
    _herb("gancao", "甘草", "gan cao", "Glycyrrhizae Radix et Rhizoma",
          species=("Glycyrrhiza uralensis", "Glycyrrhiza inflata", "Glycyrrhiza glabra"),
          part="根及根茎", nature="平", flavours=("甘",), meridians=("心", "肺", "脾", "胃"),
          actions=("补脾益气", "清热解毒", "祛痰止咳", "缓急止痛", "调和诸药"),
          aliases=("国老", "甜草"), source=f"{_BENJING}（上品）"),
    _herb("danggui", "当归", "dang gui", "Angelicae Sinensis Radix",
          species=("Angelica sinensis",), part="根", nature="温", flavours=("甘", "辛"),
          meridians=("肝", "心", "脾"), actions=("补血活血", "调经止痛", "润肠通便"),
          aliases=("秦归", "云归"), source=f"{_BENJING}（中品）"),
    _herb("baizhu", "白术", "bai zhu", "Atractylodis Macrocephalae Rhizoma",
          species=("Atractylodes macrocephala",), part="根茎", nature="温",
          flavours=("甘", "苦"), meridians=("脾", "胃"),
          actions=("健脾益气", "燥湿利水", "止汗", "安胎"), aliases=("于术",),
          source=f"{_BENJING}（上品，术）"),
    _herb("fuling", "茯苓", "fu ling", "Poria",
          species=("Poria cocos",), part="菌核", nature="平", flavours=("甘", "淡"),
          meridians=("心", "肺", "脾", "肾"), actions=("利水渗湿", "健脾", "宁心"),
          aliases=("云苓", "茯菟"), source=f"{_BENJING}（上品）"),
    _herb("fuzi", "附子", "fu zi", "Aconiti Lateralis Radix Praeparata",
          species=("Aconitum carmichaelii",), part="子根的加工品", nature="大热",
          flavours=("辛", "甘"), meridians=("心", "肾", "脾"),
          actions=("回阳救逆", "补火助阳", "散寒止痛"), aliases=("附片",), toxicity="有毒",
          source=f"{_BENJING}（下品）"),
    _herb("mahuang", "麻黄", "ma huang", "Ephedrae Herba",
          species=("Ephedra sinica", "Ephedra intermedia", "Ephedra equisetina"),
          part="草质茎", nature="温", flavours=("辛", "微苦"), meridians=("肺", "膀胱"),
          actions=("发汗解表", "宣肺平喘", "利水消肿"), aliases=("龙沙",),
          source=f"{_BENJING}（中品）"),
    _herb("guizhi", "桂枝", "gui zhi", "Cinnamomi Ramulus",
          species=("Cinnamomum cassia",), part="嫩枝", nature="温", flavours=("辛", "甘"),
          meridians=("心", "肺", "膀胱"), actions=("发汗解肌", "温通经脉", "助阳化气", "平冲降气"),
          source=_BIELU),
    _herb("baishao", "白芍", "bai shao", "Paeoniae Radix Alba",
          species=("Paeonia lactiflora",), part="根", nature="微寒", flavours=("苦", "酸"),
          meridians=("肝", "脾"), actions=("养血调经", "敛阴止汗", "柔肝止痛", "平抑肝阳"),
          aliases=("芍药", "白芍药"), source=f"{_BENJING}（中品，芍药）"),
    _herb("shengjiang", "生姜", "sheng jiang", "Zingiberis Rhizoma Recens",
          species=("Zingiber officinale",), part="新鲜根茎", nature="微温", flavours=("辛",),
          meridians=("肺", "脾", "胃"), actions=("解表散寒", "温中止呕", "化痰止咳", "解鱼蟹毒"),
          source=_BIELU),
    _herb("ganjiang", "干姜", "gan jiang", "Zingiberis Rhizoma",
          species=("Zingiber officinale",), part="干燥根茎", nature="热", flavours=("辛",),
          meridians=("脾", "胃", "肾", "心", "肺"), actions=("温中散寒", "回阳通脉", "温肺化饮"),
          source=f"{_BENJING}（中品）"),
    _herb("dazao", "大枣", "da zao", "Jujubae Fructus",
          species=("Ziziphus jujuba",), part="成熟果实", nature="温", flavours=("甘",),
          meridians=("脾", "胃", "心"), actions=("补中益气", "养血安神"), aliases=("红枣",),
          source=f"{_BENJING}（上品）"),
    _herb("danshen", "丹参", "dan shen", "Salviae Miltiorrhizae Radix et Rhizoma",
          species=("Salvia miltiorrhiza",), part="根及根茎", nature="微寒", flavours=("苦",),
          meridians=("心", "肝"), actions=("活血祛瘀", "通经止痛", "清心除烦", "凉血消痈"),
          aliases=("赤参", "紫丹参"), source=f"{_BENJING}（上品）"),
    _herb("shudihuang", "熟地黄", "shu di huang", "Rehmanniae Radix Praeparata",
          species=("Rehmannia glutinosa",), part="块根的炮制品", nature="微温", flavours=("甘",),
          meridians=("肝", "肾"), actions=("补血滋阴", "益精填髓"), aliases=("熟地",),
          source=_BIELU),
    _herb("chuanxiong", "川芎", "chuan xiong", "Chuanxiong Rhizoma",
          species=("Ligusticum chuanxiong",), part="根茎", nature="温", flavours=("辛",),
          meridians=("肝", "胆", "心包"), actions=("活血行气", "祛风止痛"), aliases=("芎䓖",),
          source=f"{_BENJING}（中品，芎䓖）"),
    _herb("chenpi", "陈皮", "chen pi", "Citri Reticulatae Pericarpium",
          species=("Citrus reticulata",), part="成熟果皮", nature="温", flavours=("辛", "苦"),
          meridians=("脾", "肺"), actions=("理气健脾", "燥湿化痰"), aliases=("橘皮",),
          source=f"{_BENJING}（上品，橘柚）"),
    _herb("shengma", "升麻", "sheng ma", "Cimicifugae Rhizoma",
          species=("Cimicifuga heracleifolia", "Cimicifuga dahurica", "Cimicifuga foetida"),
          part="根茎", nature="微寒", flavours=("辛", "微甘"), meridians=("肺", "脾", "胃", "大肠"),
          actions=("发表透疹", "清热解毒", "升举阳气"), source=f"{_BENJING}（上品）"),
    _herb("chaihu", "柴胡", "chai hu", "Bupleuri Radix",
          species=("Bupleurum chinense", "Bupleurum scorzonerifolium"), part="根",
          nature="微寒", flavours=("辛", "苦"), meridians=("肝", "胆", "肺"),
          actions=("疏散退热", "疏肝解郁", "升举阳气"), source=f"{_BENJING}（上品）"),
    _herb("xingren", "苦杏仁", "ku xing ren", "Armeniacae Semen Amarum",
          species=("Prunus armeniaca",), part="成熟种子", nature="微温", flavours=("苦",),
          meridians=("肺", "大肠"), actions=("降气止咳平喘", "润肠通便"), aliases=("杏仁",),
          toxicity="有小毒", source=f"{_BENJING}（下品）"),
    _herb("banxia", "半夏", "ban xia", "Pinelliae Rhizoma",
          species=("Pinellia ternata",), part="块茎", nature="温", flavours=("辛",),
          meridians=("脾", "胃", "肺"), actions=("燥湿化痰", "降逆止呕", "消痞散结"),
          toxicity="有毒", source=f"{_BENJING}（下品）"),
    _herb("gansui", "甘遂", "gan sui", "Kansui Radix",
          species=("Euphorbia kansui",), part="块根", nature="寒", flavours=("苦",),
          meridians=("肺", "肾", "大肠"), actions=("泻水逐饮", "消肿散结"), toxicity="有毒",
          source=f"{_BENJING}（下品）"),
    _herb("lilu", "藜芦", "li lu", "Veratri Nigri Radix et Rhizoma",
          species=("Veratrum nigrum",), part="根及根茎", nature="寒", flavours=("辛", "苦"),
          meridians=("肝", "肺", "胃"), actions=("涌吐风痰", "杀虫"), toxicity="有毒",
          source=f"{_BENJING}（下品）"),
)

_PROCESSED = (
    ProcessedHerb(id="processed.zhi_gancao", herb_id="herb.gancao", method="蜜炙", chinese="炙甘草",
                  effect_change="补脾和胃、益气复脉之力增强；清热解毒之力减弱",
                  notes="桂枝汤、四君子汤、四逆汤等方所用"),
    ProcessedHerb(id="processed.zhi_fuzi", herb_id="herb.fuzi", method="胆巴浸、煮、蒸（黑顺片/白附片）",
                  chinese="制附子", toxicity_change="双酯型生物碱（乌头碱等）水解，毒性显著降低",
                  effect_change="回阳救逆之力保留", notes="入汤剂需先煎、久煎"),
    ProcessedHerb(id="processed.fa_banxia", herb_id="herb.banxia", method="甘草、石灰制",
                  chinese="法半夏", toxicity_change="刺激性显著降低", effect_change="燥湿化痰为主"),
    ProcessedHerb(id="processed.mi_mahuang", herb_id="herb.mahuang", method="蜜炙", chinese="蜜麻黄",
                  effect_change="发汗之力缓和，宣肺平喘之力增强"),
    ProcessedHerb(id="processed.chao_baizhu", herb_id="herb.baizhu", method="麸炒", chinese="炒白术",
                  effect_change="健脾之力增强，燥性缓和"),
)

_SYNDROMES = (
    Syndrome(id="syndrome.qixu", chinese="气虚证", pinyin="qi xu zheng", english="qi deficiency",
             category="气血津液辨证", manifestations=("神疲乏力", "少气懒言", "自汗", "动则加剧"),
             tongue="舌淡", pulse="脉虚", treatment_principle="补气"),
    Syndrome(id="syndrome.piwei_qixu", chinese="脾胃气虚证", pinyin="pi wei qi xu zheng",
             english="spleen-stomach qi deficiency", category="脏腑辨证",
             manifestations=("面色萎白", "语声低微", "气短乏力", "食少便溏"), tongue="舌淡苔白",
             pulse="脉虚弱", treatment_principle="益气健脾"),
    Syndrome(id="syndrome.pixu_qixian", chinese="脾虚气陷证", pinyin="pi xu qi xian zheng",
             english="spleen qi sinking", category="脏腑辨证",
             manifestations=("饮食减少", "体倦肢软", "少气懒言", "脱肛", "子宫脱垂", "久泻久痢"),
             tongue="舌淡苔薄白", pulse="脉虚", treatment_principle="补中益气，升阳举陷"),
    Syndrome(id="syndrome.yingxue_xuzhi", chinese="营血虚滞证", pinyin="ying xue xu zhi zheng",
             english="blood deficiency with stasis", category="气血津液辨证",
             manifestations=("头晕目眩", "心悸失眠", "面色无华", "月经不调", "量少或经闭"),
             tongue="舌淡", pulse="脉细弦或细涩", treatment_principle="补血调血"),
    Syndrome(id="syndrome.taiyang_zhongfeng", chinese="太阳中风证", pinyin="tai yang zhong feng zheng",
             english="taiyang wind strike (exterior deficiency)", category="六经辨证",
             manifestations=("发热", "汗出", "恶风", "头痛", "鼻鸣干呕"), tongue="舌苔薄白",
             pulse="脉浮缓", treatment_principle="解肌发表，调和营卫", aliases=("表虚证",)),
    Syndrome(id="syndrome.taiyang_shanghan", chinese="太阳伤寒证", pinyin="tai yang shang han zheng",
             english="taiyang cold damage (exterior excess)", category="六经辨证",
             manifestations=("恶寒发热", "头身疼痛", "无汗而喘"), tongue="舌苔薄白",
             pulse="脉浮紧", treatment_principle="发汗解表，宣肺平喘", aliases=("表实证",)),
    Syndrome(id="syndrome.shaoyin_yangxu", chinese="少阴阳虚寒厥证", pinyin="shao yin yang xu han jue zheng",
             english="shaoyin yang deficiency with cold reversal", category="六经辨证",
             manifestations=("四肢厥逆", "恶寒蜷卧", "神衰欲寐", "下利清谷"), tongue="舌淡苔白滑",
             pulse="脉微细", treatment_principle="回阳救逆"),
    Syndrome(id="syndrome.xinxue_yuzu", chinese="心血瘀阻证", pinyin="xin xue yu zu zheng",
             english="heart blood stasis", category="脏腑辨证",
             manifestations=("心悸怔忡", "心胸憋闷刺痛", "痛引肩背"), tongue="舌紫暗或有瘀斑",
             pulse="脉涩或结代", treatment_principle="活血化瘀，通络止痛"),
)


def _formula(id, chinese, pinyin, source, ingredients, *, actions=(), indications=(),
             contraindications=(), aliases=(), notes="") -> Formula:
    return Formula(id=f"formula.{id}", chinese=chinese, pinyin=pinyin, source=source,
                   ingredients=tuple(Ingredient(**i) for i in ingredients), actions=actions,
                   indications=indications, contraindications=contraindications,
                   aliases=aliases, notes=notes)


_FORMULAS = (
    _formula("guizhitang", "桂枝汤", "gui zhi tang", _SHANGHAN, [
        dict(herb_id="herb.guizhi", role="君", dose="三两"),
        dict(herb_id="herb.baishao", role="臣", dose="三两"),
        dict(herb_id="herb.shengjiang", role="佐", dose="三两"),
        dict(herb_id="herb.dazao", role="佐", dose="十二枚"),
        dict(herb_id="herb.gancao", role="使", dose="二两", processed="processed.zhi_gancao")],
        actions=("解肌发表", "调和营卫"), indications=("syndrome.taiyang_zhongfeng",),
        contraindications=("表实无汗者不宜", "温病初起者不宜"), aliases=("阳旦汤",),
        notes="服后啜热稀粥以助药力，温覆取微汗"),
    _formula("mahuangtang", "麻黄汤", "ma huang tang", _SHANGHAN, [
        dict(herb_id="herb.mahuang", role="君", dose="三两"),
        dict(herb_id="herb.guizhi", role="臣", dose="二两"),
        dict(herb_id="herb.xingren", role="佐", dose="七十个"),
        dict(herb_id="herb.gancao", role="使", dose="一两", processed="processed.zhi_gancao")],
        actions=("发汗解表", "宣肺平喘"), indications=("syndrome.taiyang_shanghan",),
        contraindications=("表虚自汗者禁用", "体虚外感者不宜", "血虚者不宜")),
    _formula("sinitang", "四逆汤", "si ni tang", _SHANGHAN, [
        dict(herb_id="herb.fuzi", role="君", dose="一枚（生用）", processed="processed.zhi_fuzi"),
        dict(herb_id="herb.ganjiang", role="臣", dose="一两半"),
        dict(herb_id="herb.gancao", role="使", dose="二两", processed="processed.zhi_gancao")],
        actions=("回阳救逆",), indications=("syndrome.shaoyin_yangxu",),
        contraindications=("真热假寒者禁用",), notes="原方附子生用；今多用制附子并先煎"),
    _formula("sijunzitang", "四君子汤", "si jun zi tang", _JUFANG, [
        dict(herb_id="herb.renshen", role="君"),
        dict(herb_id="herb.baizhu", role="臣"),
        dict(herb_id="herb.fuling", role="佐"),
        dict(herb_id="herb.gancao", role="使", processed="processed.zhi_gancao")],
        actions=("益气健脾",), indications=("syndrome.piwei_qixu",)),
    _formula("siwutang", "四物汤", "si wu tang", _JUFANG, [
        dict(herb_id="herb.shudihuang", role="君"),
        dict(herb_id="herb.danggui", role="臣"),
        dict(herb_id="herb.baishao", role="佐"),
        dict(herb_id="herb.chuanxiong", role="使")],
        actions=("补血调血",), indications=("syndrome.yingxue_xuzhi",),
        notes="首见于《仙授理伤续断秘方》，《太平惠民和剂局方》收录"),
    _formula("buzhongyiqitang", "补中益气汤", "bu zhong yi qi tang", "内外伤辨惑论", [
        dict(herb_id="herb.huangqi", role="君"),
        dict(herb_id="herb.renshen", role="臣"),
        dict(herb_id="herb.baizhu", role="臣"),
        dict(herb_id="herb.gancao", role="臣", processed="processed.zhi_gancao"),
        dict(herb_id="herb.danggui", role="佐"),
        dict(herb_id="herb.chenpi", role="佐"),
        dict(herb_id="herb.shengma", role="使"),
        dict(herb_id="herb.chaihu", role="使")],
        actions=("补中益气", "升阳举陷"),
        indications=("syndrome.pixu_qixian", "syndrome.piwei_qixu"),
        contraindications=("阴虚发热及内热炽盛者不宜",), notes="李东垣创制，甘温除热之代表方"),
)

_PASSAGES = (
    ClassicalPassage(id="passage.shl_12", source=_SHANGHAN, chapter="辨太阳病脉证并治上·第12条",
                     era="东汉", text="太阳中风，阳浮而阴弱，阳浮者热自发，阴弱者汗自出，啬啬恶寒，"
                                     "淅淅恶风，翕翕发热，鼻鸣干呕者，桂枝汤主之。",
                     mentions=("formula.guizhitang", "syndrome.taiyang_zhongfeng")),
    ClassicalPassage(id="passage.shl_35", source=_SHANGHAN, chapter="辨太阳病脉证并治中·第35条",
                     era="东汉", text="太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风无汗而喘者，麻黄汤主之。",
                     mentions=("formula.mahuangtang", "syndrome.taiyang_shanghan")),
    ClassicalPassage(id="passage.shl_323", source=_SHANGHAN, chapter="辨少阴病脉证并治·第323条",
                     era="东汉", text="少阴病，脉沉者，急温之，宜四逆汤。",
                     mentions=("formula.sinitang", "syndrome.shaoyin_yangxu")),
    ClassicalPassage(id="passage.bj_huangqi", source=_BENJING, chapter="上品·草部", era="汉",
                     text="黄耆，味甘微温。主痈疽，久败疮，排脓止痛，大风癞疾，五痔鼠瘘，补虚，小儿百病。",
                     mentions=("herb.huangqi",)),
    ClassicalPassage(id="passage.bj_renshen", source=_BENJING, chapter="上品·草部", era="汉",
                     text="人参，味甘微寒。主补五脏，安精神，定魂魄，止惊悸，除邪气，明目，开心益智。久服轻身延年。",
                     mentions=("herb.renshen",)),
    ClassicalPassage(id="passage.nwsb_buzhong", source="内外伤辨惑论", chapter="卷中·饮食劳倦论", era="金",
                     text="内伤脾胃，乃伤其气；外感风寒，乃伤其形。伤外为有余，有余者泻之；伤内为不足，不足者补之。",
                     mentions=("formula.buzhongyiqitang",)),
    ClassicalPassage(id="passage.jufang_sijunzi", source=_JUFANG, chapter="卷之三·治一切气", era="宋",
                     text="四君子汤，治荣卫气虚，脏腑怯弱，心腹胀满，全不思食，肠鸣泄泻，呕哕吐逆。",
                     mentions=("formula.sijunzitang", "syndrome.piwei_qixu")),
    ClassicalPassage(id="passage.jufang_siwu", source=_JUFANG, chapter="卷之九·治妇人诸疾", era="宋",
                     text="四物汤，调益荣卫，滋养气血。治冲任虚损，月水不调，脐腹疞痛，崩中漏下。",
                     mentions=("formula.siwutang", "syndrome.yingxue_xuzhi")),
    ClassicalPassage(id="passage.shibafan", source="儒门事亲", chapter="十八反歌", era="金",
                     text="本草明言十八反，半蒌贝蔹及攻乌，藻戟遂芫俱战草，诸参辛芍叛藜芦。",
                     translation="十八反：半夏、瓜蒌、贝母、白蔹、白及反乌头；海藻、大戟、甘遂、芫花反甘草；"
                                 "人参、丹参、玄参、沙参、苦参、细辛、芍药反藜芦。",
                     mentions=("herb.banxia", "herb.fuzi", "herb.gansui", "herb.gancao",
                               "herb.renshen", "herb.danshen", "herb.baishao", "herb.lilu")),
)

_PHARMACOPOEIA_STUDY = StudyEvidence(
    id="study.pharmacopoeia_2020", tier=EvidenceTier.EXPERT_EXPERIENCE, subject_id="herb.fuzi",
    design="pharmacopoeial monograph", citation=_PHARMACOPOEIA,
    notes="药典各药材项下的性味归经、功能主治、用法用量与注意")
_TEXTBOOK_STUDY = StudyEvidence(
    id="study.textbook_zhongyaoxue", tier=EvidenceTier.EXPERT_EXPERIENCE,
    subject_id="herb.danshen", design="textbook", citation=_TEXTBOOK,
    notes="教材对功效、应用与使用注意的归纳")
_STUDIES = (_PHARMACOPOEIA_STUDY, _TEXTBOOK_STUDY)


def _rel(id, subject, predicate, obj, tier, evidence, **kw) -> ActionRelation:
    return ActionRelation(id=f"relation.{id}", subject_id=subject, predicate=predicate,
                          object_id=obj, tier=tier, evidence_ids=evidence, **kw)


_RELATIONS = (
    _rel("guizhitang_taiyang", "formula.guizhitang", "indicated_for", "syndrome.taiyang_zhongfeng",
         EvidenceTier.CLASSICAL_TEXT, ("passage.shl_12",)),
    _rel("mahuangtang_taiyang", "formula.mahuangtang", "indicated_for", "syndrome.taiyang_shanghan",
         EvidenceTier.CLASSICAL_TEXT, ("passage.shl_35",)),
    _rel("sinitang_shaoyin", "formula.sinitang", "indicated_for", "syndrome.shaoyin_yangxu",
         EvidenceTier.CLASSICAL_TEXT, ("passage.shl_323",)),
    _rel("sijunzi_piwei", "formula.sijunzitang", "indicated_for", "syndrome.piwei_qixu",
         EvidenceTier.CLASSICAL_TEXT, ("passage.jufang_sijunzi",)),
    _rel("siwu_yingxue", "formula.siwutang", "indicated_for", "syndrome.yingxue_xuzhi",
         EvidenceTier.CLASSICAL_TEXT, ("passage.jufang_siwu",), population="妇人",
         notes="局方列于治妇人诸疾"),
    _rel("buzhong_qixian", "formula.buzhongyiqitang", "indicated_for", "syndrome.pixu_qixian",
         EvidenceTier.CLASSICAL_TEXT, ("passage.nwsb_buzhong",)),
    _rel("huangqi_qixu", "herb.huangqi", "treats", "syndrome.qixu",
         EvidenceTier.CLASSICAL_TEXT, ("passage.bj_huangqi",), notes="本经：补虚"),
    _rel("renshen_qixu", "herb.renshen", "treats", "syndrome.qixu",
         EvidenceTier.CLASSICAL_TEXT, ("passage.bj_renshen",), notes="本经：主补五脏"),
    _rel("danshen_xinxue", "herb.danshen", "treats", "syndrome.xinxue_yuzu",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.textbook_zhongyaoxue",), population="成人",
         notes="教材功效：活血祛瘀，通经止痛"),
    _rel("zhi_gancao_from", "processed.zhi_gancao", "processed_from", "herb.gancao",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.pharmacopoeia_2020",)),
    _rel("zhi_fuzi_from", "processed.zhi_fuzi", "processed_from", "herb.fuzi",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.pharmacopoeia_2020",)),
    _rel("fa_banxia_from", "processed.fa_banxia", "processed_from", "herb.banxia",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.pharmacopoeia_2020",)),
    _rel("mi_mahuang_from", "processed.mi_mahuang", "processed_from", "herb.mahuang",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.pharmacopoeia_2020",)),
    _rel("chao_baizhu_from", "processed.chao_baizhu", "processed_from", "herb.baizhu",
         EvidenceTier.EXPERT_EXPERIENCE, ("study.pharmacopoeia_2020",)),
)


def _safety(id, subject, kind, description, *, severity="moderate",
            tier=EvidenceTier.EXPERT_EXPERIENCE, evidence=("study.pharmacopoeia_2020",),
            counterpart="", management="", population="") -> SafetyRecord:
    return SafetyRecord(id=f"safety.{id}", subject_id=subject, kind=kind, description=description,
                        severity=severity, tier=tier, evidence_ids=evidence,
                        counterpart_id=counterpart, management=management, population=population)


_SAFETY = (
    _safety("fuzi_toxicity", "herb.fuzi", "toxicity",
            "含乌头碱类双酯型生物碱，生品有毒；过量或煎煮不足可致心律失常、口唇肢体麻木",
            severity="critical", management="炮制后入药；入汤剂先煎、久煎；孕妇禁用"),
    _safety("fuzi_pregnancy", "herb.fuzi", "contraindication", "孕妇禁用", severity="critical",
            population="孕妇"),
    _safety("banxia_toxicity", "herb.banxia", "toxicity", "生半夏有毒，对口腔、咽喉及消化道黏膜有强烈刺激性",
            severity="high", management="内服须炮制（法半夏、姜半夏、清半夏）"),
    _safety("gansui_toxicity", "herb.gansui", "toxicity", "有毒，泻下峻猛，可致剧烈腹痛腹泻",
            severity="high", management="醋制后用；孕妇禁用；虚弱者慎用"),
    _safety("lilu_toxicity", "herb.lilu", "toxicity", "有毒，涌吐峻烈", severity="high",
            management="体虚气弱者及孕妇禁用"),
    _safety("xingren_toxicity", "herb.xingren", "toxicity", "有小毒（苦杏仁苷），过量可致氰化物中毒",
            severity="moderate", management="用量不宜过大；婴儿慎用"),
    _safety("gancao_pseudoaldosteronism", "herb.gancao", "adverse_event",
            "长期大剂量服用可致假性醛固酮增多症：水肿、血压升高、低血钾",
            severity="moderate", management="不宜长期大量使用；高血压、水肿、低钾者慎用"),
    _safety("mahuang_caution", "herb.mahuang", "contraindication",
            "表虚自汗、阴虚盗汗及肾不纳气之虚喘者慎用；含麻黄碱，高血压、心脏病者慎用",
            severity="moderate", management="蜜炙以缓其发汗之力；控制用量"),
    _safety("mahuangtang_contra", "formula.mahuangtang", "contraindication",
            "表虚自汗者禁用；体虚外感者不宜", severity="moderate",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shl_35",)),
    # --- 十八反（儒门事亲）
    _safety("shibafan_gancao_gansui", "herb.gancao", "incompatibility",
            "十八反：藻戟遂芫俱战草——甘遂反甘草", severity="high",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shibafan",),
            counterpart="herb.gansui", management="不宜同用"),
    _safety("shibafan_wutou_banxia", "herb.fuzi", "incompatibility",
            "十八反：半蒌贝蔹及攻乌——半夏反乌头（川乌、草乌、附子）", severity="high",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shibafan",),
            counterpart="herb.banxia", management="不宜同用"),
    _safety("shibafan_renshen_lilu", "herb.renshen", "incompatibility",
            "十八反：诸参辛芍叛藜芦——人参反藜芦", severity="high",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shibafan",),
            counterpart="herb.lilu", management="不宜同用"),
    _safety("shibafan_danshen_lilu", "herb.danshen", "incompatibility",
            "十八反：诸参辛芍叛藜芦——丹参反藜芦", severity="high",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shibafan",),
            counterpart="herb.lilu", management="不宜同用"),
    _safety("shibafan_shaoyao_lilu", "herb.baishao", "incompatibility",
            "十八反：诸参辛芍叛藜芦——芍药反藜芦", severity="high",
            tier=EvidenceTier.CLASSICAL_TEXT, evidence=("passage.shibafan",),
            counterpart="herb.lilu", management="不宜同用"),
)


def seed() -> TCMKnowledgeBase:
    """The checked-in seed. Public-domain classical content; no invented studies."""
    return TCMKnowledgeBase(herbs=_HERBS, processed=_PROCESSED, formulas=_FORMULAS,
                            syndromes=_SYNDROMES, passages=_PASSAGES, studies=_STUDIES,
                            relations=_RELATIONS, safety=_SAFETY)


_DEFAULT: TCMKnowledgeBase | None = None


def default_knowledge() -> TCMKnowledgeBase:
    """The seed, built once. Tools read it; nothing writes to it."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = seed()
    return _DEFAULT

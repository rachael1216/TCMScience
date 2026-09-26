"""Query terms for retrieval, in English and in Chinese.

Capability resolution and memory retrieval rank by lexical overlap between a query and a
description, and both tokenised with ``[a-z][a-z0-9-]{2,}``. Chinese has no spaces and
no Latin letters, so 黄芪治疗心力衰竭的临床证据 produced **no terms at all**: the
registry fell back to a flat relevance and memory retrieval returned nothing, for a
harness whose users write half their objectives in Chinese. The 2026-09-18 review filed
it as F10.

Three things now happen to a run of CJK characters:

* it is segmented against a bilingual lexicon of the domain — herbs, formulas, syndromes,
  diseases, study designs, omics — and each match contributes its English terms, so a
  Chinese query reaches a manifest or a memory node described in English;
* the matched Chinese words are terms themselves, so Chinese meets Chinese;
* the characters not covered by the lexicon become bigrams, the standard fallback for
  unsegmented Chinese, so two texts about the same thing overlap even where the lexicon
  is silent.

The lexicon is deliberately modest and deliberately checked in: it is data the tests can
read, and a term it lacks degrades to bigrams rather than to nothing.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

__all__ = ["LEXICON", "STOP_WORDS", "CJK_STOP", "terms", "cjk_terms", "expand_query"]

_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")
_CJK_RUN = re.compile("[\\u4e00-\\u9fff]+")
#: Function characters that carry no signal and would otherwise become terms or halves of
#: bigrams: 的 (of), 了, 和/与/或/及 (and, or), 在 (at), 是 (is), 对 (towards), 等 (etc.) ...
#: A run is split on them, so 黄芪的临床证据 is two runs, not one with 的 inside.
CJK_STOP: frozenset[str] = frozenset("的了和与或及在是对中为等其之于把被将从由并而且但则即也又都各这那些个请")

#: Words that carry no signal in a capability or memory query.
STOP_WORDS: frozenset[str] = frozenset("""the and for with from that this into over under
able all any are was were will can could would should has have had its their there which
who what when how why not use using used get set run make new via per etc""".split())

#: Chinese term -> English terms. Every value is already in tokenised form.
LEXICON: Mapping[str, tuple[str, ...]] = {
    # --- herbs and natural products
    "黄芪": ("astragalus", "huangqi"), "人参": ("ginseng", "renshen"),
    "丹参": ("danshen", "salvia"), "当归": ("angelica", "danggui"),
    "甘草": ("licorice", "glycyrrhiza", "gancao"), "黄连": ("coptis", "berberine", "huanglian"),
    "黄芩": ("scutellaria", "huangqin"), "川芎": ("chuanxiong", "ligusticum"),
    "白术": ("atractylodes", "baizhu"), "茯苓": ("poria", "fuling"),
    "桂枝": ("cinnamon", "guizhi"), "麻黄": ("ephedra", "mahuang"),
    "柴胡": ("bupleurum", "chaihu"), "葛根": ("pueraria", "puerarin", "gegen"),
    "青蒿": ("artemisia", "artemisinin", "qinghao"), "青蒿素": ("artemisinin",),
    "银杏": ("ginkgo",), "三七": ("notoginseng", "sanqi"), "红花": ("safflower", "carthamus"),
    "枸杞": ("goji", "lycium"), "大黄": ("rhubarb", "rheum", "dahuang"),
    "黄柏": ("phellodendron", "huangbo"), "半夏": ("pinellia", "banxia"),
    "陈皮": ("citrus", "peel", "chenpi"), "白芍": ("paeonia", "peony", "baishao"),
    "生地": ("rehmannia", "shengdi"), "熟地": ("rehmannia", "shudi"),
    "山药": ("dioscorea", "yam", "shanyao"), "五味子": ("schisandra", "wuweizi"),
    "灵芝": ("ganoderma", "reishi", "lingzhi"), "冬虫夏草": ("cordyceps",),
    "姜黄": ("turmeric", "curcumin", "curcuma"), "姜黄素": ("curcumin",),
    "小檗碱": ("berberine",), "黄芪甲苷": ("astragaloside",), "人参皂苷": ("ginsenoside",),
    "丹参酮": ("tanshinone",), "槲皮素": ("quercetin",), "白藜芦醇": ("resveratrol",),
    "雷公藤": ("tripterygium", "triptolide"), "附子": ("aconite", "aconitum", "fuzi"),
    "麝香": ("musk",), "金银花": ("honeysuckle", "lonicera", "jinyinhua"),
    "连翘": ("forsythia", "lianqiao"), "板蓝根": ("isatis", "banlangen"),
    "天然产物": ("natural", "product"), "活性成分": ("active", "constituent"),
    "成分": ("constituent", "compound"), "化合物": ("compound",), "药材": ("crude", "herb"),
    "本草": ("materia", "medica"), "炮制": ("processing", "paozhi"),
    # --- formulas and dosage forms
    "方剂": ("formula", "prescription"), "复方": ("formula", "compound"),
    "经方": ("classical", "formula"), "中药": ("herbal", "medicine", "tcm"),
    "中医": ("tcm", "chinese", "medicine"), "中医药": ("tcm", "chinese", "medicine"),
    "草药": ("herbal", "herb"), "汤": ("decoction",), "汤剂": ("decoction",),
    "丸": ("pill",), "散": ("powder",), "颗粒": ("granule",), "注射液": ("injection",),
    "配伍": ("combination", "compatibility"), "君臣佐使": ("formula", "hierarchy"),
    "四物汤": ("siwu", "decoction"), "六味地黄丸": ("liuwei", "dihuang"),
    "补中益气汤": ("buzhong", "yiqi"), "小柴胡汤": ("xiaochaihu",), "桂枝汤": ("guizhi", "decoction"),
    "血府逐瘀汤": ("xuefu", "zhuyu"), "生脉散": ("shengmai",), "四君子汤": ("sijunzi",),
    # --- syndromes and TCM concepts
    "证": ("syndrome", "pattern"), "证候": ("syndrome", "pattern"), "证型": ("syndrome", "pattern"),
    "辨证": ("syndrome", "differentiation"), "辨证论治": ("syndrome", "differentiation", "treatment"),
    "气虚": ("qi", "deficiency"), "血虚": ("blood", "deficiency"), "血瘀": ("blood", "stasis"),
    "阴虚": ("yin", "deficiency"), "阳虚": ("yang", "deficiency"), "痰湿": ("phlegm", "dampness"),
    "湿热": ("damp", "heat"), "肝郁": ("liver", "stagnation"), "脾虚": ("spleen", "deficiency"),
    "肾虚": ("kidney", "deficiency"), "气滞": ("qi", "stagnation"), "寒": ("cold",), "热": ("heat",),
    "虚": ("deficiency",), "实": ("excess",), "经络": ("meridian",), "穴位": ("acupoint",),
    "针灸": ("acupuncture",), "针刺": ("acupuncture",), "艾灸": ("moxibustion",),
    "推拿": ("tuina", "massage"), "拔罐": ("cupping",), "舌象": ("tongue",), "脉象": ("pulse",),
    # --- diseases and clinical
    "心力衰竭": ("heart", "failure"), "心衰": ("heart", "failure"), "心肌梗死": ("myocardial", "infarction"),
    "心律失常": ("arrhythmia",), "房颤": ("atrial", "fibrillation"), "高血压": ("hypertension",),
    "高血脂": ("hyperlipidemia",), "动脉粥样硬化": ("atherosclerosis",), "糖尿病": ("diabetes",),
    "冠心病": ("coronary", "disease"), "脑卒中": ("stroke",), "中风": ("stroke",),
    "肿瘤": ("tumor", "cancer"), "癌": ("cancer",), "癌症": ("cancer",), "肺癌": ("lung", "cancer"),
    "肝癌": ("liver", "cancer", "hepatocellular"), "乳腺癌": ("breast", "cancer"),
    "胃癌": ("gastric", "cancer"), "结直肠癌": ("colorectal", "cancer"), "胰腺癌": ("pancreatic", "cancer"),
    "白血病": ("leukemia",), "肝炎": ("hepatitis",), "肝硬化": ("cirrhosis",), "脂肪肝": ("fatty", "liver"),
    "慢性肾病": ("chronic", "kidney", "disease"), "肾病": ("kidney", "disease", "nephropathy"),
    "哮喘": ("asthma",), "慢阻肺": ("copd",), "肺炎": ("pneumonia",), "抑郁": ("depression",),
    "焦虑": ("anxiety",), "失眠": ("insomnia",), "痴呆": ("dementia",), "阿尔茨海默": ("alzheimer",),
    "帕金森": ("parkinson",), "骨质疏松": ("osteoporosis",), "骨关节炎": ("osteoarthritis",),
    "类风湿": ("rheumatoid", "arthritis"), "痛风": ("gout",), "肥胖": ("obesity",),
    "炎症": ("inflammation",), "感染": ("infection",), "新冠": ("covid",), "流感": ("influenza",),
    "溃疡性结肠炎": ("ulcerative", "colitis"), "肠易激": ("irritable", "bowel"),
    "胃炎": ("gastritis",), "不孕": ("infertility",), "多囊卵巢": ("polycystic", "ovary"),
    "痛经": ("dysmenorrhea",), "更年期": ("menopause",), "湿疹": ("eczema",), "银屑病": ("psoriasis",),
    "疾病": ("disease",), "症状": ("symptom",), "患者": ("patient",), "人群": ("population",),
    "儿童": ("children", "pediatric"), "老年": ("elderly",), "孕妇": ("pregnant",),
    # --- study designs and evidence
    "临床": ("clinical",), "试验": ("trial",), "临床试验": ("clinical", "trial"),
    "随机对照试验": ("randomized", "controlled", "trial", "rct"), "随机": ("randomized",),
    "对照": ("controlled",), "安慰剂": ("placebo",), "双盲": ("double-blind",),
    "队列": ("cohort",), "队列研究": ("cohort", "study"), "病例对照": ("case", "control"),
    "横断面": ("cross-sectional",), "荟萃分析": ("meta-analysis",), "meta分析": ("meta-analysis",),
    "系统评价": ("systematic", "review"), "系统综述": ("systematic", "review"),
    "证据": ("evidence",), "证据等级": ("evidence", "level", "grade"), "疗效": ("efficacy",),
    "有效性": ("efficacy", "effectiveness"), "安全性": ("safety",), "不良反应": ("adverse", "reaction"),
    "不良事件": ("adverse", "event"), "毒性": ("toxicity",), "剂量": ("dose",), "疗程": ("course",),
    "预后": ("prognosis",), "生存": ("survival",), "生存分析": ("survival", "analysis"),
    "死亡率": ("mortality",), "复发": ("recurrence",), "诊断": ("diagnosis",),
    "治疗": ("treatment", "therapy"), "预防": ("prevention",), "康复": ("rehabilitation",),
    "指南": ("guideline",), "共识": ("consensus",), "文献": ("literature",), "检索": ("search", "retrieval"),
    "摘要": ("abstract",), "综述": ("review",), "统计": ("statistics",), "统计学": ("statistics",),
    "样本量": ("sample", "size"), "效应量": ("effect", "size"), "偏倚": ("bias",),
    "流行病学": ("epidemiology",), "真实世界": ("real-world",), "注册": ("registry",),
    "药物警戒": ("pharmacovigilance",),
    # --- pharmacology and omics
    "药理": ("pharmacology",), "药理学": ("pharmacology",), "药代动力学": ("pharmacokinetics",),
    "药效学": ("pharmacodynamics",), "机制": ("mechanism",), "靶点": ("target",),
    "通路": ("pathway",), "信号通路": ("signaling", "pathway"), "网络药理学": ("network", "pharmacology"),
    "分子对接": ("docking",), "分子动力学": ("molecular", "dynamics"), "生物信息学": ("bioinformatics",),
    "基因": ("gene",), "基因组": ("genomics", "genome"), "基因组学": ("genomics",),
    "转录组": ("transcriptomics",), "转录组学": ("transcriptomics",), "蛋白": ("protein",),
    "蛋白质": ("protein",), "蛋白组": ("proteomics",), "蛋白质组学": ("proteomics",),
    "代谢": ("metabolism", "metabolic"), "代谢组": ("metabolomics",), "代谢组学": ("metabolomics",),
    "单细胞": ("single-cell",), "测序": ("sequencing",), "表达": ("expression",),
    "差异表达": ("differential", "expression"), "突变": ("mutation", "variant"), "变异": ("variant",),
    "序列": ("sequence",), "比对": ("alignment",), "结构": ("structure",), "晶体结构": ("crystal", "structure"),
    "进化": ("phylogeny", "evolution"), "系统发育": ("phylogeny",), "群体遗传": ("population", "genetics"),
    "微生物组": ("microbiome",), "肠道菌群": ("gut", "microbiota"), "免疫": ("immune", "immunity"),
    "炎症因子": ("cytokine",), "氧化应激": ("oxidative", "stress"), "细胞凋亡": ("apoptosis",),
    "细胞": ("cell",), "动物模型": ("animal", "model"), "小鼠": ("mouse",), "大鼠": ("rat",),
    "体外": ("vitro",), "体内": ("vivo",), "药物": ("drug",), "药": ("drug",),
    "相互作用": ("interaction",), "药物相互作用": ("drug", "interaction"), "配体": ("ligand",),
    "受体": ("receptor",), "酶": ("enzyme",), "抑制剂": ("inhibitor",), "数据集": ("dataset",),
    "数据库": ("database",), "质量控制": ("quality", "control"), "指纹图谱": ("fingerprint",),
    "含量测定": ("assay", "quantification"), "色谱": ("chromatography",), "质谱": ("mass", "spectrometry"),
}

_MAX_KEY = max(len(k) for k in LEXICON)
#: Single characters that are lexicon keys on their own are only used when nothing
#: longer matches at that position; they are too short to be reliable on their own.
_SINGLE = frozenset(k for k in LEXICON if len(k) == 1)


def _segment(run: str) -> list[tuple[str, bool]]:
    """Greedy longest-match segmentation: ``[(piece, in_lexicon)]``."""
    out: list[tuple[str, bool]] = []
    i = 0
    pending = ""
    while i < len(run):
        matched = ""
        for width in range(min(_MAX_KEY, len(run) - i), 0, -1):
            piece = run[i:i + width]
            if piece in LEXICON and (width > 1 or piece in _SINGLE):
                matched = piece
                break
        if matched:
            if pending:
                out.append((pending, False))
                pending = ""
            out.append((matched, True))
            i += len(matched)
        else:
            pending += run[i]
            i += 1
    if pending:
        out.append((pending, False))
    return out


def _runs(text: str) -> list[str]:
    """CJK runs of ``text``, split on the function characters in ``CJK_STOP``."""
    out: list[str] = []
    for run in _CJK_RUN.findall(text):
        current = ""
        for char in run:
            if char in CJK_STOP:
                if current:
                    out.append(current)
                current = ""
            else:
                current += char
        if current:
            out.append(current)
    return out


def cjk_terms(text: str) -> set[str]:
    """Terms for the CJK runs of ``text``: lexicon words, their English, and bigrams."""
    out: set[str] = set()
    for run in _runs(text):
        for piece, known in _segment(run):
            if known:
                out.add(piece)
                out.update(LEXICON[piece])
            elif len(piece) <= 2:
                out.add(piece)
            else:
                out.update(piece[i:i + 2] for i in range(len(piece) - 1))
        # Bigrams over the whole run as well, so two texts sharing a phrase the lexicon
        # splits differently still overlap.
        if len(run) >= 2:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def terms(text: str, *, stop: Iterable[str] = STOP_WORDS) -> set[str]:
    """Every retrieval term in ``text``, Latin and CJK, minus ``stop``."""
    lowered = (text or "").lower()
    out = set(_WORD.findall(lowered))
    out |= cjk_terms(lowered)
    stopset = frozenset(stop)
    return {t for t in out if t not in stopset}


def expand_query(text: str) -> str:
    """``text`` followed by the English of every lexicon word it contains.

    For consumers that match on substrings rather than term sets — a manifest's declared
    intents, for instance — so 中药 in a query still meets an intent named ``herbal``.
    """
    extra: list[str] = []
    for run in _runs(text or ""):
        for piece, known in _segment(run):
            if known:
                extra.extend(w for w in LEXICON[piece] if w not in extra)
    return text if not extra else f"{text} {' '.join(extra)}"

# TCM evidence retrieval · 中医药证据检索

`retrieve-tcm-evidence` · implementation: `bioagent.skills.p0:retrieve_tcm_evidence`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

<!-- zh -->
> 本文件是说明文档。它**不**作为权威依据被解析，也从不参与编译——契约是 `skill.yaml`（见 `docs/adr/0003`）。它被附在每个科研产物（research artifact, RA）上，让读者看到该 Skill 声称做了什么。

Retrieves study records and classical passages about a subject and reports
them with the limits each one carries.

<!-- zh -->
检索关于某一主题的研究记录与经典条文（classical passage），并在报告它们时，一并给出每一条自己所带的局限。

## What it does not do · 它不做什么

It does not answer the question. It returns evidence with its scope attached —
design, population, outcome, retraction state — and leaves the inference to a
consumer. A retrieval step that summarised would be making claims nobody could
attribute to a source.

<!-- zh -->
它不回答问题。它返回证据条目（evidence item, EI），并附上每条证据的适用范围——研究设计（study design）、人群、结局、撤稿状态——把推断留给下游使用方。一个会做概括总结的检索步骤，等于在做出无人能归因到某个来源的主张。

## Two distinctions it refuses to collapse · 它拒绝抹平的两组区分

- **An empty result is not a null finding.** A search that found nothing is
  reported as an empty result set with the search recorded, which is a much
  weaker statement than "there is no effect".
- **An unchecked retraction is not a clean record.** The three-state model
  (`not_retracted` / `retracted` / `unverified`) exists so "nobody checked" is
  representable and visible.

<!-- zh -->
- **空结果不等于阴性发现。** 一次什么都没搜到的检索，会被报告为一个空结果集，同时把这次检索本身记录下来——这比「没有效果」是一个弱得多的陈述。
- **未核查的撤稿状态不等于干净的记录。** 三态模型（`not_retracted` / `retracted` / `unverified`）之所以存在，就是为了让「没有人核查过」可以被表示、并且看得见。

## Inputs · 输入

```json
{"subject": "附子", "claim_kind": "efficacy", "max_results": 20}
```

## Outputs · 输出

`evidence.json` with `evidence`, `conflicts`, and `search` (including whether the
result set was truncated — a truncated set that does not say so is
indistinguishable from a complete one).

<!-- zh -->
`evidence.json`，包含 `evidence`、`conflicts` 与 `search`（其中包括结果集是否被截断——一个被截断却不说明自己被截断的结果集，与一个完整的结果集无法区分）。

## Limits · 局限

Only the seed corpus is searched. No live literature index (Europe PMC, PubMed)
or trial registry (ClinicalTrials.gov, ChiCTR) is queried, so this is **not a
systematic search**. Risk of bias, precision and consistency are left
**NOT ASSESSED** — the corpus carries nothing on which to judge them, and
defaulting them to "low" would be an invented finding.

<!-- zh -->
只检索种子语料库（seed corpus）。不查询任何在线文献索引（Europe PMC、PubMed）或临床试验注册库（ClinicalTrials.gov、ChiCTR），因此这**不是一次系统检索**。偏倚风险（risk of bias, RoB）、精确度与一致性一律留作**未评估（NOT ASSESSED）**——语料库不携带任何可用于判断它们的材料，而把它们默认为「低」将是一个凭空编造出来的发现。

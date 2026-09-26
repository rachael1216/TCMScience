# TCM safety assessment · 中医药安全性评估

`assess-tcm-safety` · implementation: `bioagent.skills.p0:assess_tcm_safety`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

<!-- zh -->
> 本文件是说明文档。它**不**作为权威依据被解析，也从不参与编译——契约是 `skill.yaml`（见 `docs/adr/0003`）。它被附在每个科研产物（research artifact, RA）上，让读者看到该 Skill 声称做了什么。

Reports recorded safety information for a herb or a combination, including
十八反 incompatibilities.

<!-- zh -->
报告某味药材或某个配伍已记录的安全性信息，包括十八反（the eighteen incompatibilities）配伍禁忌。

## Unknown is not safe · 未知不等于安全

The status is `risk_recorded`, `no_record` or `unknown`. **Absence of a record is
not evidence of safety**, and the artifact says so in those words. A tool that
answers "no known contraindication" when it means "I found no record" is
dangerous precisely because it looks helpful.

<!-- zh -->
状态取值为 `risk_recorded`、`no_record` 或 `unknown`。**没有记录，不构成安全的证据**，而科研产物正是用这句话原样写明的。一个在真实含义是「我没有找到记录」时却回答「无已知禁忌」的工具是危险的，恰恰因为它看起来很有用。

## The incompatibility check is a set operation · 配伍禁忌检查是一次集合运算

十八反 is a property of a *pair*, so `check_compatibility` is called once with the
whole herb list. A per-herb loop would miss every one of them.

<!-- zh -->
十八反是*一对*药物之间的属性，因此 `check_compatibility` 用整份药材清单调用一次。若按每味药材循环一次，会漏掉其中每一条。

## Severity is never downgraded · 严重程度从不被下调

There is no code path that softens a finding. A record at `critical` stays
`critical`.

<!-- zh -->
不存在任何会弱化一项发现的代码路径。标记为 `critical` 的记录就保持 `critical`。

## Inputs · 输入

```json
{"subject": "甘草", "co_administered": ["甘遂"], "population": ""}
```

## Outputs · 输出

`safety.json` with `status`, `records`, `critical_records`,
`combination_conflicts`, `processing` and `unresolved`.

<!-- zh -->
`safety.json`，包含 `status`、`records`、`critical_records`、`combination_conflicts`、`processing` 与 `unresolved`。

## What it does not claim · 它不声称什么

Its manifest permits `attribution` only. A 十八反 contraindication is real
traditional knowledge, and it is **not** a `safety_signal` — that claim kind's
floor is `case_report`, and the corpus's records are pharmacopoeia entries. The
distinction between "the classics record these two as incompatible" and "there is
clinical evidence of harm" is preserved rather than blurred.

<!-- zh -->
它的清单（manifest）只允许 `attribution`。十八反禁忌是真实的传统知识，但它**不是**一个 `safety_signal`——那一类主张的下限是 `case_report`，而语料库中的记录是药典条目。「经典记载这两味药相反」与「有临床证据表明会造成伤害」之间的区别，是被保留下来，而不是被模糊掉。

## Limits · 局限

The corpus holds 14 safety records and is illustrative; it is not a
pharmacovigilance database, and none (openFDA, DailyMed, TCMToxDB) was queried.
No herb–drug interaction check against conventional medicines was performed. No
dose–response or organ-toxicity assessment was performed.

<!-- zh -->
语料库收录 14 条安全性记录，仅作示例；它不是药物警戒数据库，也没有查询任何此类数据库（openFDA、DailyMed、TCMToxDB）。未执行针对化学药的药材–药物相互作用检查。未执行剂量–反应或器官毒性评估。

**This is not clinical advice and must not be used to decide whether a
prescription is safe for a patient.**

<!-- zh -->
**这不是临床建议，不得用于判断某个处方对某位患者是否安全。**

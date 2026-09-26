# TCM entity normalization · 中医药术语规范化

`normalize-tcm-entities` · implementation: `bioagent.skills.p0:normalize_tcm_entities`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

<!-- zh -->
> 本文件是说明文档。它**不**作为权威依据被解析，也从不参与编译——契约是 `skill.yaml`（见 `docs/adr/0003`）。它被附在每个科研产物（research artifact, RA）上，让读者看到该 Skill 声称做了什么。

Resolves herb, processed-herb, formula and syndrome names to entries in the
shipped TCM seed corpus.

<!-- zh -->
把药材（herb）、炮制品（processed herb）、方剂（formula）与证候（syndrome pattern）的名称，解析为随包发布的中医药种子语料库（seed corpus）中的条目。

## What it returns · 返回什么

For each input name, either a resolved entity or the **set of candidates** it
could mean. It never picks between candidates.

<!-- zh -->
对每一个输入名称，返回一个已解析的实体，或者返回它可能指代的**候选集合**。它从不在候选之间做选择。

## Why ambiguity is preserved · 为什么保留歧义

「姜」 is 生姜 (*Zingiberis Rhizoma Recens*, 微温, releases the exterior) or 干姜
(*Zingiberis Rhizoma*, 热, warms the interior and restores yang). Different drugs
from the same plant, different indications. 「甘草」 has processed forms
(炙甘草) that are likewise a different preparation. A resolver that chose one
would be wrong roughly half the time and confident every time, and nothing
downstream could tell a choice had been made.

<!-- zh -->
「姜」是生姜（*Zingiberis Rhizoma Recens*，微温，解表），还是干姜（*Zingiberis Rhizoma*，热，温里回阳）？同一植株来源的不同药物，主治也不同。「甘草」有炮制形态（炙甘草），那同样是一种不同的炮制品。一个替你在候选之间做出选择的解析器，大约有一半的时候是错的，却每一次都同样笃定——而下游没有任何环节能知道它曾经做过一次选择。

## Inputs · 输入

```json
{"names": ["白芍", "姜", "附子"]}
```

## Outputs · 输出

`entities.json`:
```json
{"queries": [{"query": "姜", "status": "ambiguous", "entity_id": null,
              "candidates": ["herb.shengjiang", "herb.ganjiang"]}],
 "corpus": {"snapshot_hash": "...", "snapshot_at": "..."}}
```

## Limits · 局限

Resolution is against the seed corpus only. No external identifier (InChIKey,
UniProt, HGNC) is assigned, because the corpus carries none — that requires a
connector run, which this skill does not perform.

<!-- zh -->
解析只对着种子语料库进行。不分配任何外部标识符（InChIKey、UniProt、HGNC），因为语料库本身不携带这些标识符——那需要一次连接器（connector）运行，而本 Skill 不执行该运行。

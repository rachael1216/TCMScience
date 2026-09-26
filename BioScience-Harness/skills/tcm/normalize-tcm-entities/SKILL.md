# TCM entity normalization

`normalize-tcm-entities` · implementation: `bioagent.skills.p0:normalize_tcm_entities`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

Resolves herb, processed-herb, formula and syndrome names to entries in the
shipped TCM seed corpus.

## What it returns

For each input name, either a resolved entity or the **set of candidates** it
could mean. It never picks between candidates.

## Why ambiguity is preserved

「姜」 is 生姜 (*Zingiberis Rhizoma Recens*, 微温, releases the exterior) or 干姜
(*Zingiberis Rhizoma*, 热, warms the interior and restores yang). Different drugs
from the same plant, different indications. 「甘草」 has processed forms
(炙甘草) that are likewise a different preparation. A resolver that chose one
would be wrong roughly half the time and confident every time, and nothing
downstream could tell a choice had been made.

## Inputs

```json
{"names": ["白芍", "姜", "附子"]}
```

## Outputs

`entities.json`:
```json
{"queries": [{"query": "姜", "status": "ambiguous", "entity_id": null,
              "candidates": ["herb.shengjiang", "herb.ganjiang"]}],
 "corpus": {"snapshot_hash": "...", "snapshot_at": "..."}}
```

## Limits

Resolution is against the seed corpus only. No external identifier (InChIKey,
UniProt, HGNC) is assigned, because the corpus carries none — that requires a
connector run, which this skill does not perform.


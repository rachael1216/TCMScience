# TCM network pharmacology

`analyze-tcm-network-pharmacology` · implementation: `bioagent.skills.p0:analyze_tcm_network_pharmacology`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

Builds a herb–target network for a formula, keeping predicted and measured
edges strictly apart.

## The separation, which is the point

`predicted_targets` and `measured_targets` are **different keys**. There is no
combined `targets` list, so a consumer cannot accidentally read a prediction as a
measurement. Every predicted edge also carries a predictive evidence design
(`network_prediction`) and is marked `directness: EXTRAPOLATED`.

## Why it cannot make a clinical claim

The manifest permits `mechanism` and forbids `efficacy`, `association`,
`safety_signal`, `recommendation`, `attribution` and `traditional_use`. The
kernel enforces the same thing independently: in its claim-support table,
predictive designs appear in exactly **one** row, MECHANISTIC. A clinical claim
from this skill is therefore refused twice — by its own manifest and by the
kernel's type system. Neither refusal depends on the model behaving.

## Inputs

```json
{"formula_name": "桂枝汤"}
```

## Outputs

`network.json` with `ingredients`, `composition_edges`, `predicted_targets`,
`measured_targets`, `pathway_enrichment`, `provenance_summary` and
`randomisation`.

## Limits

Every target edge is a corpus-recorded relation or a network inference; **none is
a binding measurement**. No docking, assay or structure-based prediction was run.
Enrichment is a ranked list of recorded predicates, not a background-corrected
analysis — no network randomisation was performed, so no p-value is reported. The
network is built from the seed corpus only; no external target database
(BindingDB, ChEMBL) was queried.

A predicted edge is a hypothesis about a mechanism, not evidence that the formula
treats any condition in any patient.


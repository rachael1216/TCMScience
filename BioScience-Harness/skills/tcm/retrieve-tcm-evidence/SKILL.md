# TCM evidence retrieval

`retrieve-tcm-evidence` · implementation: `bioagent.skills.p0:retrieve_tcm_evidence`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

Retrieves study records and classical passages about a subject and reports
them with the limits each one carries.

## What it does not do

It does not answer the question. It returns evidence with its scope attached —
design, population, outcome, retraction state — and leaves the inference to a
consumer. A retrieval step that summarised would be making claims nobody could
attribute to a source.

## Two distinctions it refuses to collapse

- **An empty result is not a null finding.** A search that found nothing is
  reported as an empty result set with the search recorded, which is a much
  weaker statement than "there is no effect".
- **An unchecked retraction is not a clean record.** The three-state model
  (`not_retracted` / `retracted` / `unverified`) exists so "nobody checked" is
  representable and visible.

## Inputs

```json
{"subject": "附子", "claim_kind": "efficacy", "max_results": 20}
```

## Outputs

`evidence.json` with `evidence`, `conflicts`, and `search` (including whether the
result set was truncated — a truncated set that does not say so is
indistinguishable from a complete one).

## Limits

Only the seed corpus is searched. No live literature index (Europe PMC, PubMed)
or trial registry (ClinicalTrials.gov, ChiCTR) is queried, so this is **not a
systematic search**. Risk of bias, precision and consistency are left
**NOT ASSESSED** — the corpus carries nothing on which to judge them, and
defaulting them to "low" would be an invented finding.


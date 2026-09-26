# TCM safety assessment

`assess-tcm-safety` · implementation: `bioagent.skills.p0:assess_tcm_safety`

> This file is documentation. It is **not** parsed for authority and never
> compiled — `skill.yaml` is the contract (see `docs/adr/0003`). It is attached
> to each artifact so a reader can see what the skill claims to do.

Reports recorded safety information for a herb or a combination, including
十八反 incompatibilities.

## Unknown is not safe

The status is `risk_recorded`, `no_record` or `unknown`. **Absence of a record is
not evidence of safety**, and the artifact says so in those words. A tool that
answers "no known contraindication" when it means "I found no record" is
dangerous precisely because it looks helpful.

## The incompatibility check is a set operation

十八反 is a property of a *pair*, so `check_compatibility` is called once with the
whole herb list. A per-herb loop would miss every one of them.

## Severity is never downgraded

There is no code path that softens a finding. A record at `critical` stays
`critical`.

## Inputs

```json
{"subject": "甘草", "co_administered": ["甘遂"], "population": ""}
```

## Outputs

`safety.json` with `status`, `records`, `critical_records`,
`combination_conflicts`, `processing` and `unresolved`.

## What it does not claim

Its manifest permits `attribution` only. A 十八反 contraindication is real
traditional knowledge, and it is **not** a `safety_signal` — that claim kind's
floor is `case_report`, and the corpus's records are pharmacopoeia entries. The
distinction between "the classics record these two as incompatible" and "there is
clinical evidence of harm" is preserved rather than blurred.

## Limits

The corpus holds 14 safety records and is illustrative; it is not a
pharmacovigilance database, and none (openFDA, DailyMed, TCMToxDB) was queried.
No herb–drug interaction check against conventional medicines was performed. No
dose–response or organ-toxicity assessment was performed.

**This is not clinical advice and must not be used to decide whether a
prescription is safe for a patient.**


# ADR-0002 — Runtime, Skill, Source and Benchmark version independently

- **Status:** Accepted
- **Date:** 2026-09-25
- **Related:** [ADR-0001](0001-three-registry-separation.md)

## Context

A published TCMScience result is a joint statement about four things: the
kernel that executed the run, the skills it resolved, the external data sources
it read, and the benchmark it was scored against. Today these are only loosely
versioned — `psh` and `bioagent` carry `__version__`, skill manifests carry
`version`/`api_version`, and connectors carry free-text licence strings with no
snapshot identity at all.

External sources are the sharp edge. HERB, ETCM, BindingDB and openFDA change
without telling us. A network-pharmacology result produced in March and
reproduced in September is not the same experiment if the underlying database
moved, even though every line of our code is identical.

A single project-wide version number would create the opposite problem: it
would force a new release for every skill addition, and would hide which axis
actually changed when two results disagree.

## Decision

Version the four axes separately, and make every citable result name all four.

| Axis | Where it lives | Changes when |
| --- | --- | --- |
| **Runtime** | `psh.__version__`, `bioagent.__version__` | code changes |
| **Skill** | `registry/skills.lock.yaml` revision | a promotion is approved |
| **Source** | `registry/sources.lock.yaml` revision | a connector's snapshot moves |
| **Benchmark** | `benchmarks/registry/<season>.yaml` | a Season is cut |

A **run record** carries a `composite_version` naming all four. That string,
not any individual version, is what a leaderboard row and a paper table cite.

### Source snapshots are pinned by content, not by date

A `SourceCard` records a `snapshot_hash` over the normalised response of every
declared operation on a declared date. "HERB 2.0" is not a version; the hash is.
Connector results carry the card's hash so a downstream artifact can state
exactly which bytes produced it.

## Consequences

**Positive**

- A result can be reproduced or refuted by anyone holding the four identifiers.
- Drift in an external database surfaces as a *changed source hash*, not as an
  unexplained change in output.
- Adding a skill does not force a Runtime release.

**Negative / accepted costs**

- More bookkeeping: every artifact must thread four versions instead of one.
- Snapshot hashing costs a fetch per declared operation per refresh. This is
  amortised — snapshots refresh on the Source axis's own cadence, not per run.

## Compliance

- `bioagent.contracts.artifact.ResearchArtifact` requires `composite_version`.
- `bioagent.contracts.source_card.SourceCard` requires `snapshot_hash` and
  `snapshot_at` before a connector may be used in a *publishable* artifact.
- The Arena leaderboard refuses to render a row whose four versions are not all
  resolvable in the published registries.

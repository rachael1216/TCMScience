# ADR-0001 — Separate Candidate, Stable and Benchmark-Season registries

- **Status:** Accepted
- **Date:** 2026-09-25
- **Supersedes:** —
- **Related:** [ADR-0002](0002-independent-version-axes.md), [ADR-0003](0003-skill-yaml-compilation-contract.md)

## Context

TCMScience intends to absorb new skills from upstream projects (K-Dense
scientific-agent-skills, ClawBio, PantheonOS, Biomni, BioMedArena, tcm-cli and
newly published `SKILL.md` files) on a monthly cadence. The obvious
implementation — a scheduled job that installs whatever looks popular — would
destroy the two properties the project exists to provide:

1. **Reproducibility.** A benchmark score quoted in a paper must be
   re-derivable. If the skill set can change between two runs of the same
   benchmark version, it is not.
2. **Licence and safety governance.** An upstream skill executed inside the
   trusted runtime is code executing with the operator's authority. Star count
   is not evidence of fitness for that.

A single mutable `skills/` directory conflates three distinct objects with
three distinct lifecycles: what we have *found*, what we have *approved*, and
what we *measure against*.

## Decision

Maintain three separately versioned registries with a one-way promotion path:

| Registry | Updated | Automatic | Role |
| --- | --- | --- | --- |
| **Candidate Skill Catalog** | monthly | yes | everything discovered, never executed in production |
| **Stable Skill Registry** | on human approval | no | the only source the runtime resolves skills from |
| **Frozen Benchmark Registry** | quarterly / half-yearly | no | the fixed yardstick a Season's scores are comparable under |

The monthly pipeline may write only to the Candidate Catalog. Promotion into
the Stable Registry requires an explicit human decision recorded as a
`PromotionDecision`; a benchmark Season is cut from a Stable Registry revision
and is thereafter immutable.

**A monthly update must never modify the Benchmark Registry.** Otherwise the
current month's score and last month's are not on the same scale and the
leaderboard silently becomes meaningless. Seasons are the unit of comparability.

## Consequences

**Positive**

- A published score names the exact Stable Registry revision and Benchmark
  Season it was produced under, so it is re-runnable.
- Upstream churn is absorbed at the Candidate layer and cannot reach production
  without a recorded approval.
- Licence and security audits run once, at promotion, rather than on every
  execution.

**Negative / accepted costs**

- New upstream skills are available later than they would be under
  auto-install. This is the point.
- Three registries must be kept consistent; `sources.lock.yaml` records
  provenance so drift is detectable.

## Compliance

- `bioagent.updates.promotion` is the *only* writer of a Stable Registry
  revision, and it requires a `PromotionDecision` argument.
- `registry/skills.lock.yaml` changes only in a commit that also contains a
  promotion record.
- The Arena reads a Stable Registry revision and a Season; it never writes.

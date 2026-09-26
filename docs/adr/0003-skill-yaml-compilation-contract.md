# ADR-0003 — `skill.yaml` is the compilation contract; `SKILL.md` is the human document

- **Status:** Accepted
- **Date:** 2026-09-25
- **Related:** [ADR-0001](0001-three-registry-separation.md), [ADR-0004](0004-arena-read-only-static-first.md)

## Context

Upstream ecosystems ship skills as a directory containing `SKILL.md` — a
Markdown file with YAML frontmatter. The existing provider
(`bioagent/providers/skills.py`) parses that frontmatter with a deliberately
minimal flat-key reader and declares every such skill
`RuntimeSpec(backend="none")`: *"a skill is a specification for a host agent,
not a callable."*

That is an honest description of the upstream format, and it is the correct
default — but it cannot carry what TCMScience needs. Frontmatter has no place
to state an evidence policy, a permission set, or an output schema. And a skill
declared `backend="none"` cannot be compiled into a PSH plan, so it can never be
benchmarked, audited, or held to its own claims.

The tempting shortcut is to infer those properties from prose in the Markdown.
That would make the security argument depend on a language model reading a
README, which is exactly the failure mode the kernel exists to prevent.

## Decision

Two files, two audiences, one direction of authority.

- **`SKILL.md`** — human-facing. Purpose, usage, examples, citations. Prose is
  never parsed for authority. It may be absent.
- **`skill.yaml`** — machine-facing. A **declarative, non-Turing-complete**
  document validated against a JSON Schema. It is the only source of the
  skill's declared inputs, outputs, permissions and evidence policy.

For an upstream skill that ships only `SKILL.md`, the Candidate layer holds an
**adapter** `skill.yaml` written by TCMScience. The adapter is what gets
audited and pinned. Wrapping third-party prose does not make it trusted, so:

> **Rule.** A skill is compiled into a PSH plan only from `skill.yaml`.
> `SKILL.md` is attached to the artifact as documentation and is never an input
> to compilation, permission derivation or scoring.

### Permission derivation is by intersection, never by union

A skill's effective authority is the **intersection** of what `skill.yaml`
declares, what the enclosing `RunEnvelope` permits, and what
`AuthorityLattice` allows. A skill cannot widen authority, and a manifest that
declares a permission the envelope lacks fails compilation with
`SKILL_PERMISSION_WIDENS_ENVELOPE` rather than being silently trimmed.

This mirrors `bridge_manifest`, which already derives a PSH manifest's
`max_label` as the *minimum* ceiling across destinations rather than the
maximum — the same "narrow, never widen" direction.

### A skill must state what it may not claim

`skill.yaml` carries an `evidence` block: the highest `EvidenceTier` it may
cite, the claim kinds it may assert (`ClaimType`), and an explicit
`forbidden_claims` list. The compiler rejects a `ResearchArtifact` whose claims
exceed the declaring skill's policy, so a network-pharmacology skill
structurally cannot emit a clinical efficacy claim.

## Consequences

**Positive**

- The security argument reduces to reading one declarative file per skill.
- Upstream `SKILL.md` skills remain usable — via a reviewed adapter — without
  pretending their prose is a contract.
- Permission and evidence violations are compilation errors with stable codes,
  not runtime surprises.

**Negative / accepted costs**

- Maintaining adapters for upstream skills is manual work at the Candidate
  layer. This is bounded by the monthly cadence and is the price of not
  auto-installing unreviewed code.
- Some upstream skills cannot be expressed without loss; those are rejected
  rather than approximated.

## Compliance

- `bioagent/skills/schema/skill.schema.json` is the normative schema.
- `bioagent.skills.loader` refuses to emit a `SkillSpec` from Markdown alone.
- `bioagent.skills.compiler` is the only path from `SkillSpec` to a PSH plan,
  and it raises the codes above.
- `tests/test_skill_compiler.py` asserts that a skill declaring
  `PUBLIC_REMOTE` under a `LOCAL_COMPUTE`-only envelope is refused.

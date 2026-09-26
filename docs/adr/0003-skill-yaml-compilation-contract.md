# ADR-0003 — `skill.yaml` is the compilation contract; `SKILL.md` is the human document · ADR-0003 —— `skill.yaml` 是编译契约，`SKILL.md` 是人类文档

- **Status:** Accepted
- **Date:** 2026-09-25
- **Related:** [ADR-0001](0001-three-registry-separation.md), [ADR-0004](0004-arena-read-only-static-first.md)

<!-- zh -->
- **状态：** 已接受
- **日期：** 2026-09-25
- **相关：** [ADR-0001](0001-three-registry-separation.md)、[ADR-0004](0004-arena-read-only-static-first.md)

## Context · 背景

Upstream ecosystems ship skills as a directory containing `SKILL.md` — a
Markdown file with YAML frontmatter. The existing provider
(`bioagent/providers/skills.py`) parses that frontmatter with a deliberately
minimal flat-key reader and declares every such skill
`RuntimeSpec(backend="none")`: *"a skill is a specification for a host agent,
not a callable."*

<!-- zh -->
上游生态以「一个包含 `SKILL.md` 的目录」的形式分发技能——该文件是一个带 YAML frontmatter 的 Markdown 文件。现有的提供方（provider，`bioagent/providers/skills.py`）用一个刻意保持最小化的扁平键读取器解析该 frontmatter，并把所有这类技能一律声明为 `RuntimeSpec(backend="none")`：*“技能是对宿主智能体的规格说明，不是可调用的东西。”*

That is an honest description of the upstream format, and it is the correct
default — but it cannot carry what TCMScience needs. Frontmatter has no place
to state an evidence policy, a permission set, or an output schema. And a skill
declared `backend="none"` cannot be compiled into a PSH plan, so it can never be
benchmarked, audited, or held to its own claims.

<!-- zh -->
这是对上游格式的诚实描述，也是正确的默认值——但它承载不了 TCMScience 所需要的东西。frontmatter 没有地方去声明证据策略（evidence policy）、权限集合或输出 schema。而被声明为 `backend="none"` 的技能无法编译成 PSH 计划，因此它永远无法被基准测试、被审计，也无法被要求为其自身的主张负责。

The tempting shortcut is to infer those properties from prose in the Markdown.
That would make the security argument depend on a language model reading a
README, which is exactly the failure mode the kernel exists to prevent.

<!-- zh -->
一个诱人的捷径是从 Markdown 的散文中推断这些属性。那会让安全论证依赖于某个语言模型去阅读一份 README，而这恰恰是可信内核（trusted kernel, **TK**）存在所要防止的失效模式。

## Decision · 决策

Two files, two audiences, one direction of authority.

<!-- zh -->
两个文件，两类读者，一个权威方向。

- **`SKILL.md`** — human-facing. Purpose, usage, examples, citations. Prose is
  never parsed for authority. It may be absent.
- **`skill.yaml`** — machine-facing. A **declarative, non-Turing-complete**
  document validated against a JSON Schema. It is the only source of the
  skill's declared inputs, outputs, permissions and evidence policy.

<!-- zh -->
- **`SKILL.md`** —— 面向人类。包含用途、用法、示例与引用。散文绝不会被解析以获取权威。该文件可以缺省。
- **`skill.yaml`** —— 面向机器。一份**声明式、非图灵完备**的文档，并依据 JSON Schema 进行校验。它是该技能所声明的输入、输出、权限与证据策略的唯一来源。

For an upstream skill that ships only `SKILL.md`, the Candidate layer holds an
**adapter** `skill.yaml` written by TCMScience. The adapter is what gets
audited and pinned. Wrapping third-party prose does not make it trusted, so:

<!-- zh -->
对于只提供 `SKILL.md` 的上游技能，候选层（Candidate layer）持有一份由 TCMScience 编写的**适配器**（adapter）`skill.yaml`。被审计与被固定版本的是这份适配器。包装第三方散文并不会使它变得可信，因此：

> **Rule.** A skill is compiled into a PSH plan only from `skill.yaml`.
> `SKILL.md` is attached to the artifact as documentation and is never an input
> to compilation, permission derivation or scoring.

<!-- zh -->
> **规则。** 技能只能从 `skill.yaml` 编译进 PSH 计划。
> `SKILL.md` 作为文档附加到产物上，绝不是编译、权限推导或评分的输入。

### Permission derivation is by intersection, never by union · 权限推导取交集，绝不取并集

A skill's effective authority is the **intersection** of what `skill.yaml`
declares, what the enclosing `RunEnvelope` permits, and what
`AuthorityLattice` allows. A skill cannot widen authority, and a manifest that
declares a permission the envelope lacks fails compilation with
`SKILL_PERMISSION_WIDENS_ENVELOPE` rather than being silently trimmed.

<!-- zh -->
一个技能的有效权限（effective authority）是以下三者的**交集**：`skill.yaml` 所声明的、外层 `RunEnvelope` 所允许的，以及 `AuthorityLattice`（权限格）所许可的。技能不能拓宽权限；若某份清单声明的权限是外层信封所没有的，编译会以 `SKILL_PERMISSION_WIDENS_ENVELOPE` 失败，而不是被悄悄裁剪掉。

This mirrors `bridge_manifest`, which already derives a PSH manifest's
`max_label` as the *minimum* ceiling across destinations rather than the
maximum — the same "narrow, never widen" direction.

<!-- zh -->
这与 `bridge_manifest` 的做法一致：它已经把 PSH 清单的 `max_label` 推导为跨所有目标的*最小*上界，而不是最大值——同样是「只收窄、绝不拓宽」的方向。

### A skill must state what it may not claim · 技能必须声明它不得主张什么

`skill.yaml` carries an `evidence` block: the highest `EvidenceTier` it may
cite, the claim kinds it may assert (`ClaimType`), and an explicit
`forbidden_claims` list. The compiler rejects a `ResearchArtifact` whose claims
exceed the declaring skill's policy, so a network-pharmacology skill
structurally cannot emit a clinical efficacy claim.

<!-- zh -->
`skill.yaml` 携带一个 `evidence` 块：它可引用的最高 `EvidenceTier`（证据分级）、它可断言的主张种类（`ClaimType`），以及一份显式的 `forbidden_claims` 列表。编译器会拒绝那些主张超出其声明技能策略的 `ResearchArtifact`，因此一个网络药理学（network pharmacology, **NP**）技能在结构上就不可能输出临床疗效主张。

## Consequences · 后果

**Positive**

- The security argument reduces to reading one declarative file per skill.
- Upstream `SKILL.md` skills remain usable — via a reviewed adapter — without
  pretending their prose is a contract.
- Permission and evidence violations are compilation errors with stable codes,
  not runtime surprises.

<!-- zh -->
**正面**

- 安全论证被简化为：每个技能读一份声明式文件。
- 上游的 `SKILL.md` 技能仍然可用——经由经过评审的适配器——而不必假装它们的散文是一份契约。
- 权限与证据违规是会给出稳定错误码的编译错误，而不是运行时的意外。

**Negative / accepted costs**

- Maintaining adapters for upstream skills is manual work at the Candidate
  layer. This is bounded by the monthly cadence and is the price of not
  auto-installing unreviewed code.
- Some upstream skills cannot be expressed without loss; those are rejected
  rather than approximated.

<!-- zh -->
**负面 / 已接受的代价**

- 为上游技能维护适配器是候选层的人工工作。这一工作量被每月一次的节奏所约束，也是不自动安装未经评审代码所付出的代价。
- 有些上游技能无法无损地表达；这些技能会被拒绝，而不是被近似处理。

## Compliance · 合规

- `bioagent/skills/schema/skill.schema.json` is the normative schema.
- `bioagent.skills.loader` refuses to emit a `SkillSpec` from Markdown alone.
- `bioagent.skills.compiler` is the only path from `SkillSpec` to a PSH plan,
  and it raises the codes above.
- `tests/test_skill_compiler.py` asserts that a skill declaring
  `PUBLIC_REMOTE` under a `LOCAL_COMPUTE`-only envelope is refused.

<!-- zh -->
- `bioagent/skills/schema/skill.schema.json` 是规范性 schema。
- `bioagent.skills.loader` 拒绝仅凭 Markdown 产出 `SkillSpec`。
- `bioagent.skills.compiler` 是从 `SkillSpec` 到 PSH 计划的唯一路径，并且它会抛出上述错误码。
- `tests/test_skill_compiler.py` 断言：在仅允许 `LOCAL_COMPUTE` 的信封下声明 `PUBLIC_REMOTE` 的技能会被拒绝。

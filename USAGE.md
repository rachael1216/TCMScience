# Using the skills, and reading what they return · 使用技能，以及如何解读它们返回的结果

This is the "how do I actually use this" document. It assumes you have run
`python examples/run_skills.py` once (see [INSTALL.md](INSTALL.md)) and want to
know what the output means and where the boundaries are.

<!-- zh -->
这是一份「我到底该怎么用」的文档。它假定你已经跑过一次
`python examples/run_skills.py`（见 [INSTALL.md](INSTALL.md)），现在想知道输出是什么意思、边界又在哪里。

---

## What a skill is · 技能是什么

A skill is a **pure function** from an input to a `ResearchArtifact`. It is not a
conversation, not an agent, and not a service — you call it, it returns a
document, and that document is either publishable or refused. There is no hidden
state and no session to manage.

<!-- zh -->
技能是一个从输入到 `ResearchArtifact` 的**纯函数**。它不是对话，不是智能体，也不是服务——你调用它，它返回一份文档，而这份文档要么可发布（publishable），要么被拒绝（refused）。没有隐藏状态，也没有需要管理的会话。

Each skill has two files and one manifest:

<!-- zh -->
每个技能有两个文件和一个清单（manifest）：

```
BioScience-Harness/skills/tcm/normalize-tcm-entities/
  skill.yaml    the contract — inputs, outputs, permissions, evidence policy
  SKILL.md      documentation for humans; never parsed for authority
```

And one implementation, named by `skill.yaml`:

<!-- zh -->
以及一个实现，由 `skill.yaml` 点名：

```
bioagent/skills/p0/entities.py : normalize_tcm_entities
```

The manifest is the authority. `SKILL.md` is attached to the artifact as
documentation and is **never** an input to compilation, permission derivation or
scoring — otherwise the security argument would reduce to a language model
reading a README. This is ADR-0003.

<!-- zh -->
清单才是权威。`SKILL.md` 只是作为文档附在产物上，**从不**作为编译、权限推导或评分的输入——否则整个安全论证就会退化成「让语言模型去读一份 README」。这就是 ADR-0003。

---

## Three ways to call a skill · 调用技能的三种方式

### The CLI · 命令行接口（CLI）

```bash
python -m bioagent.cli skills --dir BioScience-Harness/skills/tcm     # list them
python -m bioagent.cli skill <id> --arg <name>=<value> ... --dir BioScience-Harness/skills/tcm
```

Every skill names its subject differently (`names`, `subject`, `formula_name`).
The CLI tells you which it wants rather than guessing:

<!-- zh -->
每个技能给自己的主体参数起的名字都不一样（`names`、`subject`、`formula_name`）。CLI 会直接告诉你它要的是哪一个，而不是让你去猜：

```
$ python -m bioagent.cli skill assess-tcm-safety --dir BioScience-Harness/skills/tcm
skill 'assess-tcm-safety' needs --arg subject=<value>
  parameters: subject, co_administered, population, run_id
```

Comma-separated values become lists, which is the only structure the CLI
supports: `--arg names=姜,白芍`.

<!-- zh -->
逗号分隔的值会变成列表，这是 CLI 唯一支持的结构：`--arg names=姜,白芍`。

### Python · Python 调用

```python
from bioagent.skills.p0 import retrieve_tcm_evidence
artifact = retrieve_tcm_evidence("附子", run_id="my-run")
```

See [INSTALL.md](INSTALL.md) for the full table of entry points.

<!-- zh -->
完整的入口点对照表见 [INSTALL.md](INSTALL.md)。

### Inside the governed runtime · 在受治理的运行时内部

A skill also compiles into a `ScientificProgram` that the trusted kernel
validates, which is how it reaches the gateways, budgets and audit chain:

<!-- zh -->
技能还会编译成一个 `ScientificProgram`，由可信内核（trusted kernel, TK）校验，它正是借此接入网关、预算和审计链：

```python
from bioagent.skills.loader import load_skill_dir
from bioagent.skills.compiler import compile_skill

loaded = load_skill_dir("BioScience-Harness/skills/tcm/assess-tcm-safety")
compiled = compile_skill(loaded.spec, envelope)   # envelope: a psh RunEnvelope
```

`compile_skill` **refuses** a skill that asks for authority the envelope does not
hold (`SKILL101`) rather than silently trimming it — a silent trim turns a
misdeclared skill into a mysteriously failing one. You need an `envelope` from a
`psh.policy.PolicySnapshot`; see `tests/test_skill_compiler.py` for a worked
example.

<!-- zh -->
如果某个技能申请的权限是 envelope 并不持有的，`compile_skill` 会**拒绝**它（`SKILL101`），而不是悄悄裁掉——一次悄悄的裁剪，会把一个申报有误的技能变成一个莫名其妙失败的技能。你需要从 `psh.policy.PolicySnapshot` 取得 `envelope`；完整示例见 `tests/test_skill_compiler.py`。

---

## Reading a `ResearchArtifact` · 如何阅读 `ResearchArtifact`

```
normalize-tcm-entities@1.0.0
  question:   resolve 2 TCM name(s) to corpus entities
  versions:   runtime=psh-0.5.3+bioagent-0.2.6|skill=...|source=1982...|benchmark=unversioned
  sources:    1   evidence: 1   claims: 1   outputs: 1
  validation: publishable
  claim [attribution] 1 of 2 queried names resolve to an entity in the seed corpus
  limitations (3):
      - ...
```

| Field | What it is, and why you should care |
| --- | --- |
| `versions` | **Four independent axes** (ADR-0002): runtime, skill, source, benchmark. A result is only reproducible if all four are named. The `source` axis is a hash over the corpus contents — swap the seed data and it moves. |
| `sources` | Pinned `SourceCard`s. A card is *pinned* only when it carries both a `snapshot_hash` and a `snapshot_at`. An unpinned source may not support a publishable claim. |
| `evidence` | Each item carries a **design** (`animal`, `docking`, `randomized_trial`, …), not just a rank. The design is what decides which claims it can license. |
| `claims` | Structured assertions with the population and outcome they *assert* against what their evidence *covers*. |
| `limitations` | **Always non-empty.** An artifact claiming no limitations is claiming to have none, which is never true — so the validator refuses one. |
| `validation` | The real gate. `publishable` or `REFUSED` with stable codes (`ART101`…`ART112`). |

<!-- zh -->
| Field 字段 | What it is, and why you should care 它是什么，以及你为什么要在意 |
| --- | --- |
| `versions` | **四条彼此独立的坐标轴**（ADR-0002）：runtime、skill、source、benchmark。只有当四条轴都被点名时，结果才是可复现的。`source` 轴是语料内容的哈希——换掉种子数据，它就会变。 |
| `sources` | 已固定的 `SourceCard`。一张卡只有在同时带有 `snapshot_hash` 和 `snapshot_at` 时才算*已固定*。未固定的来源不能支撑一条可发布的主张。 |
| `evidence` | 每个证据条目（evidence item, EI）都带有一个**研究设计**（`animal`、`docking`、`randomized_trial`……），而不只是一个等级。决定它能授权哪些主张的，正是这个设计。 |
| `claims` | 结构化断言，带有它们所*主张*的人群与结局，并与其证据*覆盖*的范围相对照。 |
| `limitations` | **永远非空。** 一份声称没有局限的产物，就是在声称自己没有任何局限，而这事从来不是真的——所以校验器会拒绝它。 |
| `validation` | 真正的发布门（release gate）。`publishable`，或者带着稳定错误码（`ART101`…`ART112`）的 `REFUSED`。 |

Every output file is content-addressed: `artifact.outputs[0].sha256` is the
digest of the JSON the skill emitted, so a downstream consumer can verify it got
what the skill produced.

<!-- zh -->
每个输出文件都是内容寻址的：`artifact.outputs[0].sha256` 就是技能所产出 JSON 的摘要，因此下游消费者可以核验自己拿到的确实是技能产出的东西。

---

## What each skill will not do · 每个技能不会做什么

The most useful thing to know about this system is where it declines. Each
refusal is deliberate, tested, and visible in the artifact's `limitations`.

<!-- zh -->
关于这套系统，最值得知道的一件事就是：它在哪些地方会拒绝。每一次拒绝都是刻意的、经过测试的，并且会出现在产物的 `limitations` 里。

| Skill | It refuses to |
| --- | --- |
| `normalize-tcm-entities` | **Choose between ambiguous names.** 「姜」 returns 生姜 *and* 干姜 as candidates; 「甘草」 additionally reports its processed forms. Picking one would be wrong roughly half the time and confident every time. |
| `retrieve-tcm-evidence` | **Adjudicate.** It labels design and reports scope, retraction state and coverage limits. Risk of bias, precision and consistency stay `NOT_ASSESSED` because the corpus carries nothing to judge them on. An empty result is reported as an empty result, not as "no effect". |
| `analyze-tcm-network-pharmacology` | **Mix prediction with measurement.** `predicted_targets` and `measured_targets` are separate keys — there is no combined list. Every edge is marked `directness = EXTRAPOLATED`, and the manifest forbids every clinical claim kind. |
| `assess-tcm-safety` | **Answer "safe".** The status is `risk_recorded`, `no_record` or `unknown`. Absence of a record is not evidence of safety, and the artifact says so in those words. 十八反 is checked as a *set* because it is a property of a pair. |

<!-- zh -->
| Skill 技能 | It refuses to 它拒绝做什么 |
| --- | --- |
| `normalize-tcm-entities` | **在有歧义的名字之间做选择。** 「姜」会同时返回生姜*和*干姜作为候选；「甘草」还会额外报告它的各种炮制品。挑一个的话，大约一半的时候会挑错，而且每一次都信心十足。 |
| `retrieve-tcm-evidence` | **做裁决。** 它只标注研究设计，并报告范围、撤稿状态与覆盖限度。偏倚风险（risk of bias, RoB）、精确度与一致性一律保持 `NOT_ASSESSED`，因为语料里根本没有可供判断的依据。空结果就报成空结果，而不是报成「无效」。 |
| `analyze-tcm-network-pharmacology` | **把预测和实测混在一起。** `predicted_targets` 与 `measured_targets` 是两个分开的键——不存在合并列表。每条边都标记为 `directness = EXTRAPOLATED`，清单也禁止一切临床类主张。 |
| `assess-tcm-safety` | **回答「安全」。** 状态只有 `risk_recorded`、`no_record` 或 `unknown`。没有记录，不等于安全的证据，产物里就是这么写的。十八反是作为一个*集合*来检查的，因为它是成对药物的性质。 |

### Why the prediction rule matters · 为什么预测规则很重要

A docking result can support a **mechanism** claim. It can never support
efficacy, association or safety — those are statements about patients, and a
prediction is not an observation of one. This is enforced by the kernel's type
system, not by a prompt: in PSH's claim-support table, predictive designs appear
in exactly one row.

<!-- zh -->
一个分子对接（docking）结果可以支撑**机制**类主张。它永远无法支撑疗效、关联或安全性——那些都是关于患者的陈述，而预测并不是对患者的观察。这一点由内核的类型系统强制执行，而不是靠提示词：在 PSH 的主张—支撑对照表里，预测类设计只出现在唯一的一行。

So this is allowed:

<!-- zh -->
所以下面这样是允许的：

```python
# mechanism, from docking — the actual job of network pharmacology
claim_kind="mechanism",  evidence designs=["docking"]
```

and this is refused with `ART106` / `CLM004`:

<!-- zh -->
而下面这样会被 `ART106` / `CLM004` 拒绝：

```python
# efficacy, from docking — a category error, not a quality shortfall
claim_kind="efficacy",   evidence designs=["docking"]
```

---

## Where the data comes from · 数据从哪里来

All four skills read the **TCM seed corpus** compiled into `bioagent.tcm`:
23 herbs, 5 processed forms, 6 formulas, 8 syndromes, 9 classical passages,
2 study records, 14 relations, 14 safety records. It is declared as a pinned
`SourceCard` in every artifact, hashed over its *contents*, so a resolution can
be traced to the corpus that produced it.

<!-- zh -->
四个技能读的都是编译进 `bioagent.tcm` 的 **TCM 种子语料**：23 味药、5 种炮制品、6 首方剂、8 个证候、9 条经典条文、2 条研究记录、14 条关系、14 条安全性记录。它在每份产物里都被声明为一张已固定的 `SourceCard`，并*按其内容*做哈希，因此任何一次消解都能追溯到产生它的那份语料。

It is **illustrative, not exhaustive**, and every artifact says so. Notably it
contains **no clinical trial data** — every study record is at
`EXPERT_EXPERIENCE` or below — which is why `retrieve-tcm-evidence` cannot make
an efficacy claim and `assess-tcm-safety` cannot describe a 十八反
contraindication as a `safety_signal`.

<!-- zh -->
它**只是示例性的，并不穷尽**，每一份产物都会这么说。尤其要指出的是，它**不含任何临床试验数据**——每条研究记录都停在 `EXPERT_EXPERIENCE` 或更低——这正是 `retrieve-tcm-evidence` 无法作出疗效主张、`assess-tcm-safety` 无法把一条十八反禁忌描述成 `safety_signal` 的原因。

Adding to it means editing `bioagent/tcm/knowledge.py` and bumping
`SEED_SNAPSHOT_AT` in `bioagent/skills/p0/common.py`; the corpus hash moves
automatically, and `scripts/check_lockfile.py` will tell you the lockfile needs
regenerating.

<!-- zh -->
要往里加东西，就得改 `bioagent/tcm/knowledge.py`，并把 `bioagent/skills/p0/common.py` 里的 `SEED_SNAPSHOT_AT` 往上提；语料哈希会自动变化，`scripts/check_lockfile.py` 会告诉你 lockfile 需要重新生成。

---

## Running the governance layer · 运行治理层

```bash
# what would the monthly scan propose?  (never promotes)
python -m bioagent.cli scout --sources registry/skill_sources.yaml --skills skills

# cut a verifiable release from the lockfile
python -m bioagent.cli registry-release --release-id registry-1.0.0 --season season-1
```

The scout **cannot promote**, and that is structural rather than conventional:
`Registry.promote` is the only writer of a stable entry and it requires a
`PromotionDecision` naming a human. A scheduled run has no code path to one. See
`docs/adr/0001`.

<!-- zh -->
scout **无法执行晋级**，而这是结构性的，不是约定俗成的：`Registry.promote` 是稳定注册表条目的唯一写入者，而且它要求一份指名到人的 `PromotionDecision`。定时运行根本没有通往它的代码路径。见 `docs/adr/0001`。

---

## Troubleshooting · 故障排查

| Symptom | Cause |
| --- | --- |
| `ModuleNotFoundError: No module named 'psh'` | Install the kernel: `pip install -e PSH-Harness`. The two packages are separate and `bioagent` imports `psh`. |
| `ModuleNotFoundError: No module named 'yaml'` | `pip install pyyaml`. It is a declared dependency, so this only happens on a very old install. |
| `no skills found under skills/tcm` | You are not in the repository root. Pass `--dir BioScience-Harness/skills/tcm`. |
| `skill '...' needs --arg <name>=<value>` | The skill wants a different argument name. The message lists its parameters. |
| An artifact comes back `REFUSED` | Read the violation codes. `ART101` means no sources, `ART106` means a clinical claim on prediction-only evidence, `ART110` means no limitations were stated. |

<!-- zh -->
| Symptom 现象 | Cause 原因 |
| --- | --- |
| `ModuleNotFoundError: No module named 'psh'` | 安装内核：`pip install -e PSH-Harness`。这两个包是分开的，而 `bioagent` 会导入 `psh`。 |
| `ModuleNotFoundError: No module named 'yaml'` | `pip install pyyaml`。它已是声明过的依赖，所以只有在非常老的安装里才会出现这种情况。 |
| `no skills found under skills/tcm` | 你不在仓库根目录。请加上 `--dir BioScience-Harness/skills/tcm`。 |
| `skill '...' needs --arg <name>=<value>` | 该技能要的是另一个参数名。报错信息里会列出它的参数。 |
| An artifact comes back `REFUSED` | 去看违规码。`ART101` 表示没有来源，`ART106` 表示在只有预测证据的情况下提出了临床主张，`ART110` 表示没有声明任何局限。 |

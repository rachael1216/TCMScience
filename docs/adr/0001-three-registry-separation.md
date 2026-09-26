# ADR-0001 — Separate Candidate, Stable and Benchmark-Season registries · ADR-0001 —— 候选、稳定与基准赛季三类注册表的分离

- **Status:** Accepted
- **Date:** 2026-09-25
- **Supersedes:** —
- **Related:** [ADR-0002](0002-independent-version-axes.md), [ADR-0003](0003-skill-yaml-compilation-contract.md)

<!-- zh -->
- **状态：** 已接受
- **日期：** 2026-09-25
- **取代：** 无
- **相关：** [ADR-0002](0002-independent-version-axes.md)、[ADR-0003](0003-skill-yaml-compilation-contract.md)

## Context · 背景

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

<!-- zh -->
TCMScience 计划按月吸收上游项目的新技能（K-Dense scientific-agent-skills、ClawBio、PantheonOS、Biomni、BioMedArena、tcm-cli 以及新发布的 `SKILL.md` 文件）。最直观的实现方式——一个定时任务，凡看起来热门的就安装——会摧毁本项目赖以存在的两条根本属性：

1. **可复现性（reproducibility）。** 论文中引用的基准分数必须可被重新推导。如果同一基准版本两次运行之间的技能集合可以发生变化，那么该分数就不可复现。
2. **许可与安全治理。** 在上游技能于可信运行时（trusted runtime）内执行，等同于以运营者权限运行的代码。Star 数并不能证明它适合承担这一角色。

单一的、可变的 `skills/` 目录把三个不同的对象混为一谈，而这三者的生命周期各不相同：我们*发现了什么*、我们*批准了什么*，以及我们*以什么为测量标尺*。

## Decision · 决策

Maintain three separately versioned registries with a one-way promotion path:

| Registry | Updated | Automatic | Role |
| --- | --- | --- | --- |
| **Candidate Skill Catalog** | monthly | yes | everything discovered, never executed in production |
| **Stable Skill Registry** | on human approval | no | the only source the runtime resolves skills from |
| **Frozen Benchmark Registry** | quarterly / half-yearly | no | the fixed yardstick a Season's scores are comparable under |

<!-- zh -->
维护三个独立版本化的注册表，并只允许单向的晋级路径：

| Registry 注册表 | Updated 更新时机 | Automatic 是否自动 | Role 角色 |
| --- | --- | --- | --- |
| **Candidate Skill Catalog**<br>候选技能目录 | 每月 | 是 | 所有已发现的技能；绝不在生产环境中执行 |
| **Stable Skill Registry**<br>稳定注册表 | 人工批准时 | 否 | 运行时解析技能的唯一来源 |
| **Frozen Benchmark Registry**<br>冻结基准注册表 | 每季度 / 每半年 | 否 | 固定的标尺，某个基准赛季（benchmark Season）的分数在此标尺下才可比较 |

The monthly pipeline may write only to the Candidate Catalog. Promotion into
the Stable Registry requires an explicit human decision recorded as a
`PromotionDecision`; a benchmark Season is cut from a Stable Registry revision
and is thereafter immutable.

<!-- zh -->
每月流水线只能写入候选技能目录。晋级进入稳定注册表需要显式的人工决策，并记录为 `PromotionDecision`；一个基准赛季（benchmark Season）从某个稳定注册表修订版本切出，此后即不可变。

**A monthly update must never modify the Benchmark Registry.** Otherwise the
current month's score and last month's are not on the same scale and the
leaderboard silently becomes meaningless. Seasons are the unit of comparability.

<!-- zh -->
**月度更新绝不得修改基准注册表。** 否则本月与上月的分数不在同一量纲上，排行榜会在无人察觉的情况下失去意义。基准赛季（Season）才是可比性的单位。

## Consequences · 后果

**Positive**

- A published score names the exact Stable Registry revision and Benchmark
  Season it was produced under, so it is re-runnable.
- Upstream churn is absorbed at the Candidate layer and cannot reach production
  without a recorded approval.
- Licence and security audits run once, at promotion, rather than on every
  execution.

<!-- zh -->
**正面**

- 已发布的分数会指明它所产生的确切稳定注册表修订版本与基准赛季（benchmark Season），因此可被重新运行。
- 上游的频繁变动被候选层吸收，未经记录的批准无法进入生产环境。
- 许可与安全审计只需在晋级时执行一次，而不必在每次执行时进行。

**Negative / accepted costs**

- New upstream skills are available later than they would be under
  auto-install. This is the point.
- Three registries must be kept consistent; `sources.lock.yaml` records
  provenance so drift is detectable.

<!-- zh -->
**负面 / 已接受的代价**

- 新的上游技能可用时间比自动安装方案更晚。这正是本决策的目的所在。
- 三个注册表必须保持一致；`sources.lock.yaml` 记录溯源信息，使漂移可被检测。

## Compliance · 合规

- `bioagent.updates.promotion` is the *only* writer of a Stable Registry
  revision, and it requires a `PromotionDecision` argument.
- `registry/skills.lock.yaml` changes only in a commit that also contains a
  promotion record.
- The Arena reads a Stable Registry revision and a Season; it never writes.

<!-- zh -->
- `bioagent.updates.promotion` 是稳定注册表修订版本的*唯一*写入者，且它要求传入 `PromotionDecision` 参数。
- `registry/skills.lock.yaml` 只在与晋级记录同处一个提交中时才发生变更。
- Arena 读取稳定注册表修订版本与基准赛季（Season）；它从不写入。

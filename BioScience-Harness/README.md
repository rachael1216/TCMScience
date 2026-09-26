# bioagent-harness v2.6 — a composable harness for biomedical AI agents · 面向生物医学 AI 智能体的可组合执行框架

## v2.7 — scientific data contracts, compiled skills, and a governed update path · 科研数据契约、编译式 Skill 与受治理的更新路径

> **This section describes the newest layer. It is the one most likely to be
> what you came here for if you are looking at how the system keeps a model from
> overstating what it found.**

<!-- zh -->
> **本节描述的是最新的一层。如果你关心的正是这套系统如何防止模型夸大自己的发现，那么这一层很可能就是你要找的东西。**

Four packages were added, and each exists to close a specific way a scientific
agent goes wrong. Two of them changed the *kernel's* vocabulary, which is
recorded in PSH-Harness's `workflow/ir.py` and `workflow/compiler.py` rather than
hidden in a private copy here.

<!-- zh -->
新增了四个包，每一个都为了堵住科研智能体出错的某一条具体路径。其中两个改变了*内核*（kernel）的词汇表，这些改动记录在 PSH-Harness 的 `workflow/ir.py` 与 `workflow/compiler.py` 里，而不是藏在此处的一份私有副本中。

### `bioagent.contracts` — four schemas and one gate · 四个模式与一道门

`SourceCard → EvidenceItem → CandidateClaim → ResearchArtifact`, with
`validate_artifact` as the only entry point a publisher needs. It is a pure
function — no clock, no network, no registry — so a published artifact can be
re-checked by a reviewer holding nothing but the file.

<!-- zh -->
`SourceCard → EvidenceItem → CandidateClaim → ResearchArtifact`，其中 `validate_artifact` 是发布者唯一需要的入口。它是一个纯函数——没有时钟、没有网络、没有注册表——因此一份已发布的科研产物（research artifact, RA），评审者只拿着那个文件就能重新校验一遍。

Two decisions changed what the system can *say*, not just how it says it:

<!-- zh -->
有两个决定改变了系统*能说什么*，而不只是它怎么说：

**Design is stored; the ordinal tier is derived.** `EvidenceTier` is one rank that
collapses `animal` and `in_vitro` into `PRECLINICAL`. An assay and an animal study
license very different claims, so the finer study *design* is now the stored fact
and the tier is recovered on read. The lossy step is visible instead of silent, and
a test asserts this package and PSH agree on the design names.

<!-- zh -->
**被存储的是研究设计，序数化的分级是推导出来的。** `EvidenceTier` 只有一个秩，把 `animal` 与 `in_vitro` 一并压进 `PRECLINICAL`。一次体外测定与一项动物研究所能支撑的主张差别极大，因此现在被存储的事实是更细粒度的研究*设计*（study design），证据分级（evidence tier）在读取时再恢复出来。这个有损的步骤是可见的，而不是无声的；并且有一项测试断言本包与 PSH 对研究设计名称的看法一致。

**Computational prediction cannot become clinical fact.** In PSH's claim-support
table, predictive designs appear in exactly **one** row, `MECHANISTIC`, so a
network-pharmacology skill can claim a mechanism from a docking result and cannot
claim efficacy, association or safety from one. That is the kernel's type system
enforcing it, not a prompt instruction. Overclaiming claims are *constructible*
and refused by the validator with a distinct code (`ART106` / `CLM004`), because a
system that could not represent an overclaim could not measure one either.

<!-- zh -->
**计算预测不能变成临床事实。** 在 PSH 的主张–支持表里，预测性研究设计只出现在**一行**，即 `MECHANISTIC`，因此一个网络药理学（network pharmacology, NP）Skill 可以依据一次对接结果主张某种机制，却不能据此主张疗效、关联或安全性。这是内核的类型系统在强制执行，而不是一句提示词指令。夸大的主张是*可构造的*，会被校验器以一个专门的错误码（`ART106` / `CLM004`）拒绝——因为一个连夸大主张都无法表示的系统，也无法度量夸大。

### `bioagent.skills` — a manifest is compiled, prose is not · 清单被编译，散文不被编译

A skill is compiled from `skill.yaml` into a `ScientificProgram` that **PSH's own
compiler validates**. `SKILL.md` is documentation and is never parsed for
authority — otherwise the security argument would reduce to a language model
reading a README. Authority is by *intersection*: a skill asking for more than the
run envelope holds is refused with `SKILL101`, not silently trimmed.

<!-- zh -->
一个 Skill 从 `skill.yaml` 编译成一个 `ScientificProgram`，并交由 **PSH 自己的编译器校验**。`SKILL.md` 是文档，从不被解析为权威依据——否则整个安全论证就会退化成「一个语言模型在读一份 README」。权限的取得靠的是*交集*：一个 Skill 若索要超出本次运行信封（run envelope）所持有的权限，会被以 `SKILL101` 拒绝，而不是被悄悄裁剪。

Writing this against a real kernel rather than an assumed one found three rules I
had wrong, each now a test: evidence and claims live on *different* tasks
(`EVIDENCE101`/`102`); a repeat-safe declaration or automatic retry needs a
manifest the kernel has verified (`EFFECT106`/`RETRY102`); and a task reaching a
public host can never be `PURE` however deterministic (`EFFECT105`).

<!-- zh -->
把这件事对着一个真实的内核来写，而不是对着一个想象中的内核，让我发现有三条规则我原先理解错了，如今每一条都成为一项测试：证据与主张位于*不同的*任务上（`EVIDENCE101`/`102`）；要声明可安全重复执行或自动重试，需要一份内核已校验过的清单（`EFFECT106`/`RETRY102`）；而一个会触达公网主机的任务，无论多么确定，都永远不可能是 `PURE`（`EFFECT105`）。

### `bioagent.updates` — discovery that cannot activate · 无法自我激活的发现机制

```text
candidate ──audit──▶ 8 hard eliminations ──▶ 100-point score ──human──▶ stable
                                         │                              │
                                    stays visible                 Season frozen
```

`Registry.promote` is the only writer of a stable entry and it requires a
`PromotionDecision`, which refuses an empty `decided_by` — an unattributed
approval is not an approval. The scout CLI has no flag for promotion and cannot
reach a host outside its allowlist. Community growth is capped at 5/100 and the
cap is asserted. A hard elimination runs *before* scoring, so a good total cannot
outvote a licensing problem.

<!-- zh -->
`Registry.promote` 是稳定条目的唯一写入者，它要求一份 `PromotionDecision`，而后者拒绝空的 `decided_by`——一次没有归属人的批准不算批准。scout CLI 没有任何用于晋级（promotion）的开关，也无法访问其允许清单之外的主机。社区来源的增长被限制在 5/100，且这一上限有断言保证。硬性淘汰*先于*打分执行，因此一个漂亮的总分无法压过一次许可问题。

### `bioagent.benchmarks` — where a refusal is not a failure · 拒绝不算失败的地方

Six tracks × 20 cases, eight score dimensions, four hard gates. A hard gate blocks
the **board**, not the score: a run that fabricated a citation still has a
`task_success` number, and zeroing it would hide how well the rest worked. When a
run declines a claim because its evidence does not reach, `claim_calibration`
counts that as correct — a harness that scored abstention as a miss would push
systems toward asserting more.

<!-- zh -->
六个赛道 × 20 个案例，八个评分维度，四道硬门槛（hard gate）。硬门槛封锁的是**排行榜**，不是分数：一次伪造了引用的运行仍然有一个 `task_success` 数值，把它清零反而会掩盖其余部分做得有多好。当一次运行因为证据够不着而拒绝作出某项主张时，`claim_calibration` 会把它计为正确——一个把弃权记作失分的评测框架，会把系统推向更多的主张。

Aggregation is a weighted **harmonic** mean rather than the geometric mean the
plan names: a geometric mean with any zero term is zero, which would make one
failed dimension indistinguishable from a system that scored nothing anywhere.

<!-- zh -->
聚合采用加权**调和**平均，而不是方案里写的几何平均：几何平均只要有一项为零就整体为零，那会让「一个维度失败」与「一个处处得零的系统」变得无法区分。

### Three bugs this layer found in its own foundation · 这一层在自己的地基里发现的三个 bug

Recorded because they are the kind that look like working code:

<!-- zh -->
记录下来，是因为它们正是那种看起来像能跑通的代码的 bug：

1. The skill content hash covered only the manifest *directory*, so it pinned the
   declaration and left `src/bioagent/skills/p0/` editable — an unreviewed
   implementation change would not have moved the pin.

   <!-- zh -->
   Skill 内容哈希只覆盖了清单*目录*，于是它钉住的是声明，却让 `src/bioagent/skills/p0/` 保持可编辑——一次未经评审的实现改动，不会让这个钉扎（pin）发生任何变化。
2. All four entrypoints named `bioagent.skills.p0:fn`, a package, so `find_spec`
   resolved to `__init__.py` rather than the module holding the code.

   <!-- zh -->
   四个入口全都写成 `bioagent.skills.p0:fn`，而这是一个包，于是 `find_spec` 解析到的是 `__init__.py`，而不是真正存放代码的那个模块。
3. The digest embedded a name derived from the path *as the caller spelled it*,
   so a relative and an absolute path to the same tree hashed differently and a
   lockfile generated by the CLI failed to verify under CI. A pin that depends on
   the caller is not a pin.

   <!-- zh -->
   摘要里嵌入的名字，派生自*调用者如何书写*那个路径，于是指向同一棵树的相对路径与绝对路径会得到不同的哈希，CLI 生成的 lockfile 在 CI 下无法通过校验。一个取决于调用者的钉扎，不是钉扎。

### Try it · 试试看

```bash
pip install -e ".[dev]"
PYTHONPATH=src:../PSH-Harness/src python -m pytest -q -m unit      # 847 tests
PYTHONPATH=src:../PSH-Harness/src python scripts/check_lockfile.py  # pins match the tree
python -m bioagent.cli scout --skills skills/tcm --out candidates.json
```

---

## v2.6 — a TCM knowledge layer, a doctor, and a bridge that keeps its promises · 中医药知识层、一位医生，以及一座守承诺的桥

Three things from the 2026-09-18 architecture review (`PSH-Harness/docs/REVIEW_RESPONSE_2026-09-18.md`).

<!-- zh -->
来自 2026-09-18 架构评审的三件事（`PSH-Harness/docs/REVIEW_RESPONSE_2026-09-18.md`）。

**`bioagent.tcm` — typed traditional Chinese medicine knowledge (F11).** A formula is not
a compound, a herb is not its processed form, and 桂枝汤主之 in the Shanghan Lun is an
attribution, not a trial result. `Herb` (nature, flavours, meridians, actions, toxicity,
aliases in three scripts), `ProcessedHerb` (炮制 changes nature and toxicity), `Formula`
(ingredients in 君臣佐使 roles, source, indications, contraindications), `Syndrome` (证
with manifestations, tongue, pulse, treatment principle), `ClassicalPassage`,
`StudyEvidence`, `ActionRelation` carrying an `EvidenceTier`, and `SafetyRecord`.
`EvidenceTier` orders 经典记载 → 专家经验 → 临床前 → 病例 → 观察性 → RCT → 系统评价 and
`CLAIM_KINDS` says which tier a kind of claim needs: a classical passage licenses an
attribution and never an efficacy claim. The knowledge base resolves names without
guessing (参 is 人参 or 丹参 and the caller is told), judges a claim's applicability by
tier and by the population and condition the evidence covers (*extrapolated* when they
differ, *unsupported* when a study is retracted), inherits safety records from a formula's
ingredients, and checks combinations against 十八反. A study above the expert tier must
cite a PMID, DOI or registry id; the checked-in seed cites only public-domain texts, the
pharmacopoeia and a textbook and holds **no invented trials**. Eight native tools expose
it — `tcm_lookup`, `tcm_herb`, `tcm_formula`, `tcm_syndrome`, `tcm_compatibility`,
`tcm_applicability`, `tcm_evidence_tiers`, `tcm_classical_search` — so the toolkit is
147 tools in 12 domains.

<!-- zh -->
**`bioagent.tcm`——带类型的中医药知识（F11）。** 方剂不是化合物，药材不是它的炮制品，而《伤寒论》里的「桂枝汤主之」是一次归属（attribution），不是一次试验结果。这里包括 `Herb`（性味归经、功效、毒性，以及三种文字书写的别名）、`ProcessedHerb`（炮制［processing］会改变性与毒性）、`Formula`（组成按君臣佐使［sovereign–minister–assistant–guide roles］标注，含出处、主治、禁忌）、`Syndrome`（证候［syndrome pattern］，含表现、舌象、脉象、治法）、`ClassicalPassage`、`StudyEvidence`、携带 `EvidenceTier` 的 `ActionRelation`，以及 `SafetyRecord`。`EvidenceTier` 的次序为 经典记载 → 专家经验 → 临床前 → 病例 → 观察性 → RCT → 系统评价，而 `CLAIM_KINDS` 规定一类主张需要哪一档证据：一条经典条文可以支撑一次归属主张，永远不能支撑疗效主张。知识库解析名称而不臆测（「参」是人参还是丹参，会告知调用方），按证据分级、以及按证据所覆盖的人群与病证来判断一项主张的适用性（两者不一致时为*外推*，研究已撤稿时为*无支持*），从方剂的组成药材继承安全性记录，并针对十八反［the eighteen incompatibilities］检查配伍。高于专家经验档的研究必须引用 PMID、DOI 或注册库编号；随包检入的种子只引用公有领域文献、药典与一部教科书，**不含任何编造的试验**。有八个原生工具把它暴露出来——`tcm_lookup`、`tcm_herb`、`tcm_formula`、`tcm_syndrome`、`tcm_compatibility`、`tcm_applicability`、`tcm_evidence_tiers`、`tcm_classical_search`——于是工具箱共有 12 个领域、147 个工具。

**`bioagent doctor` (F12).** `python -m bioagent.cli doctor [--json] [--smoke]` reports
what this installation can do before a run finds out: backends, datasets present /
fetchable / blocked, connectors, native tools (optionally every smoke test), the TCM seed,
PSH's version, PHI detector and isolation report, the network proxy, and a verdict with a
remedy per problem. It reads PSH only through `psh.environment_report()`; `bioagent`
still never imports `psh.kernel`.

<!-- zh -->
**`bioagent doctor`（F12）。** `python -m bioagent.cli doctor [--json] [--smoke]` 在一次运行自己去发现之前，先报告这个安装能做什么：后端（backend）、数据集的存在／可获取／被阻断的状态、连接器、原生工具（可选地跑遍每一个冒烟测试）、中医药种子库、PSH 的版本、PHI 检测器与隔离报告、网络代理，以及一个裁决，并为每个问题给出对应的补救办法。它只通过 `psh.environment_report()` 读取 PSH；`bioagent` 依然从不 import `psh.kernel`。

**Bridge fidelity (F09, F12).** The loop's idempotency key used to be stripped with the
other `_psh_` bookkeeping, so a side-effecting backend could not recognise a replay; it
now reaches `Runtime.invoke(idempotency_key=...)` as the runtime's own keyword, recorded
on the `ToolCalled` event and the result's metadata, and never becomes an argument of the
entrypoint or a field on the wire. A `DEGRADED` result crosses as a `DegradedResult`
whose shortfall PSH records as a caveat and lists as a limitation of the release; a
`TIMEOUT` is a `ToolTimeout`. The isolated entrypoint reports both the same way.

<!-- zh -->
**桥的保真度（F09、F12）。** 循环的幂等键过去会和其他 `_psh_` 记账字段一起被剥掉，于是一个带副作用的 后端无法识别重放；现在它以运行时自身的关键字形式抵达 `Runtime.invoke(idempotency_key=...)`，被记录在 `ToolCalled` 事件与结果的元数据上，并且永远不会变成入口点的参数或链路上的字段。一个 `DEGRADED` 结果以 `DegradedResult` 的形式跨过去，它的缺口被 PSH 记录为一条警示（caveat），并列为该次发布的一项局限；一个 `TIMEOUT` 则是 `ToolTimeout`。隔离入口点对两者的报告方式相同。

## v2.4 — PSH convergence, and a connector set worth converging · PSH 收敛，以及一套值得收敛的连接器

The capability plane is now admitted into a trusted kernel, and it got a lot bigger.
Design and evidence in `docs/V24_PSH_CONVERGENCE.md`. Third-party database connectors (TCMSP, HERB, SymMap, ...) follow `docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md`.

<!-- zh -->
能力平面（capability plane, CP）现在被接纳进一个可信内核（trusted kernel, TK），而且它大了很多。设计与证据见 `docs/V24_PSH_CONVERGENCE.md`。第三方数据库连接器（TCMSP、HERB、SymMap……）遵循 `docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md`。

**`bioagent.psh` — the bridge.** A BioScience component becomes a PSH `ComponentManifest`
with the dimensions PSH's gates rule on, derived conservatively from what the BioScience
manifest declares: a public API is a `PUBLIC_REMOTE` destination with a de-identified
ceiling, a local tool keeps the local ceiling, a declared write makes it mutating and
consequential, a free-text data licence is normalised onto an SPDX id the lattice knows or
left unlicensed. Its `invoke` runs through BioScience's own `Runtime`, so a call crosses
**both** kernels in order — PSH classifies and gates, BioScience resolves, authorises and
executes, PSH labels the result as the join — and neither can be skipped. One harness per
domain at the top of PSH's two-level registry keeps the 2,567-row catalogue to a handful
of manifests of planner context. `isolate=True` runs every component in a PSH child
process with a clean environment behind the kernel's egress proxy. The bridge depends on
PSH; PSH never depends on it, and nothing in `bioagent` imports `psh.kernel` (a test
checks).

<!-- zh -->
**`bioagent.psh`——桥。** 一个 BioScience 组件会变成一个 PSH `ComponentManifest`，携带 PSH 各道门所裁决的那些维度，并从 BioScience 清单所声明的内容保守地推导出来：一个公共 API 是 `PUBLIC_REMOTE` 目的地，天花板为去标识化；一个本地工具保留本地天花板；一次声明的写入使它成为有变更、有后果的操作；一段自由文本的数据许可，会被规范化到权限格（authority lattice）认识的某个 SPDX 标识上，或者留作未许可。它的 `invoke` 走 BioScience 自己的 `Runtime`，于是一次调用按顺序穿过**两个**内核——PSH 分类并设门，BioScience 解析、授权并执行，PSH 把结果标记为这次汇合——两者都不能被跳过。在 PSH 两级注册表的顶层，每个领域一个执行框架，让 2,567 行的目录对规划器而言只剩下寥寥几份清单的上下文。`isolate=True` 会在一个 PSH 子进程里、在干净的环境中、在内核的出网代理之后运行每一个组件。桥依赖 PSH；PSH 从不依赖桥，而且 `bioagent` 中没有任何东西 import `psh.kernel`（有一项测试在查这件事）。

**The kernel boundary.** `EvolutionPipeline` gained a `boundary` stage: a proposal whose
entrypoint, source path or declared writes land in the trusted plane (PSH's kernel,
policy, labels, contracts, licensing; this package's policy kernel and bridge) is
quarantined before a smoke test or benchmark is spent on it.

<!-- zh -->
**内核边界。** `EvolutionPipeline` 增加了一个 `boundary` 阶段：一份提案，如果它的入口点、源码路径或声明的写入落在可信平面（trusted plane，即可信执行平面 trusted execution plane, **TEP**）（PSH 的内核、策略、标签、契约、许可；以及本包的策略内核与桥）之内，就会在它身上花掉任何一次冒烟测试或基准评测之前，被送入隔离区（quarantine）。

**`bioagent.tools` — native tools that run anywhere the harness runs (139 in v2.5, 147 in v2.6).** The census's
honest number was that almost nothing in the 2,567-row catalogue is executable without a
Biomni checkout, a container runtime or forty imports. This is the first tranche that is:
pure Python, no dependencies, deterministic, each with an example that is its smoke test.
Sequence analysis (reverse complement, translation, ORFs, GC, k-mers, codon usage,
primer Tm, restriction sites, oligo mass), protein properties (mass, pI, GRAVY,
hydropathy, extinction coefficient), Needleman–Wunsch and Smith–Waterman alignment
(BLOSUM62 bundled and checked against its published values),
FASTA/FASTQ/VCF/BED/GFF parsers, HGVS parsing, variant normalisation, allele frequencies
with Hardy–Weinberg, Ts/Tv, and statistics from the standard library only
(hypergeometric and Fisher tests, ORA with BH-FDR, Mann–Whitney, Welch's t with the
regularised incomplete beta, CPM/TPM, correlation, diversity, odds ratio, relative risk,
diagnostic metrics, ROC AUC, NNT). And thirty clinical calculators with the formula named
on each: CKD-EPI 2021, Cockcroft–Gault, FENa, corrected calcium and sodium, anion gap,
Henderson–Hasselbalch, alveolar gas, four QTc corrections, MAP, CHA₂DS₂-VASc, HAS-BLED,
Wells DVT/PE, CURB-65, MELD-Na (UNOS 2016), Child–Pugh, NEWS2, GCS, qSOFA, Friedewald,
eAG, Mifflin–St Jeor, Parkland, weight-based dosing, tidal volume, unit conversion,
PHQ-9, GAD-7, Apgar, Bishop, gestational age and Naegele's due date.
Through the bridge they are `LOCAL_COMPUTE` components at the PHI ceiling — a calculator
may see an identifiable payload because nothing leaves the machine, and its result
carries the label onward — which is the label model's point, and `test_psh_bridge.py`
shows the same payload refused at a public connector. Values are pinned against
hand-computed and textbook cases in `tests/test_native_tools.py`.

<!-- zh -->
**`bioagent.tools`——在执行框架能跑的任何地方都能跑的原生工具（v2.5 为 139 个，v2.6 为 147 个）。** 普查给出的诚实数字是：2,567 行的目录里，几乎没有任何一项能在没有 Biomni 检出、没有容器运行时、或者没有四十个 import 的情况下执行。这是可以执行的第一批：纯 Python、无依赖、确定性，每一个都带一个示例，而示例就是它的冒烟测试。序列分析（反向互补、翻译、ORF、GC、k-mer、密码子使用、引物 Tm、限制酶位点、寡核苷酸质量），蛋白质性质（质量、pI、GRAVY、疏水性、消光系数），Needleman–Wunsch 与 Smith–Waterman 比对（BLOSUM62 随包附带，并对照其已发表数值校验），FASTA/FASTQ/VCF/BED/GFF 解析器，HGVS 解析，变异规范化，带 Hardy–Weinberg 的等位基因频率，Ts/Tv，以及只用标准库实现的统计（超几何检验与 Fisher 检验、带 BH-FDR 的 ORA、Mann–Whitney、带正则化不完全 beta 的 Welch t 检验、CPM/TPM、相关性、多样性、比值比、相对风险、诊断指标、ROC AUC、NNT）。还有三十个临床计算器，每一个都写明所用的公式：CKD-EPI 2021、Cockcroft–Gault、FENa、校正钙与校正钠、阴离子间隙、Henderson–Hasselbalch、肺泡气、四种 QTc 校正、MAP、CHA₂DS₂-VASc、HAS-BLED、Wells DVT/PE、CURB-65、MELD-Na（UNOS 2016）、Child–Pugh、NEWS2、GCS、qSOFA、Friedewald、eAG、Mifflin–St Jeor、Parkland、按体重给药、潮气量、单位换算、PHQ-9、GAD-7、Apgar、Bishop、胎龄与 Naegele 预产期。经由桥，它们是 PHI 天花板上的 `LOCAL_COMPUTE` 组件——一个计算器可以看到可识别身份的载荷，因为没有任何东西离开这台机器，而它的结果会把该标签继续带下去——这正是标签模型的意义所在，`test_psh_bridge.py` 展示了同一份载荷在公共连接器上被拒绝。数值在 `tests/test_native_tools.py` 中对着手工计算与教科书案例做了钉扎。

**The second tranche (v0.2.5) adds sixty-two more, in the same discipline.** Pharmacology
(one-compartment kinetics, half-life from two levels, loading and maintenance doses,
accumulation to steady state, Calvert carboplatin, glucocorticoid and morphine-equivalent
conversion, BSA dosing); survival analysis (Kaplan–Meier with Greenwood errors, the
log-rank test — both pinned to the Freireich 6-MP trial and to R's `survdiff`);
inference (inverse-variance meta-analysis with DerSimonian–Laird τ² and I², chi-square
tests, OLS regression with the slope's t and CI, one-way ANOVA, Kruskal–Wallis, Wilcoxon
signed-rank, Cohen's d, Bayesian post-test probability, sample sizes for proportions and
means, exact Poisson incidence-rate intervals — every quantile and tail from the standard
library); population genetics (D′/r², π, Watterson's θ, Tajima's D, G_ST and Hudson's
F_ST); phylogenetics (p/JC69/K2P distances, neighbor joining that recovers the Saitou–Nei
example exactly, UPGMA, a Newick parser and patristic distances); IUPAC motif search,
CpG islands, six-frame translation, CRISPR guide enumeration, sequence entropy and primer
checks; tryptic and other in-silico digests with monoisotopic masses and m/z; SAM, PDB and
OBO parsers; a coding-variant consequence annotator that writes HGVS c. and p.; and twenty
more clinical calculators (the 2013 Pooled Cohort Equations checked against the
guideline's own worked examples, SOFA, osmolality and osmolar gap, Winters' formula and a
full acid–base interpretation with anion gap and delta ratio, Holliday–Segar, free-water
deficit, allowable blood loss, HEART, Centor/McIsaac, Alvarado, TIMI, ABCD², SIRS, RCRI,
STOP-Bang, FIB-4, APRI, HOMA-IR). The acquisition layer gained fourteen size-pinned
natural-product tables — NPASS 2.0, CMAUP 2.0, NP Atlas, LOTUS — plus NCBI Taxonomy and
CellMarker 3.0, as `AcquisitionSpec`s the downloader verifies on fetch.

<!-- zh -->
**第二批（v0.2.5）按同样的纪律再加六十二个。** 药理学（一室动力学、由两个浓度点求半衰期、负荷剂量与维持剂量、累积至稳态、Calvert 卡铂、糖皮质激素与吗啡当量换算、按体表面积给药）；生存分析（带 Greenwood 误差的 Kaplan–Meier、log-rank 检验——两者都对着 Freireich 6-MP 试验与 R 的 `survdiff` 做了钉扎）；推断（带 DerSimonian–Laird τ² 与 I² 的逆方差 meta 分析、卡方检验、带斜率 t 与 CI 的 OLS 回归、单因素 ANOVA、Kruskal–Wallis、Wilcoxon 符号秩、Cohen's d、贝叶斯后验概率、比例与均数的样本量、精确 Poisson 发病率区间——每一个分位数与尾概率都来自标准库）；群体遗传学（D′/r²、π、Watterson's θ、Tajima's D、G_ST 与 Hudson's F_ST）；系统发生学（p/JC69/K2P 距离、能精确复现 Saitou–Nei 例子的邻接法、UPGMA、Newick 解析器与派生距离）；IUPAC 模体搜索、CpG 岛、六框翻译、CRISPR 引导序列枚举、序列熵与引物检查；胰蛋白酶及其他计算机模拟酶切，含单同位素质量与 m/z；SAM、PDB 与 OBO 解析器；一个写出 HGVS c. 与 p. 的编码变异后果注释器；以及另外二十个临床计算器（2013 Pooled Cohort Equations 对着指南自己的演算范例校验，SOFA、渗透压与渗透压间隙、Winters 公式与一套完整的酸碱判读，含阴离子间隙与 delta 比值、Holliday–Segar、自由水缺失、可允许失血量、HEART、Centor/McIsaac、Alvarado、TIMI、ABCD²、SIRS、RCRI、STOP-Bang、FIB-4、APRI、HOMA-IR）。采集层新增十四张锁定大小的天然产物表——NPASS 2.0、CMAUP 2.0、NP Atlas、LOTUS——外加 NCBI Taxonomy 与 CellMarker 3.0，都以 `AcquisitionSpec` 的形式交给下载器在抓取时校验。

**16 → 58 verified public sources, 45 → 153 typed operations.** Structures (AlphaFold DB,
PDBe, InterPro), expression (Human Protein Atlas, GTEx, ENCODE, BioStudies, CELLxGENE,
MetaboLights), pathways and enrichment (WikiPathways, OmniPath, g:Profiler, PANTHER), drug–
gene and cancer genomics (DGIdb, CIViC, cBioPortal, NCI GDC), clinical terminology (ICD-10-CM,
RxTerms, LOINC, HCPCS and conditions via NLM Clinical Tables; RxNav/RxNorm; DailyMed; MeSH),
literature graphs (PubTator 3, Europe PMC Annotations, Crossref, OpenAlex, bioRxiv, EBI
Search) and ontologies (OLS4, HPO, Monarch, Disease Ontology, QuickGO, Bioregistry,
Identifiers.org), and natural products and taxonomy (Wikidata SPARQL — taxa, LOTUS
compound occurrences by taxon and by InChIKey, Chinese-herbology items, any read-only
query — and GBIF name matching and occurrences). Every operation is executed live by
`scripts/verify_connectors.py` and recorded in `data/connector_live_verification.csv`;
`tests/test_public_sources.py` refuses to ship an operation without a `SUCCEEDED` row.
Three sources that did not answer were removed rather than listed on faith.

<!-- zh -->
**16 → 58 个已验证的公共来源，45 → 153 个带类型的操作。** 结构（AlphaFold DB、PDBe、InterPro），表达（Human Protein Atlas、GTEx、ENCODE、BioStudies、CELLxGENE、MetaboLights），通路与富集（WikiPathways、OmniPath、g:Profiler、PANTHER），药物–基因与癌症基因组学（DGIdb、CIViC、cBioPortal、NCI GDC），临床术语（ICD-10-CM、RxTerms、LOINC、HCPCS，以及通过 NLM Clinical Tables 获取的病症；RxNav/RxNorm；DailyMed；MeSH），文献图谱（PubTator 3、Europe PMC Annotations、Crossref、OpenAlex、bioRxiv、EBI Search）与本体（OLS4、HPO、Monarch、Disease Ontology、QuickGO、Bioregistry、Identifiers.org），以及天然产物与分类学（Wikidata SPARQL——分类单元、按分类单元与按 InChIKey 查询 LOTUS 化合物出现记录、中草药条目、任意只读查询——还有 GBIF 名称匹配与出现记录）。每一个操作都由 `scripts/verify_connectors.py` 实时执行，并记录在 `data/connector_live_verification.csv` 中；`tests/test_public_sources.py` 拒绝在没有 `SUCCEEDED` 行的情况下发布一个操作。三个没有应答的来源被删除，而不是凭信心列在那里。

    PYTHONPATH=src:../PSH-Harness/src python demo_convergence.py   # live: HGNC + UniProt through both kernels
    PYTHONPATH=src python scripts/verify_connectors.py --no-write   # re-measure every operation

## v2.3 — execution semantics · 执行语义

v2.2 hardened the declarative layer. This release makes the execution layer
honour it: in several places a manifest or a planner said what should happen and
the backend did something else, or something else's work was scored as if it
were this component's. Pinned by `tests/test_v23_execution_semantics.py`.

<!-- zh -->
v2.2 加固了声明层。本次发布让执行层真正兑现它：有好几处，清单或规划器说了应该发生什么，而后端做了别的事；或者别人的工作被当成这个组件的成绩来计分。这些都由 `tests/test_v23_execution_semantics.py` 钉扎。

**The planner's decisions now reach the backend** · **规划器的决定现在能传达到后端**
- `PlanStep.arguments` had no consumer anywhere in the codebase. A planner could
  decide to call `/lookup/id/ENSG…` with specific parameters and the runtime
  invoked the component with nothing at all. Step arguments now flow through both
  `Runtime.run` and the legacy `BioAgent.run`, taking precedence over run-wide
  defaults.

  <!-- zh -->
  `PlanStep.arguments` 在整个代码库里没有任何消费者。规划器可以决定带上具体参数去调用 `/lookup/id/ENSG…`，而运行时却什么参数都不带地调用了该组件。现在步骤参数会同时流经 `Runtime.run` 与旧版的 `BioAgent.run`，并优先于运行级别的默认值。

**Self-evolution was scoring the wrong thing** · **自我演化当时在给错误的东西打分**
- *Candidates were evaluated by running the incumbent.* `PythonBackend` resolves
  entrypoints by `manifest.id` through the production loader, and a candidate
  normally carries the incumbent's id — so "the candidate improved" was the old
  version compared against itself. `Runtime.invoke_manifest()` now builds a
  scratch registry, resolver, loader and rebound backends so the only reachable
  implementation is the candidate's.

  <!-- zh -->
  *候选者是通过运行在任者来评估的。* `PythonBackend` 通过生产用的加载器按 `manifest.id` 解析入口点，而一个候选者通常携带在任者的 id——于是「候选者进步了」实际上是旧版本在和自己比。现在 `Runtime.invoke_manifest()` 会构建一套一次性的注册表、解析器、加载器与重新绑定的后端，使得唯一可达的实现就是候选者的实现。
- *Benchmarking bypassed the policy kernel.* The evaluator had its own
  simplified chain (`resolve_manifest -> backend.invoke`) that never called
  `authorize()`, so a candidate production would DENY still executed. There is
  now one execution path.

  <!-- zh -->
  *基准评测绕过了策略内核。* 评测器有自己一条简化链（`resolve_manifest -> backend.invoke`），从不调用 `authorize()`，于是一个本应被 DENY 的候选者仍会执行。现在只有一条执行路径。
- *Only the first declared benchmark ran.* A candidate that improved
  `benchmarks[0]` was promoted while a declared safety benchmark it broke was
  never executed. Every benchmark runs; any regression beyond
  `regression_tolerance` blocks promotion; and a component with no incumbent must
  clear `min_absolute_score` instead of skipping the gate entirely.

  <!-- zh -->
  *只有第一个声明的基准评测真正运行。* 一个改善了 `benchmarks[0]` 的候选者会被晋级，而它已经破坏掉的、已声明的安全性基准评测却从未执行。现在每一个基准评测都会运行；任何超出 `regression_tolerance` 的退化都会阻断晋级；而一个没有在任者的组件必须越过 `min_absolute_score`，而不是干脆跳过这道门。

**Resolution reflects reality** · **解析反映现实**
- A required component being *registered* was treated as it being *usable*, so a
  parent reported "dependencies satisfied" over a dependency that was itself
  UNAVAILABLE. Resolution now recurses, with a re-entrancy guard for cycles.

  <!-- zh -->
  一个必需组件只要被*注册*，就被当作*可用*，于是一个父组件会在某个依赖自身处于 UNAVAILABLE 的情况下报告「依赖已满足」。现在解析会递归进行，并带一个针对循环的重入保护。
- `Runtime` built its Resolver with no `dataset_probe`, so the fallback
  `lambda _cid: False` made every dataset component permanently UNAVAILABLE even
  with the file in the lake — and dead-ended `auto_fetch`, whose re-resolution
  after a successful download consulted the same always-False probe. The runtime
  now wires its `DatasetBackend` in.

  <!-- zh -->
  `Runtime` 构建它的 Resolver 时没有传 `dataset_probe`，于是兜底的 `lambda _cid: False` 让每一个数据集组件永久处于 UNAVAILABLE，即便文件就在数据湖里——并且把 `auto_fetch` 逼进了死路，因为它在下载成功之后重新解析时，问的还是那个永远为 False 的探针。现在运行时会把自己的 `DatasetBackend` 接进去。
- Hot reload's scratch resolver dropped the `backend_probe`, silently reverting
  to the permissive default, so a candidate could clear the dependency gate under
  environment assumptions the real runtime does not hold.

  <!-- zh -->
  热重载的一次性解析器丢掉了 `backend_probe`，无声地退回到那个宽松的默认值，于是一个候选者可以在真实运行时并不成立的环境假设下，通过依赖这道门。

**Entrypoints match their sources** · **入口点与它们的源码相符**
- Module paths were derived as `f"{root}.{Path(rel).stem}"`, discarding every
  intermediate package: `biomni/tool/tool_description/pharmacology.py` became
  `biomni.tool.pharmacology`. Measured independently of the deriver, **697 of 817**
  python-backed components disagreed with their own `source_paths`; it is now 0.
  `scripts/entrypoint_census.py` reports CONSISTENT / IMPORTABLE / CALLABLE so the
  error rate is measured rather than assumed.

  <!-- zh -->
  模块路径被推导为 `f"{root}.{Path(rel).stem}"`，把中间每一层包都丢掉了：`biomni/tool/tool_description/pharmacology.py` 变成了 `biomni.tool.pharmacology`。用独立于该推导器的方式测量，**817 个**由 python 支撑的组件中有 **697 个**与它们自己的 `source_paths` 不一致；现在是 0。`scripts/entrypoint_census.py` 报告 CONSISTENT / IMPORTABLE / CALLABLE，好让错误率是被测量出来的，而不是被假设出来的。

**Deny by default** · **默认拒绝**
- An http component declaring no `permissions.network` skipped the host check
  entirely, making an empty allowlist the most permissive setting rather than the
  least. Undeclared endpoints are refused.

  <!-- zh -->
  一个没有声明 `permissions.network` 的 http 组件会完全跳过主机检查，使一份空的允许清单成为最宽松的设置，而不是最严格的设置。未声明的端点一律拒绝。
- The download size gate took the manifest's declared size in preference to the
  server's (`expected_bytes or remote_size`), so a manifest claiming 10 MB waved
  through a 20 GB body; with neither known the hint was 0 and the gate never
  applied. It now takes the largest estimate and also enforces the cap
  mid-stream, aborting on the byte that crosses it.

  <!-- zh -->
  下载大小这道门优先采用清单声明的尺寸，而不是服务器给出的尺寸（`expected_bytes or remote_size`），于是一份声称 10 MB 的清单放行了一个 20 GB 的响应体；而在两者都不知道时，提示值是 0，这道门根本不会生效。现在它取最大的那个估计值，并且在流的中途也执行上限，在越过上限的那个字节上中止。

**Honest reporting** · **诚实的报告**
- *Execution success is not a scientific finding.* The default critique reported
  ACCEPTED for "all steps succeeded", which the runtime turned into
  `ScientificVerdict.ACCEPTED`. A statistical test can run cleanly and return
  p = 0.83. Planners that only watch steps execute now report INCONCLUSIVE, and
  only a validator comparing against a metric, threshold, benchmark or ground
  truth may set ACCEPTED.

  <!-- zh -->
  *执行成功不是科学发现。* 默认的评审对「所有步骤都成功」报告 ACCEPTED，运行时随之把它变成 `ScientificVerdict.ACCEPTED`。一项统计检验可以干干净净地跑完，然后返回 p = 0.83。只看步骤是否执行的规划器现在报告 INCONCLUSIVE，只有拿结果与某个指标、阈值、基准或真值做过比较的校验器，才可以置为 ACCEPTED。
- `ContainerBackend` ran `<rt> run --rm --network none <image>`, ignoring the
  entrypoint and every argument, then reported SUCCEEDED *for that component* and
  advanced it to READY — so every component sharing an image behaved identically.
  It now requires an entrypoint and passes the invocation through.

  <!-- zh -->
  `ContainerBackend` 运行 `<rt> run --rm --network none <image>`，忽略入口点和所有参数，然后*为那个组件*报告 SUCCEEDED 并把它推进到 READY——于是共享同一个镜像的每个组件行为都一模一样。现在它要求必须有入口点，并把调用透传下去。
- `SubprocessBackend` required a `code=` argument no planner path supplies. It
  builds the call from the manifest's `module:function` entrypoint instead.

  <!-- zh -->
  `SubprocessBackend` 要求一个 `code=` 参数，而没有任何规划器路径会提供它。现在它改为从清单的 `module:function` 入口点构建调用。
- `DataLakeAdapter._load_json` called `json.load()` on the whole file before
  slicing to `nrows`, so a bounded read of an 8 GB file parsed 8 GB. JSON Lines is
  detected and streamed; an oversized single document is refused with the reason,
  because a JSON document genuinely cannot be sliced without being parsed.

  <!-- zh -->
  `DataLakeAdapter._load_json` 在切片到 `nrows` 之前先对整个文件调用 `json.load()`，于是一次对 8 GB 文件的有界读取解析了 8 GB。现在 JSON Lines 会被识别并以流式处理；一个过大的单文档会被拒绝并给出原因，因为一份 JSON 文档确实无法在不被解析的情况下切片。

**Release gate** · **发布门**
- `--check` was a junk sweep plus a syntax parse, and both pass on a tree missing
  an entire package — a deleted package has no files to fail. It printed OK while
  `bioagent.workspace` did not exist. The gate now imports a **declared** list of
  public packages (a discovered list cannot detect absence) and compares the tree
  against `git ls-files`.

  <!-- zh -->
  `--check` 过去只是一次垃圾文件清扫加一次语法解析，而这两者在一棵缺了一整个包的树上都会通过——被删掉的包没有文件可以让它失败。它在 `bioagent.workspace` 并不存在的时候打印了 OK。现在这道门会 import 一份**已声明**的公共包列表（一份被发现出来的列表无法察觉缺失），并把工作树与 `git ls-files` 做比对。
- `pyproject.toml` and `__init__.py` disagreed on the version (0.2.2 vs 0.2.1).

  <!-- zh -->
  `pyproject.toml` 与 `__init__.py` 对版本的写法不一致（0.2.2 对 0.2.1）。

## v2.2 — trusted-core hardening · 可信内核加固

No new agents or connectors in this release. It closes gaps between what the
harness *declared* and what it *enforced*, each pinned by a regression test in
`tests/test_v22_trusted_core.py` and `tests/test_packaging.py`.

<!-- zh -->
本次发布没有新增智能体或连接器。它收窄的是执行框架*所声明*的内容与*所执行*的内容之间的落差，每一处都由 `tests/test_v22_trusted_core.py` 与 `tests/test_packaging.py` 中的一项回归测试钉扎。

**Release integrity** · **发布完整性**
- The v2.1 tarball carried ~80 macOS AppleDouble sidecars (`._name`). `._test_*.py`
  is a filename pytest collects, so unpacking the release and running the suite
  failed during collection. `scripts/make_release.py` now sanitizes the tree and
  *refuses* to publish an artifact containing them.

  <!-- zh -->
  v2.1 的 tarball 携带了约 80 个 macOS AppleDouble 附属文件（`._name`）。`._test_*.py` 是 pytest 会收集的文件名，于是解包这次发布再跑测试套件时，会在收集阶段就失败。现在 `scripts/make_release.py` 会清理工作树，并*拒绝*发布任何含有这类文件的产物。
- The wheel shipped no capability catalogue: it was never declared as package
  data, and `catalogue_path()` returned a path that resolves only in a source
  checkout. The catalogue now lives in `src/bioagent/data/` and travels with the
  distribution. CI installs the wheel into a clean venv, outside the repository,
  and loads the catalogue from it — the check a source-checkout suite structurally
  cannot make.

  <!-- zh -->
  wheel 包里没有附带能力目录：它从未被声明为包数据，而 `catalogue_path()` 返回的路径只在源码检出中才解析得到。现在目录位于 `src/bioagent/data/`，随发行包一起走。CI 会把 wheel 安装到一个干净的、位于仓库之外的 venv 里，并从那里加载目录——这是源码检出下的测试套件在结构上无法完成的检查。
- `demo_run.py` shipped with an import spliced into the middle of a string
  literal, so it raised `SyntaxError` on import. Fixed, and the release check now
  parses every shipped python file.

  <!-- zh -->
  `demo_run.py` 发布时有一个 import 被拼接进了字符串字面量的中间，于是它在被 import 时抛出 `SyntaxError`。已修复，并且发布检查现在会解析每一个随包发布的 python 文件。

**Policy enforcement** · **策略执行**
- *auto-fetch no longer bypasses the agent's profile.* `Runtime.fetch()`
  authorized with a hardcoded `profile="biomedical-research"` whatever the spec
  asked for, so an `offline-analysis` agent with `auto_fetch=True` completed a
  download and was only then denied at invoke time — the bytes were already on
  disk. The fetch now runs under the spec's own profile, and a caller that
  supplies none falls back to the most restrictive profile rather than the most
  permissive.

  <!-- zh -->
  *自动抓取不再绕过智能体自己的配置档。* `Runtime.fetch()` 无论 spec 要求什么都用一个硬编码的 `profile="biomedical-research"` 去授权，于是一个 `offline-analysis` 智能体在 `auto_fetch=True` 时完成了一次下载，直到调用时才发现被拒绝——但字节已经落在磁盘上了。现在抓取在 spec 自己的配置档下运行，而一个什么都不提供的调用方，退回的是最严格的配置档，而不是最宽松的。
- *Filesystem capabilities are checked.* `PermissionProfile` had carried
  `allow_filesystem_read` / `allow_filesystem_write` since v2.0 and
  `authorize()` had never looked at them. Declared paths are now ruled against
  the profile's permitted roots, `..` cannot walk out of a root, and — because
  the in-process python backend can enforce no write boundary at all — a
  component declaring writes is refused that backend outright instead of being
  granted a permission nothing enforces.

  <!-- zh -->
  *文件系统能力会被检查。* `PermissionProfile` 从 v2.0 起就带着 `allow_filesystem_read` / `allow_filesystem_write`，而 `authorize()` 从未看过它们。现在声明的路径会对着配置档允许的根目录裁决，`..` 无法走出某个根目录；而且——因为进程内的 python 后端根本执行不了任何写入边界——一个声明了写入的组件会被直接拒绝使用该后端，而不是被授予一项无人执行的权限。
- *Policy propagates along data lineage.* Rulings covered only the component
  named in the request, so a permissively licensed tool whose
  `requires.datasets` pointed at a denied dataset was authorized on its own
  merits. `AuthorizationRequest.dependencies` now carries the resolved closure
  (`Resolver.dependency_contexts()`), most-restrictive-wins, and an
  unresolvable dependency fails closed.

  <!-- zh -->
  *策略沿着数据血缘传播。* 裁决过去只覆盖请求中点名的那个组件，于是一个许可证宽松、但 `requires.datasets` 指向被拒绝数据集的工具，会凭自身的条件获得授权。现在 `AuthorizationRequest.dependencies` 携带解析出的闭包（`Resolver.dependency_contexts()`），取最严格者胜，而一个无法解析的依赖会导致失败关闭（fail closed）。

**Scientific correctness** · **科学正确性**
- *Execution success is no longer reported as scientific acceptance.* A
  validator that ran cleanly and rejected the result produced
  `outcome=SUCCESS, ok=True, accepted=True` while its own critique said the
  validation had failed. `RunReport` now reports `execution_outcome` (did the
  machinery run) and `verdict` (`ACCEPTED` / `REJECTED` / `INCONCLUSIVE`, from
  the critique) separately, and `ok` requires both. `outcome` remains as an
  alias for `execution_outcome`.

  <!-- zh -->
  *执行成功不再被报告为科学上的接受。* 一个跑得干干净净、但拒绝了结果的校验器，会产生 `outcome=SUCCESS, ok=True, accepted=True`，而它自己的评审却说这次校验失败了。现在 `RunReport` 分别报告 `execution_outcome`（机器是否跑起来了）与 `verdict`（`ACCEPTED` / `REJECTED` / `INCONCLUSIVE`，来自评审），而 `ok` 要求两者同时成立。`outcome` 保留为 `execution_outcome` 的别名。

**Environment measurement** · **环境测量**
- *Container availability is measured, not asserted.* The resolver hardcoded
  "container backend requires a container runtime (none available)", so
  installing Docker changed nothing. `Resolver` takes a `backend_probe`, and
  `Runtime` supplies one backed by its live `BackendRegistry`.

  <!-- zh -->
  *容器可用性是被测量出来的，不是被断言的。* 解析器把「容器后端需要容器运行时（当前不可用）」硬编码在里面，于是装了 Docker 也不会有任何变化。`Resolver` 现在接受一个 `backend_probe`，而 `Runtime` 会提供一个由其实时 `BackendRegistry` 支撑的探针。

## v2.1 — what changed · 变更内容
- Ten architectural defects fixed, each pinned by a regression test (`tests/test_v21_regressions.py`);
  one was security-class (path traversal into the immutable policy plane).
- `HTTPBackend` (REST + GraphQL, stdlib-only) with per-host rate limits, retries, response cache.
- 16 public data-source connectors, 45 typed operations, all verified live
  (`data/connector_live_verification.csv`): Ensembl, UniProt, NCBI E-utilities, ChEMBL, PubChem,
  ClinicalTrials.gov, openFDA, STRING, KEGG, Reactome, Open Targets, RCSB PDB, Europe PMC, gnomAD,
  MyGene, MyVariant.
- Acquisition layer: `AcquisitionSpec`, resumable checksum-verified `Downloader`, FETCHABLE resolution,
  `AgentSpec(auto_fetch=True)`, and a CLI:

<!-- zh -->
- 修复了十个架构缺陷，每一个都由一项回归测试钉扎（`tests/test_v21_regressions.py`）；其中一个是安全类缺陷（路径穿越进入不可变的策略平面）。
- `HTTPBackend`（REST + GraphQL，只用标准库），带按主机限速、重试与响应缓存。
- 16 个公共数据源连接器、45 个带类型的操作，全部实时验证过（`data/connector_live_verification.csv`）：Ensembl、UniProt、NCBI E-utilities、ChEMBL、PubChem、ClinicalTrials.gov、openFDA、STRING、KEGG、Reactome、Open Targets、RCSB PDB、Europe PMC、gnomAD、MyGene、MyVariant。
- 采集层：`AcquisitionSpec`、可断点续传且校验和的 `Downloader`、FETCHABLE 解析、`AgentSpec(auto_fetch=True)`，以及一个 CLI：

      PYTHONPATH=src python -m bioagent.cli sources
      PYTHONPATH=src python -m bioagent.cli fetchable
      PYTHONPATH=src python -m bioagent.cli fetch hgnc.dataset.hgnc_complete_set_txt

- Executable-now components: 76 → 96. 764 more have a verified-live equivalent source (routable,
  not yet dispatched per component). See `data/capability_state_census_v21.csv`.

  <!-- zh -->
  当前即可执行的组件：76 → 96。另有 764 个拥有一个已实时验证的等价来源（可路由，但尚未逐个组件派发）。见 `data/capability_state_census_v21.csv`。


Built from a survey of 16 biomedical agent projects (2,567 catalogued capabilities).
v2 rebuilds the v1 scaffold as a **component runtime**: one manifest schema, a trusted
policy kernel, pluggable execution backends, event-sourced provenance, and a validated
self-evolution pipeline.

<!-- zh -->
构建自对 16 个生物医学智能体项目的调研（2,567 项已编目能力）。v2 把 v1 的脚手架重建为一个**组件运行时**：一套清单模式、一个可信的策略内核、可插拔的执行后端、事件溯源的溯源记录，以及一条经过校验的自我演化流水线。

## What v2 corrects · v2 纠正了什么

v1 reported 621 capabilities as "confirmed routable". That was a routing decision, never a
dispatch. v2 measures executability against the live environment and reports it honestly:

<!-- zh -->
v1 曾把 621 项能力报告为「已确认可路由」。那只是一个路由决定，从来不是一次派发。v2 对着真实环境测量可执行性，并诚实地报告：

    catalogued        2,567  (100%)
    dependency-ok     1,024  (39.9%)
    executable now       76  (3.0%)   <- all datasets

Nine v1 defects were reproduced empirically and fixed; each has a regression test in
`tests/test_regressions.py`.

<!-- zh -->
九个 v1 缺陷被实证复现并修复；每一个都在 `tests/test_regressions.py` 中有对应的回归测试。

## Layout · 目录结构

    src/bioagent/
      status.py            ExecutionStatus, LifecycleState, RunOutcome + transition table
      policy.py            immutable policy kernel (read-only license/permission tables)
      config.py            path resolution: argument -> env var -> repo-relative
      data/                packaged runtime data (the capability catalogue)
      runtime/
        component.py       ComponentManifest (the one composable unit)
        registry.py        ComponentRegistry / Resolver / Loader
        events.py          event-sourced provenance with graph replay
        hmr.py             transactional hot reload + LazyComponentSet
        agentspec.py       AgentSpec (data) + Runtime (executes any spec)
      backends/            python | mcp | dataset | subprocess | container | none
      providers/           discovery from catalogue rows, SKILL.md trees and 58 public sources
      tools/               147 native bioinformatics, clinical and TCM tools (no dependencies)
      tcm/                 typed TCM knowledge: herbs, processing, formulas, syndromes, classics,
                           evidence tiers, scope, 十八反
      doctor.py            readiness report with a remedy per problem (bioagent doctor)
      psh/                 the PSH bridge: manifest derivation, the crossing, domain harnesses,
                           the isolated entrypoint (needs PSH-Harness; the rest does not)
      planners/            self-registering plugins: heuristic, llm
      evolution/           propose -> boundary -> test -> benchmark -> policy -> promote
      workspace/           file workspace with an enforced trust boundary + git
      adapters/            v1 adapters (retained; superseded by backends)

## Quick start · 快速开始

    export PYTHONPATH=src
    export BIOAGENT_DATA_LAKE=/path/to/biomni_lake     # optional
    python demo_harness.py                             # full v2 demo
    python -m pytest -q                                # full suite
    python -m pytest -q -m unit                        # no data lake, no network

## Tests · 测试

    tests/test_regressions.py        pins all 8 v1 blocker defects
    tests/test_v2_harness.py         manifests, lifecycle, policy, backends, HMR, evolution
    tests/test_bioagent.py           v1 surface (migrated to status semantics)
    tests/test_v21_regressions.py    the ten v2 architectural-audit defects
    tests/test_v22_trusted_core.py   profile bypass, filesystem capabilities, lineage
                                     propagation, execution-vs-verdict, backend probing
    tests/test_packaging.py          release hygiene: sidecars, parseability, wheel contents
    tests/test_v23_execution_semantics.py
                                     step arguments, candidate isolation, dependency
                                     propagation, dataset probing, deny-by-default,
                                     promotion gates, verdict honesty
    tests/test_psh_bridge.py         the PSH bridge: derivation, the crossing, retrieval,
                                     isolation, the kernel boundary (needs PSH importable;
                                     conftest finds the sibling checkout)
    tests/test_public_sources.py     the connector table and its verification record
    tests/test_native_tools.py       every native tool from its example; values pinned

Measuring the catalogue's python entrypoints:

<!-- zh -->
测量目录中的 python 入口点：

    python scripts/entrypoint_census.py --no-imports   # consistency with source_paths
    python scripts/entrypoint_census.py                # + importable / callable here

Building a release, with the checks that refuse a broken one:

<!-- zh -->
构建一次发布，以及那些会拒绝一个坏发布的检查：

    python scripts/make_release.py --check   # verify the tree; build nothing
    python scripts/make_release.py           # sanitize, build sdist + wheel, verify both

## What "trusted" means here, precisely · 这里的「可信」究竟指什么

This is a **policy-gated, provenance-aware runtime**, not a sandbox. The
distinction is load-bearing, and `PolicyKernel.enforcement_report()` states it
per capability class at runtime rather than leaving it to prose:

<!-- zh -->
这是一个**受策略设门、且感知溯源的运行时**，不是一个沙箱。这个区别是承重的，而 `PolicyKernel.enforcement_report()` 会在运行时按能力类别把它讲清楚，而不是留给散文去说：

| capability 能力 | how it is enforced 如何执行 |
| --- | --- |
| license / integration mode<br>许可 / 集成模式 | **mechanism** — the invocation is refused<br>**机制**——调用被拒绝 |
| subprocess<br>子进程 | **mechanism** — the invocation is refused<br>**机制**——调用被拒绝 |
| filesystem write<br>文件系统写入 | **mechanism** — backends that cannot confine writes are refused<br>**机制**——无法约束写入的后端被拒绝 |
| filesystem read<br>文件系统读取 | **declaration** — declared paths are gated; reads are not intercepted<br>**声明**——声明的路径会被设门；读取不被拦截 |
| network hosts<br>网络主机 | **declaration** — declared hosts are gated; sockets are not intercepted<br>**声明**——声明的主机会被设门；套接字不被拦截 |

"Declaration" means the kernel rules on what a component *declares* and records
the decision; it does not interpose on syscalls, so code that reaches a backend
still runs with the harness's own OS privileges. Genuine capability isolation
needs a container runtime, and `ContainerBackend` / `HardenedExecutor.guarantees()`
report whether one is present rather than assuming either way.

<!-- zh -->
「声明」的意思是：内核裁决一个组件*声明*了什么，并把该决定记录下来；它并不介入系统调用，因此抵达后端的代码仍然以执行框架自身的操作系统权限运行。真正的能力隔离需要一个容器运行时，而 `ContainerBackend` / `HardenedExecutor.guarantees()` 会报告它是否存在，而不是朝任何一边去假设。

## Honesty notes · 诚实说明

* Filesystem and network isolation depend on a container runtime; both
  `ContainerBackend.available()` and `HardenedExecutor.guarantees()` report what
  is actually present on the machine instead of implying it.
* This sandbox refuses to lower RLIMIT_AS, so `HardenedExecutor.guarantees()` probes what
  is actually enforced (CPU + file size) and reports the memory cap as unenforced.
* The Biomni data lake (15.1 GB) is not redistributable; only its inventory ships here.
* Unlicensed upstream projects are never vendored — they are invoked in place, and the
  policy kernel denies a vendor route for them on every call.

<!-- zh -->
* 文件系统与网络隔离依赖一个容器运行时；`ContainerBackend.available()` 与 `HardenedExecutor.guarantees()` 都会报告这台机器上实际存在什么，而不是暗示它存在。
* 本沙箱拒绝下调 RLIMIT_AS，因此 `HardenedExecutor.guarantees()` 探测的是实际被执行的部分（CPU + 文件大小），并把内存上限报告为未执行。
* Biomni 数据湖（15.1 GB）不可再分发；这里只随包发布它的清单。
* 未许可的上游项目从不被 vendored——它们是在原地被调用的，而策略内核在每一次调用上都拒绝为它们走 vendor 路径。

## License · 许可

MIT for this harness. `NOTICE` records every upstream project, its license, and whether it
is vendorable or federated-only.

<!-- zh -->
本执行框架采用 MIT 许可。`NOTICE` 记录了每一个上游项目、它的许可证，以及它是可 vendored 还是仅可联邦式调用。

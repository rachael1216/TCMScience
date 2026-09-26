# v2.4 — PSH ⊕ BioScience: the capability plane under the trusted kernel · v2.4 — PSH ⊕ BioScience：可信内核（trusted kernel, TK）之下的能力平面（capability plane, CP）

The review that produced this repository ended with a shape: keep PSH's `TrustedKernel`
as the immutable base, keep BioScience's registry, resolver, backends, acquisition and
evolution as the capability plane, and write the agent runtime once, on top. PSH v0.6–v0.9
built that runtime. This release builds the seam between the two planes and makes the
capability plane worth admitting: 56 public biomedical and clinical sources, 146 typed
operations, every one executed live before it was listed.

<!-- zh -->
催生本仓库的那次评审，最终收敛出一个形态：把 PSH 的 `TrustedKernel` 保留为不可变基底，把 BioScience 的注册表、解析器、后端、采集与演化保留为能力平面，并在其上一次性编写代理运行时。PSH v0.6–v0.9 构建了该运行时。本次发布构建的是两个平面之间的接缝，并使这个 CP 值得被纳入：56 个公开的生物医学与临床来源、146 个带类型的操作，每一个在被列出之前都经过了实况执行。

## 1. The bridge (`bioagent.psh`) · 1. 桥接层（`bioagent.psh`）

One asymmetry, stated in the package docstring and enforced by a test: **`bioagent.psh`
depends on PSH; PSH never depends on it**, and nothing in `bioagent` imports
`psh.kernel` — the bridge receives a kernel object and calls its public surface.

<!-- zh -->
有一处不对称，写在软件包的 docstring 里并由一个测试强制保证：**`bioagent.psh` 依赖 PSH；PSH 绝不依赖它**，并且 `bioagent` 中没有任何东西导入 `psh.kernel` —— 桥接层接收一个内核对象，并调用它的公开接口。

A BioScience `ComponentManifest` says how a component runs. A PSH `ComponentManifest`
says what the gates need to rule. `bridge_manifest()` derives the second from the first,
with the conservative reading of every dimension:

<!-- zh -->
BioScience 的 `ComponentManifest` 说明一个组件如何运行。PSH 的 `ComponentManifest` 说明各个门（gate）需要据以裁决什么。`bridge_manifest()` 从前者推导出后者，对每一个维度都取保守的读法：

| PSH dimension | derived from | rule |
| --- | --- | --- |
| `destinations` | `runtime.backend`, `permissions.network`, `permissions.filesystem_write` | an `http` connector reaches its declared hosts and nothing local; a local backend reaches `LOCAL_COMPUTE`, plus each host, plus `PERSISTENT` if it writes. A host is `PUBLIC_REMOTE` unless the operator's `HostPolicy` names it trusted. |
| `max_label` | the destinations | the lowest ceiling among them, capped by the operator's local ceiling — a PubMed query accepts at most `RESEARCH_DEIDENTIFIED`, so PHI in a query is refused by the rule that refuses PHI to a public model |
| `risk_tier`, `mutates`, `min_autonomy` | subprocess / container / writes | consequential, mutating, `ACT_WITH_APPROVAL` when any applies; routine, read-only, `OBSERVE` otherwise |
| `license_spdx`, `integration_mode` | `license.spdx`, `license.integration_mode` | an exact alias table maps the free-text data licences this package ships onto SPDX ids the lattice knows (`Public-domain (US Gov)` → `US-Gov-Public-Domain`); anything else stays unlicensed, which the lattice permits for `native`/`federated` use and refuses for `vendor`. The raw string stays in provenance. |
| `allowed_hosts`, `requires_network` | `permissions.network` | as declared; the isolated executor opens exactly those |
| `provenance["description_sensitivity"]` | the description, classified at admission | a description this kernel did not write is text; the rendered manifest item carries its label |

<!-- zh -->
| PSH 维度 | 推导自 | 规则 |
| --- | --- | --- |
| `destinations` | `runtime.backend`、`permissions.network`、`permissions.filesystem_write` | 一个 `http` 连接器只能到达它声明的宿主，到达不了任何本地目标；本地后端能到达 `LOCAL_COMPUTE`，外加每一个宿主，若它会写入则再加上 `PERSISTENT`。一个宿主是 `PUBLIC_REMOTE`，除非操作者的 `HostPolicy` 将其指名为受信任。 |
| `max_label` | 各个目的地（destination） | 取其中最低的上限，并再受操作者本地上限的封顶 —— 一次 PubMed 查询最多接受 `RESEARCH_DEIDENTIFIED`，因此查询中的 PHI 会被那条“拒绝把 PHI 交给公开模型”的规则所拒绝 |
| `risk_tier`、`mutates`、`min_autonomy` | subprocess / container / 写入 | 任一适用即为 consequential、mutating、`ACT_WITH_APPROVAL`；否则为 routine、read-only、`OBSERVE` |
| `license_spdx`、`integration_mode` | `license.spdx`、`license.integration_mode` | 一张精确的别名表把本软件包随附的免费文本（free-text）数据许可证映射到权限格（authority lattice）所认识的 SPDX id（`Public-domain (US Gov)` → `US-Gov-Public-Domain`）；除此之外一律保持为无许可证，权限格允许它用于 `native`/`federated` 用途，而拒绝它用于 `vendor`。原始字符串保留在溯源（provenance）之中。 |
| `allowed_hosts`、`requires_network` | `permissions.network` | 按声明处理；隔离执行器只打开这些 |
| `provenance["description_sensitivity"]` | 该描述，在准入时被分类 | 一段并非由本内核书写的描述就只是文本；渲染后的清单条目携带它的标签 |

`BridgedComponent.invoke()` is the crossing. PSH's broker has already classified the
payload and gated it against the destination when it is called; it maps the payload to
BioScience keyword arguments (a connector payload names an `operation`, rendered through
the source's typed template, whose example values fill optional parameters and **never a
required one**), runs `Runtime.invoke` — resolution, the BioScience policy kernel's
licence and lineage ruling, the backend, the BioScience event log — and translates the
`ExecutionStatus`: `DENIED` is a `PolicyDenied`, `UNAVAILABLE` a `CapabilityUnavailable`,
anything else that did not succeed a `ContractViolation` with a bounded reason. PSH then
labels the result as the join of what went in and what came back. Neither kernel can be
skipped, and the demo prints both audit trails.

<!-- zh -->
`BridgedComponent.invoke()` 就是那次穿越。被调用时，PSH 的 broker 已经对载荷做过分类，并已针对目的地完成门控；它把载荷映射为 BioScience 的关键字参数（一个连接器载荷会给出一个 `operation`，经由该来源的带类型模板渲染，其示例值填充可选参数，**绝不填充必需参数**），运行 `Runtime.invoke` —— 解析、BioScience 策略内核的许可证与血缘（lineage）裁决、后端、BioScience 事件日志 —— 并翻译 `ExecutionStatus`：`DENIED` 是 `PolicyDenied`，`UNAVAILABLE` 是 `CapabilityUnavailable`，其他任何未成功的都是带受限原因的 `ContractViolation`。随后 PSH 把结果标注为“进去的东西”与“回来的东西”的并（join）。两个内核都无法被跳过，演示会打印出两条审计轨迹。

**Two levels of retrieval.** `register_into()` puts one `HARNESS` manifest per BioScience
domain at the top of PSH's registry and the domain's capabilities beneath it. PSH's
resolver narrows to the domains a task needs before it ranks capabilities, so admitting
the 2,567-row catalogue costs the planner fifteen harness descriptions of context, not
2,567 lines. The demo shows "find the HGNC record and the UniProt entry for TP53"
narrowing to genomics, proteomics and literature and ranking UniProt first.

<!-- zh -->
**两级检索。** `register_into()` 把每个 BioScience 域的一条 `HARNESS` 清单放在 PSH 注册表的顶层，并把该域的能力置于其下。PSH 的解析器先收窄到任务所需的那些域，然后再对能力排序，因此纳入这份 2,567 行的目录，只让规划器付出十五条 harness 描述的上下文代价，而不是 2,567 行。演示展示了 “find the HGNC record and the UniProt entry for TP53” 收窄到 genomics、proteomics 与 literature，并把 UniProt 排在首位。

**Isolation, when the policy demands it.** `BioScienceBridge(isolate=True)` declares each
component with PSH's `subprocess` backend and an entrypoint of `python exec.py --manifest
<file>`, the admitted manifest written owner-only under the kernel's state directory.
PSH's `IsolatedExecutor` then runs it in a child with a clean environment — no
`PYTHONPATH`, no inherited secrets, the kernel's egress proxy as the only route out — and
BioScience executes inside it. A policy with `require_isolated_tools=True` refuses the
in-process bridge and admits this one, which is the point. This is also why
`bioagent/__init__.py` now loads its v1 surface lazily: the child imports the package
without pandas, and the bridge's PSH-facing names load on first use so the assembly, the
argument mapping and the kernel boundary work in a process that has BioScience alone.

<!-- zh -->
**隔离，当策略如此要求时。** `BioScienceBridge(isolate=True)` 用 PSH 的 `subprocess` 后端声明每一个组件，entrypoint 为 `python exec.py --manifest <file>`，被准入的清单以仅所有者可写的方式写在内核的状态目录之下。随后 PSH 的 `IsolatedExecutor` 在一个环境干净的子进程中运行它 —— 没有 `PYTHONPATH`，没有继承来的机密，内核的出网代理（egress proxy）是唯一的出口路径 —— BioScience 就在其中执行。一个带 `require_isolated_tools=True` 的策略会拒绝进程内桥接，而准入这一个，这正是要点所在。这也是 `bioagent/__init__.py` 现在惰性加载其 v1 接口的原因：子进程在不含 pandas 的情况下导入该软件包，桥接层面向 PSH 的那些名字在首次使用时才加载，从而让装配、参数映射与内核边界都能在一个只有 BioScience 的进程中工作。

## 2. The kernel boundary (`bioagent.evolution.boundary`) · 2. 内核边界（`bioagent.evolution.boundary`）

The review's one hard rule for self-evolution: an evolution agent may propose changes to
components, skills, prompts and planners and may **never** write the kernel. `KernelBoundary`
makes that structural. A proposal whose entrypoint resolves into `psh.kernel`, PSH's
policy, labels, contracts or licensing, BioScience's own policy kernel, this bridge or the
boundary itself — or whose source path or declared writes land there — is quarantined by
`EvolutionPipeline.submit()` as its `boundary` stage, before a smoke test or a benchmark
is spent on it, whatever it would have scored. The test proposes an "optimisation" that
points a component at `psh.kernel.output_gate` and asserts the smoke runner never ran.

<!-- zh -->
评审为自我演化（self-evolution）定下的唯一一条硬门槛：演化代理可以提议修改组件、技能、提示词与规划器，但**绝不**可以写入内核。`KernelBoundary` 使这一点成为结构性的。若一份提案的 entrypoint 解析落在 `psh.kernel`、PSH 的策略、标签、契约或授权许可、BioScience 自身的策略内核、本桥接层或边界自身之内 —— 或者其源路径或声明的写入落在这些地方 —— 它就会被 `EvolutionPipeline.submit()` 在其 `boundary` 阶段隔离（quarantine），在它身上花费一次冒烟测试（smoke test）或基准测试（benchmark）之前，无论它本可以得多少分。测试提出一个“优化”，把一个组件指向 `psh.kernel.output_gate`，并断言冒烟运行器从未运行过。

## 3. The connector set: 16 → 58 sources, 45 → 153 operations, all verified live · 3. 连接器集合：16 → 58 个来源，45 → 153 个操作，全部经过实况验证

`scripts/verify_connectors.py` renders every operation from its own example arguments,
sends it through the real `HTTPBackend` (rate limits, retries, size caps) and writes one
row per operation to `data/connector_live_verification.csv`. `tests/test_public_sources.py`
then asserts that every shipped operation has a `SUCCEEDED` row. A source is listed because
it answered, and the date on the row says when.

<!-- zh -->
`scripts/verify_connectors.py` 用每个操作自己的示例参数渲染该操作，把它送经真实的 `HTTPBackend`（限流、重试、体积上限），并把每个操作写成一行到 `data/connector_live_verification.csv`。随后 `tests/test_public_sources.py` 断言每一个随包发布的操作都有一行 `SUCCEEDED`。一个来源之所以被列出，是因为它应答了，而那一行上的日期说明了应答的时间。

| domain | sources |
| --- | --- |
| genomics (9) | Ensembl, gnomAD, MyGene, MyVariant, HGNC, NCBI Datasets v2, GWAS Catalog, UCSC, ENA |
| proteomics / structures (7) | UniProt, STRING, InterPro, PRIDE, RCSB PDB, AlphaFold DB, PDBe |
| expression / epigenomics / single-cell / metabolomics (6) | Human Protein Atlas, GTEx, BioStudies (ArrayExpress), ENCODE, CZ CELLxGENE, MetaboLights |
| pathways / networks / enrichment (6) | KEGG, Reactome, WikiPathways, OmniPath, g:Profiler, PANTHER |
| chemistry / drug discovery (4) | ChEMBL, PubChem, Open Targets, DGIdb |
| cancer genomics (3) | CIViC, cBioPortal, NCI GDC |
| clinical / medical terminology (6) | ClinicalTrials.gov, openFDA, NLM Clinical Tables (ICD-10-CM, RxTerms, LOINC, HCPCS, conditions), RxNav/RxNorm, DailyMed, MeSH |
| literature (8) | NCBI E-utilities (PubMed, Gene, ClinVar, dbSNP, GEO), Europe PMC, Europe PMC Annotations, PubTator 3, Crossref, OpenAlex, bioRxiv/medRxiv, EBI Search |
| ontologies / identifiers (7) | OLS4, HPO, Monarch, Disease Ontology, QuickGO, Bioregistry, Identifiers.org |
| natural products / taxonomy (2) | Wikidata SPARQL (taxa by name, LOTUS compounds in a taxon, taxa containing an InChIKey, any read-only query), GBIF (name match, species, occurrences) |

<!-- zh -->
| 域 | 来源 |
| --- | --- |
| genomics (9) | Ensembl、gnomAD、MyGene、MyVariant、HGNC、NCBI Datasets v2、GWAS Catalog、UCSC、ENA |
| proteomics / structures (7) | UniProt、STRING、InterPro、PRIDE、RCSB PDB、AlphaFold DB、PDBe |
| expression / epigenomics / single-cell / metabolomics (6) | Human Protein Atlas、GTEx、BioStudies (ArrayExpress)、ENCODE、CZ CELLxGENE、MetaboLights |
| pathways / networks / enrichment (6) | KEGG、Reactome、WikiPathways、OmniPath、g:Profiler、PANTHER |
| chemistry / drug discovery (4) | ChEMBL、PubChem、Open Targets、DGIdb |
| cancer genomics (3) | CIViC、cBioPortal、NCI GDC |
| clinical / medical terminology (6) | ClinicalTrials.gov、openFDA、NLM Clinical Tables（ICD-10-CM、RxTerms、LOINC、HCPCS、conditions）、RxNav/RxNorm、DailyMed、MeSH |
| literature (8) | NCBI E-utilities（PubMed、Gene、ClinVar、dbSNP、GEO）、Europe PMC、Europe PMC Annotations、PubTator 3、Crossref、OpenAlex、bioRxiv/medRxiv、EBI Search |
| ontologies / identifiers (7) | OLS4、HPO、Monarch、Disease Ontology、QuickGO、Bioregistry、Identifiers.org |
| natural products / taxonomy (2) | Wikidata SPARQL（按名称查类群、某类群中的 LOTUS 化合物、含某个 InChIKey 的类群、任意只读查询）、GBIF（名称匹配、物种、出现记录） |

Wikidata is where the traditional-Chinese-medicine material that has no API of its own
actually lives: herb items, their source taxa, and — through LOTUS — the compounds
recorded in each taxon with InChIKeys that resolve in PubChem and ChEMBL. The service
throttles shared cloud addresses to one query a minute, and the declared rate says so
rather than letting the service refuse; an operator on a better-treated network raises it.

<!-- zh -->
那些自身没有 API 的中医药材料，实际上就栖居在 Wikidata 里：药材条目、它们的来源类群，以及 —— 经由 LOTUS —— 记录在每个类群中的化合物，其 InChIKey 可在 PubChem 与 ChEMBL 中解析。该服务对共享云地址限流为每分钟一次查询，声明的速率把这一点讲明，而不是让服务去拒绝；在受到更好对待的网络上的操作者可以把它调高。

Two mechanical additions made the set expressible: an `Operation` may carry a JSON body
template (g:Profiler's POST API), substituted like `params`; and a GraphQL list variable
receives the typed single value, which GraphQL coerces (DGIdb, CIViC).

<!-- zh -->
两项机械性的增补使这套集合得以表达：一个 `Operation` 可以携带 JSON body 模板（g:Profiler 的 POST API），像 `params` 一样被替换；以及 GraphQL 列表变量接收带类型的单值，由 GraphQL 自行强制转换（DGIdb、CIViC）。

**What is not shipped, and why.** Three sources that were written did not survive
verification and were removed rather than listed on faith: Pathway Commons timed out at
60 s on every operation; PharmGKB was unreachable from the verification network; Semantic
Scholar throttles unauthenticated shared addresses. OpenAlex ships its by-id lookup only,
because its search endpoints spend a per-address daily budget the verification network had
exhausted; bioRxiv ships its by-DOI lookup, because the date-range listing timed out.
Services that need a credential (UMLS, OMIM, DisGeNET, BioGRID, Orphanet, SNOMED CT) are
not public in this module's sense. And traditional-Chinese-medicine resources (TCMSP,
HERB 2.0, SymMap, BATMAN-TCM 2.0, ETCM) publish downloadable tables rather than stable
JSON APIs; they belong to the acquisition layer as datasets, not as connectors to invent.

<!-- zh -->
**哪些没有随包发布，以及为什么。** 有三个已经写好的来源没能通过验证，于是被移除，而不是凭信念把它们列上：Pathway Commons 在每一个操作上都在 60 s 时超时；PharmGKB 从验证网络不可达；Semantic Scholar 对未认证的共享地址限流。OpenAlex 只随包发布其按 id 查询，因为它的搜索端点要消耗按地址计的每日配额，而验证网络已把该配额耗尽；bioRxiv 随包发布其按 DOI 查询，因为按日期范围列出的那一个超时了。需要凭据的服务（UMLS、OMIM、DisGeNET、BioGRID、Orphanet、SNOMED CT）不是本模块意义上的公开服务。而中医药资源（TCMSP、HERB 2.0、SymMap、BATMAN-TCM 2.0、ETCM）发布的是可下载的表格，而不是稳定的 JSON API；它们作为数据集属于采集层，而不是等待被凭空造出的连接器。

**v0.2.5 did that step, with the sources that actually answer.** Probed from this harness:
HERB's per-file download URLs return HTML pages, BATMAN-TCM's download page answered 503,
and TCMSP, HIT 2.0 and TCMBank did not answer at all — so none of them can be pinned.
What can be, and now is, in `acquisition/sources.py` as size-pinned `AcquisitionSpec`s:
the six NPASS 2.0 tables (natural products, source organisms with taxonomy, structures,
targets, quantitative activities with references, organism pairs) and the five CMAUP 2.0
tables (medicinal plants, ingredients, targets, plant→ingredient and ingredient→target
activities), both from BIDD and covering the same herbs, compounds and targets the TCM
databases do; NP Atlas; and LOTUS's frozen Wikidata export with the md5 values Zenodo
publishes. NCBI Taxonomy and CellMarker 3.0 round out the tranche as reference data.
`tests/test_datasets.py` checks every spec is well-formed, its host allowlisted, and the
natural-product tranche pinned; `Downloader.fetch` verifies size or checksum on acquisition.

<!-- zh -->
**v0.2.5 完成了那一步，用的是真正能应答的那些来源。** 从本测试框架探针（probe）得到的结果：HERB 的按文件下载 URL 返回 HTML 页面，BATMAN-TCM 的下载页应答 503，而 TCMSP、HIT 2.0 与 TCMBank 则完全没有应答 —— 因此它们没有一个能被固定下来。能被固定的 —— 并且现在已被固定在 `acquisition/sources.py` 中作为按大小固定的 `AcquisitionSpec` —— 是：六张 NPASS 2.0 表（天然产物、带分类学的来源生物、结构、靶点、带参考文献的定量活性、生物对）与五张 CMAUP 2.0 表（药用植物、成分、靶点、植物→成分与成分→靶点活性），两者都来自 BIDD，覆盖与那些中医药数据库相同的药材、化合物与靶点；NP Atlas；以及 LOTUS 的冻结 Wikidata 导出，带有 Zenodo 发布的 md5 值。NCBI Taxonomy 与 CellMarker 3.0 作为参考数据补足这一批。`tests/test_datasets.py` 检查每一份 spec 形式正确、其宿主已在允许列表之内，以及天然产物这一批已被固定；`Downloader.fetch` 在采集时校验大小或校验和。

Every new host is allowlisted in the `biomedical-research` profile (the BioScience policy
kernel refuses undeclared hosts), and every host has a declared request rate.

<!-- zh -->
每一个新的宿主都已在 `biomedical-research` profile 中加入允许列表（BioScience 策略内核会拒绝未声明的宿主），并且每一个宿主都有一段声明的请求速率。

## 3b. Native tools: 139 capabilities executable anywhere the harness runs · 3b. 原生工具：139 项能力，凡测试框架可运行之处皆可执行

The census's central number was honest and uncomfortable: of 2,567 catalogued
capabilities, the executable ones on a machine without a Biomni checkout, a container
runtime or forty third-party imports were the datasets. `bioagent.tools` is the first
tranche of capability that is executable *everywhere*: pure Python, no dependencies,
deterministic, each a function of keyword arguments returning a JSON-serialisable dict,
each with an example that is its smoke test (`native:<name>` in the manifest;
`native_smoke_runner` serves the hot reloader and the evolution pipeline).

<!-- zh -->
那次普查的核心数字既诚实又令人不适：在 2,567 项已编目的能力当中，在一台没有 Biomni 检出、没有容器运行时、也没有四十个第三方导入的机器上可执行的，只有那些数据集。`bioagent.tools` 是第一批*处处*可执行的能力：纯 Python、无依赖、确定性，每一个都是把关键字参数映射为可 JSON 序列化 dict 的函数，每一个都带一个作为其冒烟测试的示例（清单中的 `native:<name>`；`native_smoke_runner` 为热重载器与演化流水线服务）。

| domain | tools |
| --- | --- |
| sequence analysis (18) | reverse complement, transcription, translation, GC content (windowed), ORF finding, k-mer counts, Hamming and edit distance, codon usage, primer Tm (Wallace / salt-adjusted), restriction sites (20 enzymes), oligo mass; IUPAC motif search, CpG islands (Gardiner-Garden & Frommer), six-frame translation, CRISPR guide enumeration, Shannon entropy / low complexity, primer checks (clamp, runs, self-complementarity, hairpin, template sites) |
| protein analysis (4) | mass, pI (EMBOSS pKa set), GRAVY, composition, aromaticity, extinction coefficient; Kyte–Doolittle hydropathy profile; peptide monoisotopic/average mass and m/z; in-silico digestion (trypsin, Lys-C, Arg-C, chymotrypsin, Glu-C, Asp-N) with missed cleavages |
| alignment (3) | Needleman–Wunsch global, Smith–Waterman local, linear gaps, optional substitution matrix; protein alignment with the bundled BLOSUM62 (symmetry and published diagonal checked by test) |
| file formats (8) | FASTA, FASTQ (quality statistics), VCF (INFO and genotypes), BED, GFF3/GTF, SAM (flags, CIGAR, mapping summary), PDB (chains, sequences, hetero groups, centroid), OBO ontologies |
| variants (5) | HGVS parsing (c./g./n./m./r. substitution, deletion, duplication, insertion, delins; p. substitution, nonsense, frameshift, synonymous), variant normalisation and keys, allele frequencies with Hardy–Weinberg, Ts/Tv; coding-variant consequence (synonymous, missense, nonsense, stop/start loss, in-frame and frameshift indels) with HGVS c. and p. |
| statistics (27) | hypergeometric and Fisher exact tests, ORA with Benjamini–Hochberg, Mann–Whitney U, Welch's t (regularised incomplete beta), log2 fold change, CPM, TPM, Pearson/Spearman, Shannon/Simpson, odds ratio and relative risk with CIs, diagnostic metrics, ROC AUC, NNT; inverse-variance meta-analysis (fixed and DerSimonian–Laird random effects, Q, I², τ²), chi-square tests with Cramér's V, OLS regression, one-way ANOVA, Kruskal–Wallis, Wilcoxon signed-rank, Cohen's d / Hedges' g, Bayesian post-test probability, sample sizes for two proportions and two means, exact Poisson incidence rates |
| pharmacology (9) | one-compartment kinetics, half-life from two levels, loading dose, maintenance dose, accumulation and time to steady state, Calvert carboplatin dosing, glucocorticoid equivalence, CDC morphine milligram equivalents, BSA dosing |
| survival analysis (2) | Kaplan–Meier with Greenwood standard errors and median; log-rank test with the Pike hazard-ratio estimate |
| population genetics (3) | linkage disequilibrium (D, D′, r²), nucleotide diversity with Watterson's θ and Tajima's D, G_ST and Hudson's F_ST |
| phylogenetics (5) | p / Jukes–Cantor / Kimura two-parameter distances, neighbor joining, UPGMA, Newick parsing, patristic distances |
| clinical calculators (55) | BMI, BSA, ideal/adjusted body weight, CKD-EPI 2021, Cockcroft–Gault, FENa, corrected calcium, anion gap, corrected sodium, Henderson–Hasselbalch, alveolar gas and A–a gradient, QTc (Bazett, Fridericia, Framingham, Hodges), MAP, CHA₂DS₂-VASc, HAS-BLED, Wells DVT and PE, CURB-65, MELD-Na (UNOS 2016), Child–Pugh, NEWS2, GCS, qSOFA, Friedewald LDL, HbA1c→eAG, Mifflin–St Jeor, Parkland, weight-based dosing, tidal volume, unit conversion, PHQ-9, GAD-7, Apgar, Bishop, gestational age with Naegele's due date; 2013 Pooled Cohort Equations (ASCVD), SOFA, calculated osmolality and osmolar gap, Winters' formula, acid–base interpretation with anion gap and delta ratio, Holliday–Segar, free-water deficit, allowable blood loss, infusion rate, HEART, Centor/McIsaac, Alvarado, TIMI UA/NSTEMI, ABCD², SIRS, RCRI, STOP-Bang, FIB-4, APRI, HOMA-IR |

<!-- zh -->
| 域 | 工具 |
| --- | --- |
| sequence analysis (18) | 反向互补、转录、翻译、GC 含量（滑窗）、ORF 查找、k-mer 计数、汉明距离与编辑距离、密码子使用、引物 Tm（Wallace / 盐校正）、限制性酶切位点（20 种酶）、寡核苷酸质量；IUPAC 基序搜索、CpG 岛（Gardiner-Garden & Frommer）、六框翻译、CRISPR guide 枚举、Shannon 熵 / 低复杂度、引物检查（clamp、连续碱基、自互补、发夹、模板位点） |
| protein analysis (4) | 质量、pI（EMBOSS pKa 集）、GRAVY、组成、芳香性、消光系数；Kyte–Doolittle 疏水性剖面；肽的单同位素/平均质量与 m/z；计算机模拟酶切（trypsin、Lys-C、Arg-C、chymotrypsin、Glu-C、Asp-N）并计漏切位点 |
| alignment (3) | Needleman–Wunsch 全局、Smith–Waterman 局部、线性空位、可选替换矩阵；用随包 BLOSUM62 做蛋白比对（对称性与公布的对角线由测试校验） |
| file formats (8) | FASTA、FASTQ（质量统计）、VCF（INFO 与基因型）、BED、GFF3/GTF、SAM（flag、CIGAR、比对摘要）、PDB（链、序列、杂原子基团、质心）、OBO 本体 |
| variants (5) | HGVS 解析（c./g./n./m./r. 的替换、缺失、重复、插入、delins；p. 的替换、无义、移码、同义）、变异规范化与键、带 Hardy–Weinberg 的等位基因频率、Ts/Tv；编码变异后果（同义、错义、无义、终止/起始丢失、框内与移码 indel），并给出 HGVS c. 与 p. |
| statistics (27) | 超几何与 Fisher 精确检验、带 Benjamini–Hochberg 的 ORA、Mann–Whitney U、Welch t 检验（正则化不完全 beta）、log2 倍数变化、CPM、TPM、Pearson/Spearman、Shannon/Simpson、带置信区间的比值比与相对风险、诊断指标、ROC AUC、NNT；逆方差荟萃分析（固定效应与 DerSimonian–Laird 随机效应、Q、I²、τ²）、带 Cramér's V 的卡方检验、OLS 回归、单因素方差分析、Kruskal–Wallis、Wilcoxon 符号秩、Cohen's d / Hedges' g、贝叶斯后验概率、两比例与两均值的样本量、精确 Poisson 发病率 |
| pharmacology (9) | 一室动力学、由两点血药浓度求半衰期、负荷剂量、维持剂量、蓄积与达稳态时间、Calvert 卡铂剂量、糖皮质激素等效换算、CDC 吗啡毫克当量、BSA 剂量 |
| survival analysis (2) | 带 Greenwood 标准误与中位数的 Kaplan–Meier；带 Pike 风险比估计的 log-rank 检验 |
| population genetics (3) | 连锁不平衡（D、D′、r²）、带 Watterson's θ 与 Tajima's D 的核苷酸多样性、G_ST 与 Hudson's F_ST |
| phylogenetics (5) | p / Jukes–Cantor / Kimura 双参数距离、邻接法、UPGMA、Newick 解析、谱系距离 |
| clinical calculators (55) | BMI、BSA、理想/调整体重、CKD-EPI 2021、Cockcroft–Gault、FENa、校正钙、阴离子间隙、校正钠、Henderson–Hasselbalch、肺泡气体与 A–a 梯度、QTc（Bazett、Fridericia、Framingham、Hodges）、MAP、CHA₂DS₂-VASc、HAS-BLED、Wells DVT 与 PE、CURB-65、MELD-Na（UNOS 2016）、Child–Pugh、NEWS2、GCS、qSOFA、Friedewald LDL、HbA1c→eAG、Mifflin–St Jeor、Parkland、按体重给药、潮气量、单位换算、PHQ-9、GAD-7、Apgar、Bishop、带 Naegele 预产期的孕龄；2013 Pooled Cohort Equations（ASCVD）、SOFA、计算渗透压与渗透压间隙、Winters 公式、带阴离子间隙与 delta ratio 的酸碱判读、Holliday–Segar、自由水缺失、可允许失血量、输注速率、HEART、Centor/McIsaac、Alvarado、TIMI UA/NSTEMI、ABCD²、SIRS、RCRI、STOP-Bang、FIB-4、APRI、HOMA-IR |

Through the bridge each is a `LOCAL_COMPUTE` component at the local ceiling: a clinical
calculator may be handed an identifiable payload because nothing leaves the machine, and
its result carries the PHI label onward. `test_a_phi_payload_runs_locally_and_is_refused_remotely`
is the label model in one test — the same payload reaches the calculator and never reaches
a public connector. The eleven toolkit domains are eleven harnesses in PSH's registry, so
"estimate kidney function from creatinine" ranks the CKD-EPI tool without the planner
having seen a hundred and thirty-nine manifests.

<!-- zh -->
经由桥接层，每一项都是处于本地上限的 `LOCAL_COMPUTE` 组件：临床计算器可以被交付可识别的载荷，因为没有东西离开本机，而它的结果会把 PHI 标签继续携带下去。`test_a_phi_payload_runs_locally_and_is_refused_remotely` 用一个测试表达了整个标签模型 —— 同一份载荷能到达计算器，绝不到达公开连接器。十一个工具包域就是 PSH 注册表中的十一条 harness，因此 “estimate kidney function from creatinine” 会排到 CKD-EPI 工具，而规划器无须看过一百三十九条清单。

Correctness is pinned, not assumed: `tests/test_native_tools.py` runs every tool from
its example and checks values by hand (CKD-EPI 2021 for a 50-year-old at Scr 1.0 is
68.6 / 91.7; MELD-Na for bilirubin 3, INR 2, creatinine 2, sodium 128 is 29; Fisher's
tea-tasting table gives 0.4857; t = 2.228 at 10 df gives 0.05; the Freireich 6-MP arm's
Kaplan–Meier curve is 0.857 → 0.448 with a median of 23 weeks and its log-rank statistic
is 16.79, as R reports; neighbor joining recovers the Saitou–Nei five-taxon tree with
every path length exact; the Pooled Cohort Equations reproduce the guideline's four
worked examples to 0.1 %). A structural test refuses any tool whose parameter name would
be swallowed by `Runtime.invoke`'s own keywords, which the survival tools' first draft
found the hard way. Every calculator names
its formula in its docstring and refuses out-of-range input with a reason, and none of
them returns a recommendation — only the interpretation bands its source publishes.

<!-- zh -->
正确性是被固定下来的，而不是被假定的：`tests/test_native_tools.py` 用每个工具的示例运行它并手工核对数值（50 岁、Scr 1.0 的 CKD-EPI 2021 为 68.6 / 91.7；胆红素 3、INR 2、肌酐 2、钠 128 的 MELD-Na 为 29；Fisher 品茶表给出 0.4857；10 自由度下 t = 2.228 给出 0.05；Freireich 6-MP 组的 Kaplan–Meier 曲线为 0.857 → 0.448，中位数 23 周，其 log-rank 统计量为 16.79，与 R 报告一致；邻接法还原出 Saitou–Nei 五类群树，每条路径长度都精确；Pooled Cohort Equations 复现该指南的四个演算示例，误差在 0.1 % 以内）。一项结构性测试拒绝任何参数名会被 `Runtime.invoke` 自身关键字吞掉的工具，这是生存分析工具的第一版以惨痛方式发现的。每个计算器都在其 docstring 中写明公式，并以给出理由的方式拒绝越界输入，而且它们都不返回建议 —— 只给出其来源所发布的判读区间。

## 4. Tests · 4. 测试

* `tests/test_psh_bridge.py` — 21 tests: manifest derivation on every dimension; the
  operator's trusted-host decision; licence normalisation and a permissive-only run
  excluding KEGG while keeping Ensembl; id sanitisation and shadow refusal; PHI never
  reaching a public connector (the transport and the BioScience runtime both untouched);
  a clean call crossing both kernels in order with both audit trails; a BioScience denial
  surfacing as `PolicyDenied`; an unrunnable component as `CapabilityUnavailable`;
  operation contract violations named before any request; two-level retrieval over
  domain harnesses; a local-only run seeing no public connectors; descriptions classified
  at admission; a component running in a kernel child process; a policy requiring
  isolation refusing the in-process bridge; the structural rule that `bioagent` never
  imports `psh.kernel`; the boundary naming every way into the trusted plane; and the
  pipeline quarantining a kernel-targeting proposal before its smoke test.
* `tests/test_public_sources.py` — every source internally consistent and renderable
  from its own example, JSON bodies and GraphQL variables templated, every source a valid
  http manifest, and the verification record complete.
* `tests/test_native_tools.py` — every native tool runs its example, is deterministic and
  returns JSON; the provider's manifests are valid offline components; the python backend
  loads and runs all 139; values pinned against hand-computed and textbook cases; bad input
  is a reason, not a traceback.
* `tests/test_datasets.py` — every bulk dataset spec well-formed and host-allowlisted; the
  natural-product tranche pinned by size, LOTUS and CellMarker by Zenodo's md5.
* The suite runs from a plain clone: `tests/conftest.py` puts the sibling `PSH-Harness/src`
  on the path when `psh` is not installed. The root `.github/workflows/ci.yml` runs both
  packages and the bridge; live verification is a manual job.

<!-- zh -->
* `tests/test_psh_bridge.py` —— 21 个测试：每一个维度上的清单推导；操作者对受信任宿主的决策；许可证规范化，以及一次仅允许宽松许可证的运行排除 KEGG 而保留 Ensembl；id 清洗与影子拒绝；PHI 绝不到达公开连接器（传输层与 BioScience 运行时两者都未被触及）；一次干净的调用按顺序穿越两个内核并留下两条审计轨迹；一次 BioScience 的拒绝以 `PolicyDenied` 浮出；一个不可运行的组件表现为 `CapabilityUnavailable`；操作契约违规在任何请求之前就被指名；跨域 harness 的两级检索；一次仅本地的运行看不到任何公开连接器；描述在准入时被分类；一个组件在内核子进程中运行；一个要求隔离的策略拒绝进程内桥接；`bioagent` 绝不导入 `psh.kernel` 的结构性规则；边界层指名了通往可信平面的每一条路径；以及流水线在冒烟测试之前就把一份瞄准内核的提案隔离。
* `tests/test_public_sources.py` —— 每一个来源都内部一致，且能用自己的示例渲染，JSON body 与 GraphQL 变量被模板化，每一个来源都是合法的 http 清单，验证记录完整。
* `tests/test_native_tools.py` —— 每一个原生工具都运行其示例、具备确定性并返回 JSON；该提供者的清单是合法的离线组件；python 后端加载并运行全部 139 个；数值对照手工计算与教科书案例固定；坏输入给出的是理由，而不是回溯。
* `tests/test_datasets.py` —— 每一份批量数据集 spec 形式正确且宿主在允许列表之内；天然产物这一批按大小固定，LOTUS 与 CellMarker 按 Zenodo 的 md5 固定。
* 测试套件可以从一个普通克隆直接运行：当 `psh` 未安装时，`tests/conftest.py` 会把同级的 `PSH-Harness/src` 放到路径上。根部的 `.github/workflows/ci.yml` 运行两个软件包与桥接层；实况验证是一项手动任务。

## 5. Honest state · 5. 诚实状态

* The bridge admits the connector set and the catalogue; catalogue components that need
  a Biomni checkout or a container runtime resolve to `UNAVAILABLE` here exactly as they do
  in BioScience alone, and reach PSH as `CapabilityUnavailable`. The bridge changes what
  is *governed*, not what is *installed*.
* The isolated child runs under `NoSandbox` on this machine; the egress proxy governs
  proxy-honouring clients (the HTTP backend is one). Genuine containment still needs the
  OS layer PSH's `SandboxBackend` seam is for.
* Admitting the full catalogue re-registers 2,567 manifests per process (about a second).
  A persistent, content-addressed admission cache is the obvious next step and is not
  built.

<!-- zh -->
* 桥接层纳入了连接器集合与目录；需要 Biomni 检出或容器运行时的目录组件，在这里解析为 `UNAVAILABLE`，与它们在单独运行的 BioScience 中完全一样，并以 `CapabilityUnavailable` 到达 PSH。桥接层改变的是*被治理*的东西，而不是*被安装*的东西。
* 隔离子进程在本机上运行于 `NoSandbox` 之下；出网代理只能管住遵守代理的客户端（HTTP 后端就是其中之一）。真正的围堵仍然需要 PSH 的 `SandboxBackend` 接缝所预留的那个操作系统层。
* 纳入完整目录会在每个进程中重新注册 2,567 条清单（约一秒）。一个持久的、按内容寻址的准入缓存是显而易见的下一步，而它尚未被构建。

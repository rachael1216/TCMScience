# Third-party public database unified connector interface specification (TCM-DB Connector Spec v1) · 第三方公开数据库统一接入接口规范（TCM-DB Connector Spec v1）

> Scope: TCMSP, HERB, SymMap, ETCM, BATMAN-TCM, TCMBank, HIT, TCMID, NPASS, CMAUP
> and other traditional Chinese medicine / natural-product databases, as well as other
> public research databases that **have no formal API and must be encapsulated by
> analysing the requests issued by the web front end**.
>
> This specification does not start from scratch; it extends mechanisms that already
> exist in the repository:
>
> | Existing mechanism | Location | Role in this specification |
> | --- | --- | --- |
> | `Operation` / `PublicSource` | `src/bioagent/providers/public_apis.py` | Declarative request templates → extended into the Source Manifest of §4 |
> | `AcquisitionSpec` | `src/bioagent/acquisition/sources.py` | Bulk download channel (L1 in §3) |
> | `HTTPBackend` | `src/bioagent/backends/http.py` | Unified transport layer: rate limiting, retry, caching, size cap |
> | `CallResult` / `ExecutionStatus` | `src/bioagent/adapters/base.py`, `status.py` | Unified return value and status (§8) |
> | `ProvenanceLog` | `src/bioagent/core/provenance.py` | Call provenance and replay (§10) |
> | `PolicyKernel` / `LicensePolicy` | `src/bioagent/policy.py` | Licence and network host admission (§2) |
> | `EvidenceTier` / TCM entity model | `src/bioagent/tcm/model.py` | Unified entities and evidence tiers (§6, §7) |

<!-- zh -->
> 适用范围：TCMSP、HERB、SymMap、ETCM、BATMAN-TCM、TCMBank、HIT、TCMID、NPASS、CMAUP
> 等中医药/天然产物数据库，以及其它**没有正式 API、需要通过分析网页前端请求来封装**的公开科研数据库。
>
> 本规范不另起炉灶，而是在仓库已有的机制上扩展：
>
> | 已有机制 | 位置 | 本规范中的角色 |
> | --- | --- | --- |
> | `Operation` / `PublicSource` | `src/bioagent/providers/public_apis.py` | 声明式请求模板 → 扩展为 §4 的 Source Manifest |
> | `AcquisitionSpec` | `src/bioagent/acquisition/sources.py` | 批量下载通道（§3 的 L1） |
> | `HTTPBackend` | `src/bioagent/backends/http.py` | 统一传输层：限速、重试、缓存、体积上限 |
> | `CallResult` / `ExecutionStatus` | `src/bioagent/adapters/base.py`、`status.py` | 统一返回与状态（§8） |
> | `ProvenanceLog` | `src/bioagent/core/provenance.py` | 调用溯源与重放（§10） |
> | `PolicyKernel` / `LicensePolicy` | `src/bioagent/policy.py` | 许可证与网络主机准入（§2） |
> | `EvidenceTier` / TCM 实体模型 | `src/bioagent/tcm/model.py` | 统一实体与证据分级（§6、§7） |

---

## 0. Design goals · 0. 设计目标

1. **One interface, many databases**: upper-layer analysis code calls only `search / get / related / export` and does not care which database the data came from, or whether the source is a JSON API or an HTML page.
2. **Declaration first, code as the fallback**: every request that can be described by a manifest is expressed declaratively; code is written only for parsing logic (a parser).
3. **Traceable and replayable results**: every record carries its source database, version, retrieval time and raw-response hash.
4. **No silent merging of evidence**: predictive data (such as TCMSP OB/DL filtering and target prediction) and experimental data (such as NPASS activity values) must carry different labels and must not be conflated.
5. **Upstream-friendly and compliant**: rate-limit, cache, respect the terms of service, and when an access attempt fails, honestly report `UNAVAILABLE` rather than fabricating success.

<!-- zh -->
1. **一个接口，多个库**：上层分析代码只调用 `search / get / related / export`，不关心数据来自哪个库、是 JSON API 还是 HTML 页面。
2. **声明优先，代码兜底**：能用 manifest 描述的请求一律声明式；只有解析逻辑才写代码（parser）。
3. **结果可溯源、可重放**：每条记录都带来源库、版本、抓取时间、原始响应哈希。
4. **不静默合并证据**：预测型（如 TCMSP 的 OB/DL 筛选、靶点预测）与实验型（如 NPASS 活性值）数据必须带不同标签，不能混为一谈。
5. **对上游友好且合规**：限速、缓存、尊重服务条款，接入失败时如实报告 `UNAVAILABLE`，而不是伪造成功。

---

## 1. Layered architecture · 1. 分层架构

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Analysis layer       network pharmacology / enrichment / KG / agent tools            │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Unified query layer  TCMDataHub.search/get/related/export (§5)                       │  ← the only public interface
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Normalization layer  ID mapping, names, units, evidence labels (§6 §7)               │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Connector layer      SourceConnector (one manifest + parser per source) (§4)         │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Transport layer      HTTPBackend: rate limit, retry, cache, size cap (existing)      │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Governance layer     PolicyKernel host/licence admission + ProvenanceLog (existing)  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

<!-- zh -->
```
┌──────────────────────────────────────────────────────────────┐
│ 分析层  network pharmacology / 富集 / 知识图谱 / Agent 工具   │
├──────────────────────────────────────────────────────────────┤
│ 统一查询层  TCMDataHub.search/get/related/export  (§5)        │  ← 唯一对外接口
├──────────────────────────────────────────────────────────────┤
│ 归一化层   Normalizer: ID 映射、名称、单位、证据标签 (§6 §7)   │
├──────────────────────────────────────────────────────────────┤
│ 连接器层   SourceConnector（每个库一个 manifest + parser）(§4) │
├──────────────────────────────────────────────────────────────┤
│ 传输层     HTTPBackend: 限速/重试/缓存/体积上限 (已有)         │
├──────────────────────────────────────────────────────────────┤
│ 治理层     PolicyKernel 主机与许可证准入 + ProvenanceLog (已有) │
└──────────────────────────────────────────────────────────────┘
```

Rule: **upper layers are prohibited from importing a concrete connector directly**; routing goes only through the unified query layer via the `source=` parameter.

<!-- zh -->
规则：**上层禁止直接 import 某个具体连接器**，只能通过统一查询层按 `source=` 参数路由。

---

## 2. Compliance boundary (this gate must be passed before any code is written) · 2. 合规边界（必须先过此关再写代码）

In this specification, "reverse encapsulation" means only this: **analysing the requests that a database's public web pages already issue in the browser (XHR/fetch, form submissions, download links), and organising them into a stable programmatic interface**.

<!-- zh -->
"逆向封装"在本规范中仅指：**分析数据库公开网页在浏览器中本来就会发出的请求（XHR/fetch、表单提交、下载链接），并把它们整理为稳定的程序接口**。

### 2.1 Allowed · 2.1 允许

- Calling the login-free JSON/HTML endpoints that the public page front end itself uses;
- Using the bulk download files offered by the official site (preferred over page-by-page scraping);
- Storing responses in a local cache so that they can be replayed and the number of requests reduced;
- Using officially provided API keys / academic accounts (credentials go in environment variables, never into the repository).

<!-- zh -->
- 调用公开页面前端本身使用的、无需登录的 JSON/HTML 端点；
- 使用官网提供的批量下载文件（优先于逐页抓取）；
- 在自有缓存中保存响应以便重放与减少请求量；
- 使用官方提供的 API Key / 学术账号（凭据放环境变量，不入库）。

### 2.2 Prohibited · 2.2 禁止

- Bypassing CAPTCHAs, logins, paywalls, IP blocks or any other access control;
- Forging a UA to impersonate a browser in order to evade anti-scraping measures, or rotating IPs through a proxy pool;
- Violating `robots.txt` or any clause of the terms of service that explicitly prohibits automated access;
- High-concurrency full-database scraping (all bulk requirements must go through L1 bulk download, or by contacting the maintainers);
- Redistributing the upstream data itself from this repository (only the **acquisition method** is distributed; the data lands in the user's local `data/`).

<!-- zh -->
- 绕过验证码、登录、付费墙、IP 封禁或任何访问控制；
- 伪造 UA 冒充浏览器以规避反爬策略、使用代理池轮换 IP；
- 违反 `robots.txt` 或服务条款中明确禁止自动访问的条款；
- 高并发全库拖取（全量需求一律走 L1 批量下载或联系维护方）；
- 在本仓库中再分发上游数据本体（只分发**获取方法**，数据落在用户本地 `data/`）。

### 2.3 Must be recorded before onboarding (written into the manifest's `compliance` section) · 2.3 接入前必须记录（写入 manifest 的 `compliance` 段）

| Field | Description |
| --- | --- |
| `terms_url` | Address of the terms of service / usage statement |
| `license` | SPDX identifier or a summary of the original text (e.g. `Free for academic use`) |
| `commercial_use` | `allowed` / `forbidden` / `unknown` |
| `robots_checked_at` | Date and conclusion of the robots.txt check |
| `citation` | The reference (DOI) that this database requires to be cited |
| `contact` | Maintainer contact details (used to request bulk data) |

<!-- zh -->
| 字段 | 说明 |
| --- | --- |
| `terms_url` | 服务条款/使用声明地址 |
| `license` | SPDX 或原文摘要（如 `Free for academic use`） |
| `commercial_use` | `allowed` / `forbidden` / `unknown` |
| `robots_checked_at` | 检查 robots.txt 的日期与结论 |
| `citation` | 该库要求的引用文献（DOI） |
| `contact` | 维护方联系方式（用于申请批量数据） |

`license` is read by `LicensePolicy`; `hosts` is validated by `PermissionProfile.check_network`, and **any host not listed in the manifest is denied outright**.

<!-- zh -->
`license` 会被 `LicensePolicy` 读取；`hosts` 会被 `PermissionProfile.check_network` 校验，**未列入 manifest 的主机一律拒绝**。

---

## 3. Access tier · 3. 访问通道分级（Access Tier）

Every operation must declare a `tier`; when the same data can be obtained through several channels, the unified query layer **selects the best in L0 → L3 order**.

<!-- zh -->
每个 operation 必须声明 `tier`，统一查询层在同一数据可由多通道获取时**按 L0 → L3 顺序择优**。

| Tier | Name | Typical form | Stability | Default rate limit |
| --- | --- | --- | --- | --- |
| **L0** | `official_api` | Documented REST/GraphQL | High | Per official documentation |
| **L1** | `bulk_download` | Official-site TSV/CSV/SDF/ZIP downloads | High (versioned) | Serial, one file at a time |
| **L2** | `web_json` | JSON returned by front-end XHR (undocumented) | Medium | ≤ 1 req/s |
| **L3** | `web_html` | Parsing of server-rendered HTML pages | Low | ≤ 0.5 req/s |

<!-- zh -->
| Tier | 名称 | 典型形态 | 稳定性 | 默认限速 |
| --- | --- | --- | --- | --- |
| **L0** | `official_api` | 有文档的 REST/GraphQL | 高 | 按官方文档 |
| **L1** | `bulk_download` | 官网 TSV/CSV/SDF/ZIP 下载 | 高（有版本） | 单文件串行 |
| **L2** | `web_json` | 前端 XHR 返回的 JSON（无文档） | 中 | ≤ 1 req/s |
| **L3** | `web_html` | 服务端渲染的 HTML 页面解析 | 低 | ≤ 0.5 req/s |

L2/L3 count as "reverse encapsulation" and must additionally fill in the `reverse` section of §4.3.

<!-- zh -->
L2/L3 属于"逆向封装"，必须额外填写 §4.3 的 `reverse` 段。

---

## 4. Source Manifest specification · 4. Source Manifest 规范

One manifest file per database: `src/bioagent/sources/tcm/<source_key>.yaml` (or an equivalent `PublicSource` Python declaration).

<!-- zh -->
每个数据库一个 manifest 文件：`src/bioagent/sources/tcm/<source_key>.yaml`（或等价的 `PublicSource` Python 声明）。

### 4.1 Top-level fields · 4.1 顶层字段

```yaml
key: herb                       # 全局唯一、小写、[a-z0-9_]，用作 ID 前缀
name: HERB
version: "2.0"                  # 上游数据版本；未知时写观测日期 "observed:2026-09-23"
domain: tcm                     # tcm | natural-products | pharmacology | ...
description: 中药-成分-靶点-疾病 高通量实验与文献整合数据库
base_url: http://herb.ac.cn
hosts: [herb.ac.cn]             # 允许访问的全部主机
rate_limit: {rps: 1.0, burst: 1, concurrency: 1}
timeout_s: 30
auth: {type: none}              # none | api_key(env: HERB_API_KEY) | cookie_session(禁止自动登录)
compliance: {...}               # 见 §2.3
entities: [herb, ingredient, target, disease, formula]   # 本库覆盖的统一实体类型（§6.1）
operations: [...]               # 见 §4.2
smoke: search_herb              # 冒烟测试使用的 operation
maintainer: "@github-handle"
```

### 4.2 Operation fields (compatible with the existing `Operation` dataclass; new items marked ★) · 4.2 Operation 字段（兼容现有 `Operation` dataclass，新增项标 ★）

```yaml
- name: search_herb
  verb: search                  # ★ 统一动词：search | get | related | list | export | health
  entity: herb                  # ★ 返回的统一实体类型
  tier: L2                      # ★ 访问通道
  description: 按名称检索中药
  method: GET
  path: /api/herb/search
  params: {keyword: "{query}", page: "{page}", size: "{page_size}"}
  args: [query]                 # 必填参数
  defaults: {page: 1, page_size: 50}   # ★
  accept: application/json
  pagination:                   # ★
    style: page                 # page | offset | cursor | none
    page_param: page
    size_param: size
    max_page_size: 100
    total_path: $.data.total    # JSONPath；无总数时省略
  response:                     # ★
    format: json                # json | html | tsv | csv | sdf
    records_path: $.data.list   # JSONPath（json）或 CSS 选择器（html）
    parser: herb.parse_herb     # ★ 可选：模块内解析函数名，缺省为通用 JSON 抽取
    schema_fingerprint: sha256:...   # ★ 见 §11，响应结构指纹
  field_map:                    # ★ 上游字段 → 统一字段
    Herb_ID: source_id
    Herb_cn_name: name_zh
    Herb_pinyin_name: name_pinyin
    Herb_latin_name: name_latin
  example: {query: 黄芪}
```

### 4.3 Additional information for reverse-engineered endpoints (required for L2/L3) · 4.3 逆向端点附加信息（L2/L3 必填）

```yaml
  reverse:
    discovered_via: devtools-network   # devtools-network | page-source | download-link
    observed_at: 2026-09-23
    page_url: http://herb.ac.cn/Detail/?v=HERB000001&label=Herb   # 触发该请求的页面
    required_headers: {Referer: "{base_url}/"}   # 只写前端本来就带的头
    fragility: medium                  # low | medium | high
    notes: 返回字段顺序不稳定，按键名取值
```

---

## 5. Unified query interface · 5. 统一查询接口

### 5.1 Python interface (which every connector must implement) · 5.1 Python 接口（所有连接器必须实现）

```python
class SourceConnector(Protocol):
    key: str                                   # manifest.key
    entities: frozenset[str]

    def search(self, entity: str, query: str, *, filters: Mapping[str, Any] | None = None,
               page: int = 1, page_size: int = 50) -> Page[Record]: ...

    def get(self, entity: str, source_id: str) -> Record: ...

    def related(self, entity: str, source_id: str, target_entity: str, *,
                predicate: str | None = None, page: int = 1,
                page_size: int = 200) -> Page[Edge]: ...

    def export(self, entity: str, *, since: str | None = None) -> Iterator[Record]: ...  # 优先 L1

    def health(self) -> CallResult: ...        # 跑 smoke operation
```

The unified entry point (the only object available to upper layers):

<!-- zh -->
统一入口（上层唯一可用的对象）：

```python
hub = TCMDataHub(connectors=[...], http=HTTPBackend(cache_dir=...), provenance=log)

hub.search("herb", "黄芪", sources=["herb", "tcmsp", "symmap"])     # 多源并查，结果按 xref 合并
hub.get("tcmsp:MOL000098")                                          # CURIE 自动路由
hub.related("herb:HERB002560", "ingredient")                        # 中药 → 成分
hub.related("tcmsp:MOL000098", "target", predicate="predicted_target")
hub.export("ingredient", source="npass")                            # 批量
```

### 5.2 Verb semantics · 5.2 动词语义

| Verb | Semantics | Idempotent | Cacheable |
| --- | --- | --- | --- |
| `search` | Fuzzy lookup by name/keyword, returning candidates; **it does not automatically settle on a single entity** | Yes | Yes (TTL 7 days) |
| `get` | Precise retrieval of one complete record by in-database ID | Yes | Yes (by data version) |
| `related` | Retrieval of one-hop relations (herb→ingredient, ingredient→target, target→disease …) | Yes | Yes |
| `list` | Enumeration of one class of entities (paginated) | Yes | Yes |
| `export` | Full/incremental export; **L1 download must be preferred** | Yes | Persisted to disk |
| `health` | Smoke test, checking upstream availability and schema drift | Yes | No |

<!-- zh -->
| 动词 | 语义 | 幂等 | 可缓存 |
| --- | --- | --- | --- |
| `search` | 按名称/关键词模糊查找，返回候选，**不自动选定唯一实体** | 是 | 是（TTL 7 天） |
| `get` | 按库内 ID 精确取一条完整记录 | 是 | 是（按数据版本） |
| `related` | 取一跳关系（中药→成分、成分→靶点、靶点→疾病…） | 是 | 是 |
| `list` | 枚举某类实体（分页） | 是 | 是 |
| `export` | 全量/增量导出，**必须优先 L1 下载** | 是 | 落盘 |
| `health` | 冒烟测试，检测上游可用性与结构漂移 | 是 | 否 |

When `search` returns several candidates it must preserve the ambiguity (consistent with `TCMKnowledgeBase.resolve`): for example "参" may be ginseng or Danshen, and the caller decides.

<!-- zh -->
`search` 返回多个候选时须保留歧义（与 `TCMKnowledgeBase.resolve` 一致）：例如"参"可能是人参或丹参，由调用方决定。

### 5.3 Unified response envelope · 5.3 统一响应信封

The `CallResult.value` returned by every method has the following structure (JSON-serialisable):

<!-- zh -->
所有方法返回的 `CallResult.value` 为如下结构（JSON 可序列化）：

```json
{
  "query": {"verb": "related", "entity": "ingredient", "source_id": "MOL000098",
            "target_entity": "target", "source": "tcmsp"},
  "records": [ { "...": "见 §6.2 Record / Edge" } ],
  "page": {"page": 1, "page_size": 200, "total": 154, "has_more": false, "cursor": null},
  "source": {"key": "tcmsp", "version": "2.3", "tier": "L2",
             "retrieved_at": "2026-09-23T08:12:03Z", "cached": true,
             "response_sha256": "sha256:4be1...", "request_url": "https://..."},
  "warnings": ["3 records dropped: missing target identifier"]
}
```

`CallResult.status` takes its value per §8; only `SUCCEEDED` / `DEGRADED` carry `records`.

<!-- zh -->
`CallResult.status` 按 §8 取值；只有 `SUCCEEDED` / `DEGRADED` 才会带 `records`。

---

## 6. Unified data model · 6. 统一数据模型

### 6.1 Entity types (aligned with `tcm/model.py`) · 6.1 实体类型（与 `tcm/model.py` 对齐）

| entity | Meaning | Preferred global identifier | Common alternatives |
| --- | --- | --- | --- |
| `herb` | Medicinal herb (source organism of a decoction piece) | Latin drug name + source species NCBI Taxon | Chinese name, pinyin, in-database ID |
| `processed_herb` | Processed product | Parent herb + processing method | — |
| `formula` | Formula | Formula name + source text | In-database ID |
| `ingredient` | Chemical constituent | **InChIKey** | PubChem CID, ChEMBL, CAS, SMILES |
| `target` | Target protein/gene | **UniProt** accession | HGNC Symbol, Entrez, Ensembl |
| `disease` | Disease | MONDO / ICD-11 | MeSH, UMLS CUI, DOID |
| `syndrome` | Syndrome pattern | Standard TCM syndrome-pattern name (GB/T 16751.2) | In-database ID |
| `symptom` | Symptom (TCM / modern) | SymMap/TCM standard symptom name | HPO, MeSH |
| `pathway` | Pathway | Reactome / KEGG | WikiPathways |
| `literature` | Literature | PMID / DOI | CNKI number |

<!-- zh -->
| entity | 含义 | 首选全局标识 | 常见备选 |
| --- | --- | --- | --- |
| `herb` | 中药材（饮片基原） | 拉丁药材名 + 基原物种 NCBI Taxon | 中文名、拼音、库内 ID |
| `processed_herb` | 炮制品 | 父 herb + 炮制方法 | — |
| `formula` | 方剂 | 方名 + 出处 | 库内 ID |
| `ingredient` | 化学成分 | **InChIKey** | PubChem CID、ChEMBL、CAS、SMILES |
| `target` | 靶点蛋白/基因 | **UniProt** 登录号 | HGNC Symbol、Entrez、Ensembl |
| `disease` | 疾病 | MONDO / ICD-11 | MeSH、UMLS CUI、DOID |
| `syndrome` | 证候 | 中医证候规范名（GB/T 16751.2） | 库内 ID |
| `symptom` | 症状（中医/现代） | SymMap/TCM 症状规范名 | HPO、MeSH |
| `pathway` | 通路 | Reactome / KEGG | WikiPathways |
| `literature` | 文献 | PMID / DOI | CNKI 号 |

### 6.2 Record and Edge · 6.2 Record 与 Edge

```python
@dataclass(frozen=True)
class Record:
    id: str                          # CURIE: "<source_key>:<source_id>"，如 "tcmsp:MOL000098"
    entity: str                      # §6.1
    source: str                      # manifest.key
    source_id: str
    name: str                        # 首选名（中文实体用中文名）
    names: Mapping[str, tuple[str, ...]]   # {"zh": ..., "pinyin": ..., "latin": ..., "en": ..., "synonym": ...}
    xrefs: Mapping[str, tuple[str, ...]]   # {"inchikey": ("...",), "pubchem": ("5280343",), "uniprot": (...)}
    attributes: Mapping[str, Any]    # 归一化后的属性（单位统一，见 §7.3）
    raw: Mapping[str, Any]           # 上游原始字段（未改名），便于审计
    retrieved_at: str                # ISO-8601 UTC
    source_version: str

@dataclass(frozen=True)
class Edge:
    subject: str                     # CURIE
    predicate: str                   # §6.3
    object: str                      # CURIE
    source: str
    evidence_kind: str               # §7.1
    evidence_tier: int               # tcm.model.EvidenceTier 数值
    score: float | None = None       # 上游给出的分数（原样保留）
    score_name: str | None = None    # 如 "OB", "DL", "HERB_score", "combined_score"
    references: tuple[str, ...] = () # PMID / DOI
    attributes: Mapping[str, Any] = field(default_factory=dict)  # 如 IC50、测定体系
```

**ID rules**:

- Outward-facing identifiers are always CURIEs `<key>:<source_id>`, where `key` comes from the manifest; external standard databases use fixed prefixes: `inchikey:`, `pubchem:`, `uniprot:`, `hgnc:`, `ncbitaxon:`, `mondo:`, `mesh:`, `pmid:`, `doi:`.
- A connector **must not** rewrite upstream IDs (including case and leading zeros).
- Cross-database merging happens only in the unified query layer and only on the basis of `xrefs`; after merging, the `Record` of every source is retained and none is overwritten.

<!-- zh -->
**ID 规则**：

- 对外一律使用 CURIE `<key>:<source_id>`，`key` 取自 manifest；外部标准库使用固定前缀：`inchikey:`、`pubchem:`、`uniprot:`、`hgnc:`、`ncbitaxon:`、`mondo:`、`mesh:`、`pmid:`、`doi:`。
- 连接器**不得**改写上游 ID（包括大小写和前导零）。
- 跨库合并只在统一查询层根据 `xrefs` 做，合并后保留每个来源的 `Record`，不覆盖。

### 6.3 Unified relationship predicates · 6.3 统一关系谓词

| subject → object | predicate | Description |
| --- | --- | --- |
| formula → herb | `has_component` | Attributes carry the sovereign–minister–assistant–guide `role` and the dose |
| herb → ingredient | `contains` | Attributes carry the content and the medicinal part |
| herb → processed_herb | `processed_as` | |
| ingredient → target | `binds_experimental` | Has an experimental activity value |
| ingredient → target | `predicted_target` | Computational prediction (similarity/docking/machine learning) |
| ingredient → target | `text_mined_target` | Literature mining |
| target → disease | `associated_with` | |
| target → pathway | `participates_in` | |
| herb / formula → syndrome | `treats_syndrome` | Usually from literature / classical records |
| herb / formula → disease | `indicated_for` | |
| herb / formula → symptom | `relieves_symptom` | |
| syndrome → symptom | `manifests_as` | |
| herb ↔ herb | `incompatible_with` | Eighteen incompatibilities, nineteen mutual antagonisms, etc.; wired to `check_compatibility` |

<!-- zh -->
| subject → object | predicate | 说明 |
| --- | --- | --- |
| formula → herb | `has_component` | 属性中带君臣佐使 `role`、剂量 |
| herb → ingredient | `contains` | 属性中带含量、药用部位 |
| herb → processed_herb | `processed_as` | |
| ingredient → target | `binds_experimental` | 有实验活性值 |
| ingredient → target | `predicted_target` | 计算预测（相似性/对接/机器学习） |
| ingredient → target | `text_mined_target` | 文献挖掘 |
| target → disease | `associated_with` | |
| target → pathway | `participates_in` | |
| herb / formula → syndrome | `treats_syndrome` | 通常为文献/经典记载 |
| herb / formula → disease | `indicated_for` | |
| herb / formula → symptom | `relieves_symptom` | |
| syndrome → symptom | `manifests_as` | |
| herb ↔ herb | `incompatible_with` | 十八反、十九畏等，对接 `check_compatibility` |

A new predicate may be used only after it has been registered in this table, so that connectors do not each invent their own naming scheme.

<!-- zh -->
新增谓词需在本表登记后才可使用，避免各连接器各起一套名字。

---

## 7. Normalization rules · 7. 归一化规则

### 7.1 Evidence labels (mandatory) · 7.1 证据标签（强制）

Every `Edge` must write `evidence_kind` and map it to an `EvidenceTier`:

<!-- zh -->
每条 `Edge` 必须写 `evidence_kind`，并映射到 `EvidenceTier`：

| evidence_kind | Typical source | EvidenceTier |
| --- | --- | --- |
| `classical_text` | Classical materia medica / formulary records | `CLASSICAL_TEXT` |
| `curated_textbook` | Pharmacopoeia, textbooks, expert curation | `EXPERT_EXPERIENCE` |
| `predicted` | TCMSP/BATMAN target prediction, molecular docking | `PRECLINICAL` |
| `text_mined` | Automatic literature extraction | `PRECLINICAL` |
| `experimental_invitro` / `experimental_invivo` | NPASS/ChEMBL activity, animal experiments | `PRECLINICAL` |
| `clinical` | Clinical research (requires `references`) | `CASE_REPORT`…`SYSTEMATIC_REVIEW` by study design |

<!-- zh -->
| evidence_kind | 典型来源 | EvidenceTier |
| --- | --- | --- |
| `classical_text` | 经典本草/方书记载 | `CLASSICAL_TEXT` |
| `curated_textbook` | 药典、教材、专家整理 | `EXPERT_EXPERIENCE` |
| `predicted` | TCMSP/BATMAN 靶点预测、分子对接 | `PRECLINICAL` |
| `text_mined` | 文献自动抽取 | `PRECLINICAL` |
| `experimental_invitro` / `experimental_invivo` | NPASS/ChEMBL 活性、动物实验 | `PRECLINICAL` |
| `clinical` | 临床研究（需 `references`） | 按研究设计取 `CASE_REPORT`…`SYSTEMATIC_REVIEW` |

When `EvidenceTier.needs_citation` is true, `references` must not be empty; otherwise that edge is downgraded to `DEGRADED` and a note is written into `warnings`.

<!-- zh -->
`EvidenceTier.needs_citation` 为真时 `references` 不得为空，否则该边降级为 `DEGRADED` 并写入 `warnings`。

### 7.2 Names · 7.2 名称

- Chinese names are compared after stripping whitespace and full-width/half-width differences (reusing `_norm` from `tcm/knowledge.py`);
- `zh / pinyin / latin / en` are all stored; anything missing is not invented;
- Medicinal materials are distinguished from their source species: for `黄芪`, `names.latin = "Astragali Radix"`, and the source species is written into `xrefs.ncbitaxon`;
- Homonyms with different referents (for example "白芍/赤芍", both from Paeonia) are not merged inside a connector.

<!-- zh -->
- 中文名去除空白与全角/半角差异后比较（复用 `tcm/knowledge.py` 的 `_norm`）；
- 同时保存 `zh / pinyin / latin / en`，缺失的不臆造；
- 药材与基原物种区分：`黄芪` 的 `names.latin = "Astragali Radix"`，基原物种写入 `xrefs.ncbitaxon`；
- 同名异物（如"白芍/赤芍"均来自芍药）不在连接器里合并。

### 7.3 Values and units · 7.3 数值与单位

- Activity values are converted uniformly to nM, retaining `activity_type` (IC50/EC50/Ki/Kd/MIC) and `relation` (`=`/`<`/`>`);
- Original values and original units are retained in `raw`;
- ADME parameters such as TCMSP OB (%), DL and Caco-2 enter `attributes` as-is; **the connector performs no threshold filtering** (e.g. OB≥30%, DL≥0.18). Filtering is a decision of the analysis layer and must be written out explicitly in the analysis code;
- Missing values are uniformly `None`; 0, `"-"` or `"N/A"` must not be used in their place.

<!-- zh -->
- 活性值统一换算到 nM，保留 `activity_type`（IC50/EC50/Ki/Kd/MIC）和 `relation`（`=`/`<`/`>`）；
- 原始值与原单位保留在 `raw`；
- TCMSP 的 OB（%）、DL、Caco-2 等 ADME 参数原样进入 `attributes`，**连接器不做阈值筛选**（如 OB≥30%、DL≥0.18），筛选是分析层的决定，须在分析代码中显式写出；
- 缺失值统一用 `None`，不得用 0、`"-"`、`"N/A"` 代替。

### 7.4 Time and encoding · 7.4 时间与编码

- Times are always ISO-8601 UTC;
- Text is uniformly UTF-8 and NFC-normalised; HTML entities are decoded before the data is written.

<!-- zh -->
- 时间一律 ISO-8601 UTC；
- 文本统一 UTF-8、NFC 规范化；HTML 实体解码后入库。

---

## 8. Error and status mapping · 8. 错误与状态映射

A connector does not throw bare exceptions at upper layers; it returns a `CallResult` uniformly, with the following status values:

<!-- zh -->
连接器不抛裸异常给上层，统一返回 `CallResult`，状态取值如下：

| Situation | ExecutionStatus | error content |
| --- | --- | --- |
| 2xx and parsing succeeds | `SUCCEEDED` | — |
| Some records fail to parse / missing citation / truncated | `DEGRADED` | Number of dropped records and the reason |
| Schema fingerprint disagrees with the manifest (upstream revision) | `DEGRADED` or `FAILED` | `schema_drift: <field differences>` |
| 404 / no such ID | `FAILED` | `not_found` |
| Other 4xx | `FAILED` | Status code + response summary |
| 429 / 5xx with retries exhausted | `UNAVAILABLE` | `upstream_unavailable` |
| DNS/connection failure/network denied by the sandbox | `UNAVAILABLE` | The specific cause |
| Timeout | `TIMEOUT` | Timeout in seconds |
| Host not on the allowlist / licence does not permit | `DENIED` | The `PolicyKernel` rule |
| Login required or a CAPTCHA appears | `DENIED` | `auth_required` (**must not attempt to bypass**) |

<!-- zh -->
| 情形 | ExecutionStatus | error 内容 |
| --- | --- | --- |
| 2xx 且解析成功 | `SUCCEEDED` | — |
| 部分记录解析失败 / 缺引用 / 截断 | `DEGRADED` | 丢弃条数与原因 |
| 结构指纹与 manifest 不符（上游改版） | `DEGRADED` 或 `FAILED` | `schema_drift: <字段差异>` |
| 404 / 无该 ID | `FAILED` | `not_found` |
| 其它 4xx | `FAILED` | 状态码 + 响应摘要 |
| 429 / 5xx 重试耗尽 | `UNAVAILABLE` | `upstream_unavailable` |
| DNS/连接失败/网络被沙箱拒绝 | `UNAVAILABLE` | 具体原因 |
| 超时 | `TIMEOUT` | 超时秒数 |
| 主机不在白名单 / 许可证不允许 | `DENIED` | `PolicyKernel` 的 rule |
| 需要登录或出现验证码 | `DENIED` | `auth_required`（**不得尝试绕过**） |

---

## 9. Pagination, rate limiting, caching · 9. 分页、限速、缓存

- **Pagination**: `page / page_size` are exposed uniformly to the outside; internally the connector converts from offset/cursor styles. When `page_size` exceeds `max_page_size` it is truncated automatically and the fact is explained in `warnings`.
- **Rate limiting**: the `rate_limit.rps` from the manifest is registered with the `HTTPBackend`'s `DEFAULT_RATES`; the L2 default is 1 req/s, the L3 default is 0.5 req/s, concurrency 1.
- **Retry**: only for 429/5xx/transient network breaks, with exponential backoff (1s, 2s, 4s), at most 3 times; `Retry-After` is respected.
- **Caching**: the `HTTPBackend`'s request-hash disk cache is reused; the cache key must include `source.version`, so it is invalidated automatically after an upstream upgrade. `search` has a TTL of 7 days; `get/related` are governed by the data version.
- **Size cap**: a single response defaults to 50 MB; anything larger returns `DEGRADED` with a prompt to use `export` (L1) instead.

<!-- zh -->
- **分页**：统一对外暴露 `page / page_size`；连接器内部把 offset/cursor 风格转换过来。`page_size` 超过 `max_page_size` 时自动截断并在 `warnings` 说明。
- **限速**：把 manifest 中的 `rate_limit.rps` 注册到 `HTTPBackend` 的 `DEFAULT_RATES`；L2 默认 1 req/s、L3 默认 0.5 req/s、并发 1。
- **重试**：仅对 429/5xx/网络瞬断，指数退避（1s, 2s, 4s），最多 3 次；尊重 `Retry-After`。
- **缓存**：沿用 `HTTPBackend` 的请求哈希磁盘缓存；缓存键需包含 `source.version`，上游升级后自动失效。`search` TTL 7 天，`get/related` 以数据版本为准。
- **体积上限**：单响应默认 50 MB，超出返回 `DEGRADED` 并提示改用 `export`（L1）。

---

## 10. Provenance · 10. 溯源（Provenance）

Every call writes one `ProvenanceEntry`; this specification additionally requires that `CallResult.metadata` carry:

<!-- zh -->
每次调用写一条 `ProvenanceEntry`，本规范额外要求在 `CallResult.metadata` 中携带：

```json
{"source_key": "tcmsp", "source_version": "2.3", "tier": "L2", "operation": "ingredient_targets",
 "request_url": "https://...", "http_status": 200, "cached": false,
 "response_sha256": "sha256:...", "schema_fingerprint": "sha256:...",
 "retrieved_at": "2026-09-23T08:12:03Z", "citation": "doi:10.1186/1758-2946-6-13"}
```

`response_sha256` lets `ProvenanceLog.replay()` determine whether a replayed result agrees with the original retrieval; when an analysis report cites a database result, the `citation` must be given alongside it.

<!-- zh -->
`response_sha256` 使 `ProvenanceLog.replay()` 能判断重放结果是否与原始抓取一致；分析报告引用数据库结果时，必须同时给出 `citation`。

---

## 11. Schema drift detection and testing · 11. 结构漂移检测与测试

Undocumented endpoints (L2/L3) break whenever the site is redesigned, so every connector must provide:

<!-- zh -->
无文档端点（L2/L3）会随网站改版失效，因此每个连接器必须提供：

1. **Schema fingerprint**: for a JSON response, a sorted hash of the "set of key paths + value types"; for HTML, a hash of the boolean vector of whether the key selectors matched. It is written into the manifest's `schema_fingerprint`, and any runtime mismatch raises `schema_drift`.
2. **Offline contract tests** (`tests/sources/test_<key>.py`): real responses recorded under `tests/fixtures/sources/<key>/*.json|html` (small in size, only a few records) are used to test the parser and the `field_map`; CI does not go online.
3. **Online smoke test**: `health()` runs the `smoke` operation, marked `@pytest.mark.network`, skipped by default, and executed manually at regular intervals by `scripts/verify_connectors.py`.
4. **Mandatory assertions**: IDs are CURIEs, `evidence_kind` is non-empty, `raw` is retained, missing values are `None`, and error paths return the correct `ExecutionStatus`.

<!-- zh -->
1. **结构指纹**：对 JSON 响应取"键路径集合 + 值类型"的排序哈希；对 HTML 取关键选择器是否命中的布尔向量哈希。写入 manifest 的 `schema_fingerprint`，运行时不一致即报 `schema_drift`。
2. **离线契约测试**（`tests/sources/test_<key>.py`）：用 `tests/fixtures/sources/<key>/*.json|html` 录制的真实响应（体积小、仅少量记录）测试 parser 与 `field_map`，CI 不联网。
3. **在线冒烟测试**：`health()` 跑 `smoke` 操作，标记为 `@pytest.mark.network`，默认跳过，由 `scripts/verify_connectors.py` 定期手动执行。
4. **必测断言**：ID 为 CURIE、`evidence_kind` 非空、`raw` 保留、缺失值为 `None`、错误路径返回正确的 `ExecutionStatus`。

---

## 12. Directory and naming conventions · 12. 目录与命名约定

```
src/bioagent/sources/
├── __init__.py
├── hub.py                 # TCMDataHub: routing, multi-source merge, CURIE resolution
├── connector.py           # SourceConnector protocol, Record/Edge/Page, generic JSON/HTML extraction
├── normalize.py           # name/unit/ID normalization, evidence label mapping
└── tcm/
    ├── tcmsp.yaml         # manifest
    ├── tcmsp.py           # parser functions only (parse_ingredient, parse_targets ...)
    ├── herb.yaml
    ├── herb.py
    └── ...
tests/sources/test_<key>.py
tests/fixtures/sources/<key>/...
```

<!-- zh -->
```
src/bioagent/sources/
├── __init__.py
├── hub.py                 # TCMDataHub：路由、多源合并、CURIE 解析
├── connector.py           # SourceConnector 协议、Record/Edge/Page、通用 JSON/HTML 抽取
├── normalize.py           # 名称/单位/ID 归一化、证据标签映射
└── tcm/
    ├── tcmsp.yaml         # manifest
    ├── tcmsp.py           # 仅放 parser 函数（parse_ingredient, parse_targets ...）
    ├── herb.yaml
    ├── herb.py
    └── ...
tests/sources/test_<key>.py
tests/fixtures/sources/<key>/...
```

- The manifest `key`, the file name and the CURIE prefix must all three agree;
- The parser function signature is fixed: `def parse_<entity>(payload: Any, *, op: Operation) -> list[dict]`; it performs only a pure "raw → unified fields" transformation and **issues no requests**;
- No direct `requests.get`/`urllib` call may appear in a connector; all network I/O goes through `HTTPBackend`.

<!-- zh -->
- manifest `key`、文件名、CURIE 前缀三者一致；
- parser 函数签名固定：`def parse_<entity>(payload: Any, *, op: Operation) -> list[dict]`，只做"原始 → 统一字段"的纯函数转换，**不发请求**；
- 连接器中不得出现 `requests.get`/`urllib` 直接调用，所有网络 I/O 走 `HTTPBackend`。

---

## 13. New database checklist · 13. 新增数据库检查清单

- [ ] Complete the §2.3 compliance record and confirm that no access control needs to be bypassed
- [ ] Where L0/L1 data is usable, do not use L2/L3
- [ ] The manifest is filled in completely: `hosts`, `rate_limit`, `entities`, `smoke`
- [ ] Every operation is annotated with `verb / entity / tier`; L2/L3 carry a `reverse` section
- [ ] `field_map` covers the mandatory fields of §6.2; IDs use CURIEs
- [ ] Every Edge has `evidence_kind` and `evidence_tier`
- [ ] Fixtures are recorded + offline contract tests pass
- [ ] The schema fingerprint is written into the manifest
- [ ] `health()` passes the online smoke test (or is honestly recorded as `UNAVAILABLE` with the reason)
- [ ] The database and its citation are registered in the README/data-source table

<!-- zh -->
- [ ] 完成 §2.3 合规记录，确认不需要绕过任何访问控制
- [ ] 能用 L0/L1 的数据不用 L2/L3
- [ ] manifest 填写完整：`hosts`、`rate_limit`、`entities`、`smoke`
- [ ] 每个 operation 标注 `verb / entity / tier`，L2/L3 带 `reverse` 段
- [ ] `field_map` 覆盖 §6.2 必填字段；ID 用 CURIE
- [ ] 每条 Edge 有 `evidence_kind` 与 `evidence_tier`
- [ ] 录制 fixtures + 离线契约测试通过
- [ ] 结构指纹写入 manifest
- [ ] `health()` 在线冒烟通过（或如实记录为 `UNAVAILABLE` 及原因）
- [ ] README/数据来源表登记该库及引用文献

---

## Appendix A: example TCMSP-style connector manifest (L2) · 附录 A：TCMSP 风格连接器 manifest 示例（L2）

> The endpoint paths are format examples only; for an actual integration, the requests observed in the browser developer tools are authoritative, and `reverse.observed_at` must be filled in.

<!-- zh -->
> 端点路径仅为格式示例，实际接入时以浏览器开发者工具中观察到的请求为准，并填写 `reverse.observed_at`。

```yaml
key: tcmsp
name: TCMSP
version: "2.3"
domain: tcm
base_url: https://tcmsp-e.com
hosts: [tcmsp-e.com]
rate_limit: {rps: 0.5, burst: 1, concurrency: 1}
auth: {type: none}
compliance:
  terms_url: https://tcmsp-e.com
  license: "Free for academic use"
  commercial_use: unknown
  robots_checked_at: "<检查日期>: <结论>"
  citation: "doi:10.1186/1758-2946-6-13"
entities: [herb, ingredient, target, disease]
smoke: search_herb
operations:
  - name: search_herb
    verb: search
    entity: herb
    tier: L3
    path: /tcmspsearch.php
    params: {qs: herb_all_name, q: "{query}"}
    args: [query]
    response: {format: html, parser: tcmsp.parse_herb_search}
    field_map: {herb_cn_name: name_zh, herb_pinyin: name_pinyin, herb_en_name: name_en}
    reverse: {discovered_via: page-source, observed_at: 2026-09-23, fragility: high}
    example: {query: 黄芪}
  - name: herb_ingredients
    verb: related
    entity: ingredient
    tier: L3
    path: /tcmspsearch.php
    params: {qr: "{source_id}", qsr: herb_en_name}
    args: [source_id]
    response: {format: html, parser: tcmsp.parse_ingredients}
    field_map: {MOL_ID: source_id, molecule_name: name_en, ob: attributes.ob_percent, dl: attributes.dl}
    edge: {predicate: contains, evidence_kind: curated_textbook}
    reverse: {discovered_via: page-source, observed_at: 2026-09-23, fragility: high,
              notes: 数据以内联 JS 变量形式嵌在页面中}
  - name: ingredient_targets
    verb: related
    entity: target
    tier: L3
    path: /tcmspsearch.php
    params: {qr: "{source_id}", qsr: mol_id}
    args: [source_id]
    response: {format: html, parser: tcmsp.parse_targets}
    edge: {predicate: predicted_target, evidence_kind: predicted}
```

## Appendix B: connector implementation skeleton · 附录 B：连接器实现骨架

```python
# src/bioagent/sources/tcm/tcmsp.py —— 只放纯函数 parser
import json, re
from typing import Any

_GRID = re.compile(r"data:\s*(\[\{.*?\}\])", re.S)

def parse_ingredients(payload: str, *, op) -> list[dict[str, Any]]:
    m = _GRID.search(payload)
    if not m:
        raise ValueError("schema_drift: ingredient grid not found")
    return json.loads(m.group(1))
```

```python
# src/bioagent/sources/connector.py —— 通用连接器（节选）
class ManifestConnector:
    def __init__(self, manifest: SourceManifest, http: HTTPBackend) -> None:
        self.m, self.http = manifest, http
        self.key, self.entities = manifest.key, frozenset(manifest.entities)

    def related(self, entity, source_id, target_entity, *, predicate=None, page=1, page_size=200):
        op = self.m.find(verb="related", entity=target_entity)
        req = op.render(source_id=source_id, page=page, page_size=page_size)
        status, payload, err, meta = self.http.request(self._to_http(req))
        if not status.successful:
            return self._fail(status, err, meta)
        rows = self._parse(op, payload)              # parser 或通用 records_path 抽取
        edges, dropped = self._to_edges(op, source_id, rows)
        return self._envelope(op, edges, meta, dropped)  # §5.3 信封 + §10 溯源元数据
```

---

*Version: v1 (2026-09-23). Any change to the entity types, predicates or status mappings in this specification must be accompanied by a corresponding update to `tcm/model.py` and its tests.*

<!-- zh -->
*版本：v1（2026-09-23）。修改本规范中的实体类型、谓词或状态映射时，须同步更新 `tcm/model.py` 与对应测试。*

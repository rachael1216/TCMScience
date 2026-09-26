# 第三方公开数据库统一接入接口规范（TCM-DB Connector Spec v1）

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

## 0. 设计目标

1. **一个接口，多个库**：上层分析代码只调用 `search / get / related / export`，不关心数据来自哪个库、是 JSON API 还是 HTML 页面。
2. **声明优先，代码兜底**：能用 manifest 描述的请求一律声明式；只有解析逻辑才写代码（parser）。
3. **结果可溯源、可重放**：每条记录都带来源库、版本、抓取时间、原始响应哈希。
4. **不静默合并证据**：预测型（如 TCMSP 的 OB/DL 筛选、靶点预测）与实验型（如 NPASS 活性值）数据必须带不同标签，不能混为一谈。
5. **对上游友好且合规**：限速、缓存、尊重服务条款，接入失败时如实报告 `UNAVAILABLE`，而不是伪造成功。

---

## 1. 分层架构

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

规则：**上层禁止直接 import 某个具体连接器**，只能通过统一查询层按 `source=` 参数路由。

---

## 2. 合规边界（必须先过此关再写代码）

"逆向封装"在本规范中仅指：**分析数据库公开网页在浏览器中本来就会发出的请求（XHR/fetch、表单提交、下载链接），并把它们整理为稳定的程序接口**。

### 2.1 允许

- 调用公开页面前端本身使用的、无需登录的 JSON/HTML 端点；
- 使用官网提供的批量下载文件（优先于逐页抓取）；
- 在自有缓存中保存响应以便重放与减少请求量；
- 使用官方提供的 API Key / 学术账号（凭据放环境变量，不入库）。

### 2.2 禁止

- 绕过验证码、登录、付费墙、IP 封禁或任何访问控制；
- 伪造 UA 冒充浏览器以规避反爬策略、使用代理池轮换 IP；
- 违反 `robots.txt` 或服务条款中明确禁止自动访问的条款；
- 高并发全库拖取（全量需求一律走 L1 批量下载或联系维护方）；
- 在本仓库中再分发上游数据本体（只分发**获取方法**，数据落在用户本地 `data/`）。

### 2.3 接入前必须记录（写入 manifest 的 `compliance` 段）

| 字段 | 说明 |
| --- | --- |
| `terms_url` | 服务条款/使用声明地址 |
| `license` | SPDX 或原文摘要（如 `Free for academic use`） |
| `commercial_use` | `allowed` / `forbidden` / `unknown` |
| `robots_checked_at` | 检查 robots.txt 的日期与结论 |
| `citation` | 该库要求的引用文献（DOI） |
| `contact` | 维护方联系方式（用于申请批量数据） |

`license` 会被 `LicensePolicy` 读取；`hosts` 会被 `PermissionProfile.check_network` 校验，**未列入 manifest 的主机一律拒绝**。

---

## 3. 访问通道分级（Access Tier）

每个 operation 必须声明 `tier`，统一查询层在同一数据可由多通道获取时**按 L0 → L3 顺序择优**。

| Tier | 名称 | 典型形态 | 稳定性 | 默认限速 |
| --- | --- | --- | --- | --- |
| **L0** | `official_api` | 有文档的 REST/GraphQL | 高 | 按官方文档 |
| **L1** | `bulk_download` | 官网 TSV/CSV/SDF/ZIP 下载 | 高（有版本） | 单文件串行 |
| **L2** | `web_json` | 前端 XHR 返回的 JSON（无文档） | 中 | ≤ 1 req/s |
| **L3** | `web_html` | 服务端渲染的 HTML 页面解析 | 低 | ≤ 0.5 req/s |

L2/L3 属于"逆向封装"，必须额外填写 §4.3 的 `reverse` 段。

---

## 4. Source Manifest 规范

每个数据库一个 manifest 文件：`src/bioagent/sources/tcm/<source_key>.yaml`（或等价的 `PublicSource` Python 声明）。

### 4.1 顶层字段

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

### 4.2 Operation 字段（兼容现有 `Operation` dataclass，新增项标 ★）

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

### 4.3 逆向端点附加信息（L2/L3 必填）

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

## 5. 统一查询接口

### 5.1 Python 接口（所有连接器必须实现）

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

统一入口（上层唯一可用的对象）：

```python
hub = TCMDataHub(connectors=[...], http=HTTPBackend(cache_dir=...), provenance=log)

hub.search("herb", "黄芪", sources=["herb", "tcmsp", "symmap"])     # 多源并查，结果按 xref 合并
hub.get("tcmsp:MOL000098")                                          # CURIE 自动路由
hub.related("herb:HERB002560", "ingredient")                        # 中药 → 成分
hub.related("tcmsp:MOL000098", "target", predicate="predicted_target")
hub.export("ingredient", source="npass")                            # 批量
```

### 5.2 动词语义

| 动词 | 语义 | 幂等 | 可缓存 |
| --- | --- | --- | --- |
| `search` | 按名称/关键词模糊查找，返回候选，**不自动选定唯一实体** | 是 | 是（TTL 7 天） |
| `get` | 按库内 ID 精确取一条完整记录 | 是 | 是（按数据版本） |
| `related` | 取一跳关系（中药→成分、成分→靶点、靶点→疾病…） | 是 | 是 |
| `list` | 枚举某类实体（分页） | 是 | 是 |
| `export` | 全量/增量导出，**必须优先 L1 下载** | 是 | 落盘 |
| `health` | 冒烟测试，检测上游可用性与结构漂移 | 是 | 否 |

`search` 返回多个候选时须保留歧义（与 `TCMKnowledgeBase.resolve` 一致）：例如"参"可能是人参或丹参，由调用方决定。

### 5.3 统一响应信封

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

`CallResult.status` 按 §8 取值；只有 `SUCCEEDED` / `DEGRADED` 才会带 `records`。

---

## 6. 统一数据模型

### 6.1 实体类型（与 `tcm/model.py` 对齐）

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

### 6.2 Record 与 Edge

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

**ID 规则**：

- 对外一律使用 CURIE `<key>:<source_id>`，`key` 取自 manifest；外部标准库使用固定前缀：`inchikey:`、`pubchem:`、`uniprot:`、`hgnc:`、`ncbitaxon:`、`mondo:`、`mesh:`、`pmid:`、`doi:`。
- 连接器**不得**改写上游 ID（包括大小写和前导零）。
- 跨库合并只在统一查询层根据 `xrefs` 做，合并后保留每个来源的 `Record`，不覆盖。

### 6.3 统一关系谓词

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

新增谓词需在本表登记后才可使用，避免各连接器各起一套名字。

---

## 7. 归一化规则

### 7.1 证据标签（强制）

每条 `Edge` 必须写 `evidence_kind`，并映射到 `EvidenceTier`：

| evidence_kind | 典型来源 | EvidenceTier |
| --- | --- | --- |
| `classical_text` | 经典本草/方书记载 | `CLASSICAL_TEXT` |
| `curated_textbook` | 药典、教材、专家整理 | `EXPERT_EXPERIENCE` |
| `predicted` | TCMSP/BATMAN 靶点预测、分子对接 | `PRECLINICAL` |
| `text_mined` | 文献自动抽取 | `PRECLINICAL` |
| `experimental_invitro` / `experimental_invivo` | NPASS/ChEMBL 活性、动物实验 | `PRECLINICAL` |
| `clinical` | 临床研究（需 `references`） | 按研究设计取 `CASE_REPORT`…`SYSTEMATIC_REVIEW` |

`EvidenceTier.needs_citation` 为真时 `references` 不得为空，否则该边降级为 `DEGRADED` 并写入 `warnings`。

### 7.2 名称

- 中文名去除空白与全角/半角差异后比较（复用 `tcm/knowledge.py` 的 `_norm`）；
- 同时保存 `zh / pinyin / latin / en`，缺失的不臆造；
- 药材与基原物种区分：`黄芪` 的 `names.latin = "Astragali Radix"`，基原物种写入 `xrefs.ncbitaxon`；
- 同名异物（如"白芍/赤芍"均来自芍药）不在连接器里合并。

### 7.3 数值与单位

- 活性值统一换算到 nM，保留 `activity_type`（IC50/EC50/Ki/Kd/MIC）和 `relation`（`=`/`<`/`>`）；
- 原始值与原单位保留在 `raw`；
- TCMSP 的 OB（%）、DL、Caco-2 等 ADME 参数原样进入 `attributes`，**连接器不做阈值筛选**（如 OB≥30%、DL≥0.18），筛选是分析层的决定，须在分析代码中显式写出；
- 缺失值统一用 `None`，不得用 0、`"-"`、`"N/A"` 代替。

### 7.4 时间与编码

- 时间一律 ISO-8601 UTC；
- 文本统一 UTF-8、NFC 规范化；HTML 实体解码后入库。

---

## 8. 错误与状态映射

连接器不抛裸异常给上层，统一返回 `CallResult`，状态取值如下：

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

## 9. 分页、限速、缓存

- **分页**：统一对外暴露 `page / page_size`；连接器内部把 offset/cursor 风格转换过来。`page_size` 超过 `max_page_size` 时自动截断并在 `warnings` 说明。
- **限速**：把 manifest 中的 `rate_limit.rps` 注册到 `HTTPBackend` 的 `DEFAULT_RATES`；L2 默认 1 req/s、L3 默认 0.5 req/s、并发 1。
- **重试**：仅对 429/5xx/网络瞬断，指数退避（1s, 2s, 4s），最多 3 次；尊重 `Retry-After`。
- **缓存**：沿用 `HTTPBackend` 的请求哈希磁盘缓存；缓存键需包含 `source.version`，上游升级后自动失效。`search` TTL 7 天，`get/related` 以数据版本为准。
- **体积上限**：单响应默认 50 MB，超出返回 `DEGRADED` 并提示改用 `export`（L1）。

---

## 10. 溯源（Provenance）

每次调用写一条 `ProvenanceEntry`，本规范额外要求在 `CallResult.metadata` 中携带：

```json
{"source_key": "tcmsp", "source_version": "2.3", "tier": "L2", "operation": "ingredient_targets",
 "request_url": "https://...", "http_status": 200, "cached": false,
 "response_sha256": "sha256:...", "schema_fingerprint": "sha256:...",
 "retrieved_at": "2026-09-23T08:12:03Z", "citation": "doi:10.1186/1758-2946-6-13"}
```

`response_sha256` 使 `ProvenanceLog.replay()` 能判断重放结果是否与原始抓取一致；分析报告引用数据库结果时，必须同时给出 `citation`。

---

## 11. 结构漂移检测与测试

无文档端点（L2/L3）会随网站改版失效，因此每个连接器必须提供：

1. **结构指纹**：对 JSON 响应取"键路径集合 + 值类型"的排序哈希；对 HTML 取关键选择器是否命中的布尔向量哈希。写入 manifest 的 `schema_fingerprint`，运行时不一致即报 `schema_drift`。
2. **离线契约测试**（`tests/sources/test_<key>.py`）：用 `tests/fixtures/sources/<key>/*.json|html` 录制的真实响应（体积小、仅少量记录）测试 parser 与 `field_map`，CI 不联网。
3. **在线冒烟测试**：`health()` 跑 `smoke` 操作，标记为 `@pytest.mark.network`，默认跳过，由 `scripts/verify_connectors.py` 定期手动执行。
4. **必测断言**：ID 为 CURIE、`evidence_kind` 非空、`raw` 保留、缺失值为 `None`、错误路径返回正确的 `ExecutionStatus`。

---

## 12. 目录与命名约定

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

- manifest `key`、文件名、CURIE 前缀三者一致；
- parser 函数签名固定：`def parse_<entity>(payload: Any, *, op: Operation) -> list[dict]`，只做"原始 → 统一字段"的纯函数转换，**不发请求**；
- 连接器中不得出现 `requests.get`/`urllib` 直接调用，所有网络 I/O 走 `HTTPBackend`。

---

## 13. 新增数据库检查清单

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

## 附录 A：TCMSP 风格连接器 manifest 示例（L2）

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

## 附录 B：连接器实现骨架

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

*版本：v1（2026-09-23）。修改本规范中的实体类型、谓词或状态映射时，须同步更新 `tcm/model.py` 与对应测试。*

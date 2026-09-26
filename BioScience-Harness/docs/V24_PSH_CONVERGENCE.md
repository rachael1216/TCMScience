# v2.4 — PSH ⊕ BioScience: the capability plane under the trusted kernel

The review that produced this repository ended with a shape: keep PSH's `TrustedKernel`
as the immutable base, keep BioScience's registry, resolver, backends, acquisition and
evolution as the capability plane, and write the agent runtime once, on top. PSH v0.6–v0.9
built that runtime. This release builds the seam between the two planes and makes the
capability plane worth admitting: 56 public biomedical and clinical sources, 146 typed
operations, every one executed live before it was listed.

## 1. The bridge (`bioagent.psh`)

One asymmetry, stated in the package docstring and enforced by a test: **`bioagent.psh`
depends on PSH; PSH never depends on it**, and nothing in `bioagent` imports
`psh.kernel` — the bridge receives a kernel object and calls its public surface.

A BioScience `ComponentManifest` says how a component runs. A PSH `ComponentManifest`
says what the gates need to rule. `bridge_manifest()` derives the second from the first,
with the conservative reading of every dimension:

| PSH dimension | derived from | rule |
| --- | --- | --- |
| `destinations` | `runtime.backend`, `permissions.network`, `permissions.filesystem_write` | an `http` connector reaches its declared hosts and nothing local; a local backend reaches `LOCAL_COMPUTE`, plus each host, plus `PERSISTENT` if it writes. A host is `PUBLIC_REMOTE` unless the operator's `HostPolicy` names it trusted. |
| `max_label` | the destinations | the lowest ceiling among them, capped by the operator's local ceiling — a PubMed query accepts at most `RESEARCH_DEIDENTIFIED`, so PHI in a query is refused by the rule that refuses PHI to a public model |
| `risk_tier`, `mutates`, `min_autonomy` | subprocess / container / writes | consequential, mutating, `ACT_WITH_APPROVAL` when any applies; routine, read-only, `OBSERVE` otherwise |
| `license_spdx`, `integration_mode` | `license.spdx`, `license.integration_mode` | an exact alias table maps the free-text data licences this package ships onto SPDX ids the lattice knows (`Public-domain (US Gov)` → `US-Gov-Public-Domain`); anything else stays unlicensed, which the lattice permits for `native`/`federated` use and refuses for `vendor`. The raw string stays in provenance. |
| `allowed_hosts`, `requires_network` | `permissions.network` | as declared; the isolated executor opens exactly those |
| `provenance["description_sensitivity"]` | the description, classified at admission | a description this kernel did not write is text; the rendered manifest item carries its label |

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

**Two levels of retrieval.** `register_into()` puts one `HARNESS` manifest per BioScience
domain at the top of PSH's registry and the domain's capabilities beneath it. PSH's
resolver narrows to the domains a task needs before it ranks capabilities, so admitting
the 2,567-row catalogue costs the planner fifteen harness descriptions of context, not
2,567 lines. The demo shows "find the HGNC record and the UniProt entry for TP53"
narrowing to genomics, proteomics and literature and ranking UniProt first.

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

## 2. The kernel boundary (`bioagent.evolution.boundary`)

The review's one hard rule for self-evolution: an evolution agent may propose changes to
components, skills, prompts and planners and may **never** write the kernel. `KernelBoundary`
makes that structural. A proposal whose entrypoint resolves into `psh.kernel`, PSH's
policy, labels, contracts or licensing, BioScience's own policy kernel, this bridge or the
boundary itself — or whose source path or declared writes land there — is quarantined by
`EvolutionPipeline.submit()` as its `boundary` stage, before a smoke test or a benchmark
is spent on it, whatever it would have scored. The test proposes an "optimisation" that
points a component at `psh.kernel.output_gate` and asserts the smoke runner never ran.

## 3. The connector set: 16 → 58 sources, 45 → 153 operations, all verified live

`scripts/verify_connectors.py` renders every operation from its own example arguments,
sends it through the real `HTTPBackend` (rate limits, retries, size caps) and writes one
row per operation to `data/connector_live_verification.csv`. `tests/test_public_sources.py`
then asserts that every shipped operation has a `SUCCEEDED` row. A source is listed because
it answered, and the date on the row says when.

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

Wikidata is where the traditional-Chinese-medicine material that has no API of its own
actually lives: herb items, their source taxa, and — through LOTUS — the compounds
recorded in each taxon with InChIKeys that resolve in PubChem and ChEMBL. The service
throttles shared cloud addresses to one query a minute, and the declared rate says so
rather than letting the service refuse; an operator on a better-treated network raises it.

Two mechanical additions made the set expressible: an `Operation` may carry a JSON body
template (g:Profiler's POST API), substituted like `params`; and a GraphQL list variable
receives the typed single value, which GraphQL coerces (DGIdb, CIViC).

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

Every new host is allowlisted in the `biomedical-research` profile (the BioScience policy
kernel refuses undeclared hosts), and every host has a declared request rate.

## 3b. Native tools: 139 capabilities executable anywhere the harness runs

The census's central number was honest and uncomfortable: of 2,567 catalogued
capabilities, the executable ones on a machine without a Biomni checkout, a container
runtime or forty third-party imports were the datasets. `bioagent.tools` is the first
tranche of capability that is executable *everywhere*: pure Python, no dependencies,
deterministic, each a function of keyword arguments returning a JSON-serialisable dict,
each with an example that is its smoke test (`native:<name>` in the manifest;
`native_smoke_runner` serves the hot reloader and the evolution pipeline).

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

Through the bridge each is a `LOCAL_COMPUTE` component at the local ceiling: a clinical
calculator may be handed an identifiable payload because nothing leaves the machine, and
its result carries the PHI label onward. `test_a_phi_payload_runs_locally_and_is_refused_remotely`
is the label model in one test — the same payload reaches the calculator and never reaches
a public connector. The eleven toolkit domains are eleven harnesses in PSH's registry, so
"estimate kidney function from creatinine" ranks the CKD-EPI tool without the planner
having seen a hundred and thirty-nine manifests.

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

## 4. Tests

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

## 5. Honest state

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

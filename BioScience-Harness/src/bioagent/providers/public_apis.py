"""Public biomedical data sources as connector components.

Each source is a `connector` component with a base URL, the host it touches, its
data license, and a set of typed **operations** — small declarative request
templates (path, params, GraphQL) — plus one operation designated as the smoke
test. The HTTP backend executes them; the policy kernel checks the host; the
event log records every call. No source-specific Python code is needed to add a
new API: it is a manifest entry here.

`operations` use `{name}` placeholders substituted from the call's arguments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions,
                                 Provider, RuntimeSpec, Validation)
from .base import Provider as ProviderBase


@dataclass(frozen=True)
class Operation:
    """One typed request template against a source."""

    name: str
    description: str
    path: str = ""
    method: str = "GET"
    params: Mapping[str, Any] = field(default_factory=dict)
    graphql: str | None = None
    variables: Mapping[str, Any] = field(default_factory=dict)
    #: JSON request body template for POST endpoints that are not GraphQL. Substituted
    #: like ``params``; ``None`` means the request has no body.
    json_body: Any = None
    accept: str = "application/json"
    args: tuple[str, ...] = ()            # required argument names
    example: Mapping[str, Any] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> dict[str, Any]:
        missing = [a for a in self.args if a not in kwargs]
        if missing:
            raise ValueError(f"operation {self.name!r} requires {missing}")

        def sub(v: Any) -> Any:
            if isinstance(v, str):
                # A bare "{name}" placeholder is replaced by the TYPED value, so a
                # GraphQL Int! variable stays an int (the live test caught "5" being
                # sent as a string). Placeholders embedded in longer strings are
                # still substituted textually.
                whole = re.fullmatch(r"\{(\w+)\}", v)
                if whole and whole.group(1) in kwargs:
                    return kwargs[whole.group(1)]
                return re.sub(r"\{(\w+)\}", lambda m: str(kwargs.get(m.group(1), m.group(0))), v)
            if isinstance(v, dict):
                return {k: sub(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [sub(x) for x in v]
            return v

        out: dict[str, Any] = {"path": sub(self.path), "method": self.method,
                               "params": sub(dict(self.params)), "accept": self.accept}
        if self.graphql is not None:
            out["graphql"] = self.graphql
            out["variables"] = sub(dict(self.variables))
        if self.json_body is not None:
            out["json_body"] = sub(self.json_body)
        return out


@dataclass(frozen=True)
class PublicSource:
    key: str
    name: str
    base_url: str
    host: str
    license: str
    description: str
    domain: str
    operations: tuple[Operation, ...]
    smoke: str                              # operation name used as the smoke test
    docs: str = ""
    rate_note: str = ""

    def op(self, name: str) -> Operation:
        for o in self.operations:
            if o.name == name:
                return o
        raise KeyError(f"{self.key} has no operation {name!r}; have {[o.name for o in self.operations]}")


J = {"content-type": "application/json"}

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "ensembl", "Ensembl REST", "https://rest.ensembl.org", "rest.ensembl.org",
        "Apache-2.0", "Genes, transcripts, variants, sequences and cross-references across vertebrates.",
        "genomics", (
            Operation("gene_lookup", "Gene record by symbol", "lookup/symbol/{species}/{symbol}",
                      params={**J, "expand": 0}, args=("symbol",),
                      example={"species": "homo_sapiens", "symbol": "TP53"}),
            Operation("id_lookup", "Any Ensembl stable id", "lookup/id/{id}", params=J, args=("id",),
                      example={"id": "ENSG00000141510"}),
            Operation("sequence", "Sequence for a stable id", "sequence/id/{id}",
                      params={**J, "type": "{type}"}, args=("id",),
                      example={"id": "ENST00000269305", "type": "cdna"}),
            Operation("vep_hgvs", "Variant effect prediction for an HGVS string",
                      "vep/{species}/hgvs/{hgvs}", params=J, args=("hgvs",),
                      example={"species": "human", "hgvs": "ENST00000269305.9:c.215C>G"}),
            Operation("xrefs", "External references for a gene", "xrefs/id/{id}", params=J,
                      args=("id",), example={"id": "ENSG00000141510"}),
            Operation("ping", "Service health", "info/ping", params=J),
        ), smoke="gene_lookup", docs="https://rest.ensembl.org", rate_note="15 req/s"),

    PublicSource(
        "uniprot", "UniProt REST", "https://rest.uniprot.org", "rest.uniprot.org",
        "CC-BY-4.0", "Protein sequence and functional annotation.", "proteomics", (
            Operation("entry", "UniProtKB entry by accession", "uniprotkb/{accession}.json",
                      args=("accession",), example={"accession": "P04637"}),
            Operation("search", "Search UniProtKB", "uniprotkb/search",
                      params={"query": "{query}", "format": "json", "size": "{size}",
                              "fields": "accession,gene_names,protein_name,organism_name,length"},
                      args=("query",), example={"query": "gene:TP53 AND organism_id:9606", "size": 5}),
            Operation("fasta", "FASTA sequence", "uniprotkb/{accession}.fasta", accept="text/plain",
                      args=("accession",), example={"accession": "P04637"}),
        ), smoke="entry", docs="https://www.uniprot.org/help/api"),

    PublicSource(
        "ncbi_eutils", "NCBI E-utilities", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        "eutils.ncbi.nlm.nih.gov", "Public-domain (US Gov)",
        "Search and fetch across PubMed, Gene, Nucleotide, Protein, GEO, ClinVar, dbSNP.",
        "literature", (
            Operation("esearch", "Search a database", "esearch.fcgi",
                      params={"db": "{db}", "term": "{term}", "retmode": "json", "retmax": "{retmax}"},
                      args=("db", "term"), example={"db": "pubmed", "term": "TP53 AND cancer", "retmax": 5}),
            Operation("esummary", "Summaries for ids", "esummary.fcgi",
                      params={"db": "{db}", "id": "{id}", "retmode": "json"}, args=("db", "id"),
                      example={"db": "gene", "id": "7157"}),
            Operation("efetch", "Fetch full records", "efetch.fcgi",
                      params={"db": "{db}", "id": "{id}", "retmode": "{retmode}", "rettype": "{rettype}"},
                      accept="*/*", args=("db", "id"),
                      example={"db": "pubmed", "id": "31452104", "retmode": "xml", "rettype": "abstract"}),
            Operation("einfo", "Database list", "einfo.fcgi", params={"retmode": "json"}),
        ), smoke="esummary", docs="https://www.ncbi.nlm.nih.gov/books/NBK25501/",
        rate_note="3 req/s without API key"),

    PublicSource(
        "chembl", "ChEMBL", "https://www.ebi.ac.uk/chembl/api/data", "www.ebi.ac.uk",
        "CC-BY-SA-3.0", "Bioactive molecules, targets, assays and activities.", "chemistry", (
            Operation("molecule", "Molecule by ChEMBL id", "molecule/{chembl_id}.json",
                      args=("chembl_id",), example={"chembl_id": "CHEMBL25"}),
            Operation("target", "Target by ChEMBL id", "target/{chembl_id}.json",
                      args=("chembl_id",), example={"chembl_id": "CHEMBL2074"}),
            Operation("target_search", "Targets by gene symbol",
                      "target.json", params={"target_synonym__icontains": "{symbol}", "limit": "{limit}"},
                      args=("symbol",), example={"symbol": "TP53", "limit": 5}),
            Operation("activities", "Activities for a target", "activity.json",
                      params={"target_chembl_id": "{target}", "limit": "{limit}",
                              "standard_type": "{standard_type}"},
                      args=("target",), example={"target": "CHEMBL2074", "limit": 5, "standard_type": "IC50"}),
        ), smoke="target", docs="https://chembl.gitbook.io/chembl-interface-documentation/web-services"),

    PublicSource(
        "pubchem", "PubChem PUG-REST", "https://pubchem.ncbi.nlm.nih.gov/rest/pug",
        "pubchem.ncbi.nlm.nih.gov", "Public-domain (US Gov)", "Chemical structures and properties.",
        "chemistry", (
            Operation("properties_by_cid", "Properties for a CID",
                      "compound/cid/{cid}/property/{props}/JSON", args=("cid",),
                      example={"cid": 2244, "props": "MolecularFormula,MolecularWeight,IUPACName,CanonicalSMILES"}),
            Operation("cid_by_name", "Resolve a name to CIDs", "compound/name/{name}/cids/JSON",
                      args=("name",), example={"name": "aspirin"}),
            Operation("synonyms", "Synonyms for a CID", "compound/cid/{cid}/synonyms/JSON",
                      args=("cid",), example={"cid": 2244}),
        ), smoke="properties_by_cid", docs="https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest",
        rate_note="5 req/s"),

    PublicSource(
        "clinicaltrials", "ClinicalTrials.gov v2", "https://clinicaltrials.gov/api/v2",
        "clinicaltrials.gov", "Public-domain (US Gov)", "Registered clinical studies.", "clinical", (
            Operation("search", "Search studies", "studies",
                      params={"query.term": "{term}", "pageSize": "{page_size}",
                              "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition"},
                      args=("term",), example={"term": "TP53", "page_size": 5}),
            Operation("study", "One study by NCT id", "studies/{nct_id}", args=("nct_id",),
                      example={"nct_id": "NCT04383938"}),
        ), smoke="search", docs="https://clinicaltrials.gov/data-api/api"),

    PublicSource(
        "openfda", "openFDA", "https://api.fda.gov", "api.fda.gov",
        "Public-domain (US Gov)", "Drug labels, adverse events, recalls and device data.", "clinical", (
            Operation("drug_label", "Drug labels", "drug/label.json",
                      params={"search": "{search}", "limit": "{limit}"}, args=("search",),
                      example={"search": "openfda.generic_name:aspirin", "limit": 1}),
            Operation("drug_event", "Adverse event reports", "drug/event.json",
                      params={"search": "{search}", "limit": "{limit}"}, args=("search",),
                      example={"search": "patient.drug.medicinalproduct:aspirin", "limit": 1}),
        ), smoke="drug_label", docs="https://open.fda.gov/apis/", rate_note="240 req/min without key"),

    PublicSource(
        "string", "STRING", "https://string-db.org/api", "string-db.org",
        "CC-BY-4.0", "Protein–protein interaction networks.", "proteomics", (
            Operation("map_ids", "Map identifiers to STRING ids", "tsv/get_string_ids",
                      params={"identifiers": "{identifiers}", "species": "{species}", "limit": 1},
                      accept="text/tab-separated-values", args=("identifiers",),
                      example={"identifiers": "TP53", "species": 9606}),
            Operation("network", "Interaction partners", "tsv/network",
                      params={"identifiers": "{identifiers}", "species": "{species}",
                              "required_score": "{score}"},
                      accept="text/tab-separated-values", args=("identifiers",),
                      example={"identifiers": "TP53", "species": 9606, "score": 900}),
        ), smoke="map_ids", docs="https://string-db.org/help/api/", rate_note="1 req/s"),

    PublicSource(
        "kegg", "KEGG REST", "https://rest.kegg.jp", "rest.kegg.jp",
        "Academic use free; commercial requires license", "Pathways, genes, compounds, diseases.",
        "pathways", (
            Operation("get", "Fetch an entry", "get/{entry}", accept="text/plain", args=("entry",),
                      example={"entry": "hsa:7157"}),
            Operation("find", "Search a database", "find/{db}/{query}", accept="text/plain",
                      args=("db", "query"), example={"db": "genes", "query": "TP53"}),
            Operation("link", "Linked entries", "link/{target_db}/{entry}", accept="text/plain",
                      args=("target_db", "entry"), example={"target_db": "pathway", "entry": "hsa:7157"}),
            Operation("info", "Database info", "info/kegg", accept="text/plain"),
        ), smoke="get", docs="https://www.kegg.jp/kegg/rest/keggapi.html"),

    PublicSource(
        "reactome", "Reactome ContentService", "https://reactome.org/ContentService", "reactome.org",
        "CC-BY-4.0", "Curated pathways and reactions.", "pathways", (
            Operation("query", "Entity by stable id", "data/query/{id}", args=("id",),
                      example={"id": "R-HSA-69488"}),
            Operation("pathways_for_entity", "Pathways containing an entity",
                      "data/pathways/low/entity/{id}", args=("id",), example={"id": "R-HSA-69488"}),
            Operation("version", "Database version", "data/database/version", accept="text/plain"),
        ), smoke="query", docs="https://reactome.org/ContentService/"),

    PublicSource(
        "opentargets", "Open Targets Platform", "https://api.platform.opentargets.org/api/v4",
        "api.platform.opentargets.org", "CC0-1.0",
        "Target–disease associations, tractability and genetics evidence.", "drug-discovery", (
            Operation("target", "Target by Ensembl id", "graphql", method="POST",
                      graphql="query($id:String!){target(ensemblId:$id){id approvedSymbol approvedName biotype}}",
                      variables={"id": "{ensembl_id}"}, args=("ensembl_id",),
                      example={"ensembl_id": "ENSG00000141510"}),
            Operation("associated_diseases", "Top associated diseases for a target", "graphql",
                      method="POST",
                      graphql="query($id:String!,$n:Int!){target(ensemblId:$id){approvedSymbol "
                              "associatedDiseases(page:{index:0,size:$n}){count rows{score disease{id name}}}}}",
                      variables={"id": "{ensembl_id}", "n": "{n}"}, args=("ensembl_id",),
                      example={"ensembl_id": "ENSG00000141510", "n": 5}),
        ), smoke="target", docs="https://platform-docs.opentargets.org/data-access/graphql-api"),

    PublicSource(
        "rcsb", "RCSB PDB Data API", "https://data.rcsb.org/rest/v1", "data.rcsb.org",
        "CC0-1.0", "Macromolecular structure metadata.", "structural-biology", (
            Operation("entry", "PDB entry", "core/entry/{pdb_id}", args=("pdb_id",),
                      example={"pdb_id": "4HHB"}),
            Operation("polymer_entity", "Polymer entity", "core/polymer_entity/{pdb_id}/{entity_id}",
                      args=("pdb_id", "entity_id"), example={"pdb_id": "4HHB", "entity_id": 1}),
        ), smoke="entry", docs="https://data.rcsb.org/redoc/"),

    PublicSource(
        "europepmc", "Europe PMC", "https://www.ebi.ac.uk/europepmc/webservices/rest", "www.ebi.ac.uk",
        "CC-BY / mixed per article", "Literature search with open-access full text.", "literature", (
            Operation("search", "Search articles", "search",
                      params={"query": "{query}", "format": "json", "pageSize": "{page_size}"},
                      args=("query",), example={"query": "TP53 AND SRC:MED", "page_size": 5}),
            Operation("citations", "Citations of an article", "{source}/{id}/citations",
                      params={"format": "json", "pageSize": "{page_size}"}, args=("source", "id"),
                      example={"source": "MED", "id": "31452104", "page_size": 5}),
        ), smoke="search", docs="https://europepmc.org/RestfulWebService"),

    PublicSource(
        "gnomad", "gnomAD GraphQL", "https://gnomad.broadinstitute.org/api", "gnomad.broadinstitute.org",
        "CC0-1.0 (data) / CC-BY-4.0", "Population allele frequencies and gene constraint.", "genomics", (
            Operation("gene_constraint", "Constraint metrics for a gene", "", method="POST",
                      graphql="query($sym:String!){gene(gene_symbol:$sym,reference_genome:GRCh38){gene_id symbol "
                              "gnomad_constraint{pLI oe_lof oe_lof_upper oe_mis}}}",
                      variables={"sym": "{symbol}"}, args=("symbol",), example={"symbol": "TP53"}),
            Operation("variant", "Variant by id", "", method="POST",
                      graphql="query($v:String!){variant(variantId:$v,dataset:gnomad_r4){variant_id rsids "
                              "genome{ac an af}}}",
                      variables={"v": "{variant_id}"}, args=("variant_id",),
                      example={"variant_id": "17-7675088-C-T"}),
        ), smoke="gene_constraint", docs="https://gnomad.broadinstitute.org/api", rate_note="~1 req/s"),

    PublicSource(
        "mygene", "MyGene.info", "https://mygene.info/v3", "mygene.info",
        "Apache-2.0", "Aggregated gene annotation across species.", "genomics", (
            Operation("query", "Search genes", "query", params={"q": "{q}", "species": "{species}",
                      "size": "{size}", "fields": "symbol,name,entrezgene,ensembl.gene"},
                      args=("q",), example={"q": "symbol:TP53", "species": "human", "size": 5}),
            Operation("gene", "Gene by id", "gene/{id}", params={"fields": "symbol,name,summary,go"},
                      args=("id",), example={"id": "7157"}),
        ), smoke="gene", docs="https://docs.mygene.info/"),

    PublicSource(
        "myvariant", "MyVariant.info", "https://myvariant.info/v1", "myvariant.info",
        "Apache-2.0", "Aggregated variant annotation (ClinVar, dbSNP, gnomAD, CADD, ...).", "genomics", (
            # MyVariant defaults to hg19; declare the assembly so GRCh38 HGVS resolves.
            Operation("variant", "Variant by HGVS id (assembly hg19|hg38)", "variant/{hgvs}",
                      params={"assembly": "{assembly}",
                              "fields": "clinvar.rcv.clinical_significance,dbsnp.rsid,cadd.phred,gnomad_exome.af"},
                      args=("hgvs",), example={"hgvs": "chr17:g.7675088C>T", "assembly": "hg38"}),
            Operation("query", "Search variants", "query", params={"q": "{q}", "size": "{size}"},
                      args=("q",), example={"q": "dbsnp.rsid:rs28934578", "size": 5}),
        ), smoke="variant", docs="https://docs.myvariant.info/"),
)

#: The sixteen sources above were the v2.1 set. The extended set — structures, expression,
#: cancer genomics, pharmacogenomics, clinical terminology, literature graphs, ontologies
#: and enrichment — lives in its own module so this one stays readable; both are one
#: registry here and every operation of both is verified live by
#: ``scripts/verify_connectors.py``.
from .public_apis_ext import EXTENDED_SOURCES  # noqa: E402

CORE_SOURCES: tuple[PublicSource, ...] = SOURCES
SOURCES = CORE_SOURCES + EXTENDED_SOURCES

BY_KEY: Mapping[str, PublicSource] = {s.key: s for s in SOURCES}
if len(BY_KEY) != len(SOURCES):                       # pragma: no cover - programming error
    raise RuntimeError("duplicate public source keys")


class PublicAPIProvider(ProviderBase):
    """Yields one connector component per public source."""

    name = "public-apis"

    def discover(self) -> Iterator[ComponentManifest]:
        for s in SOURCES:
            yield ComponentManifest(
                id=f"public.connector.{s.key}", kind="connector", name=s.name,
                version="2024.1", description=s.description, domain=s.domain,
                provider=Provider(project="public-apis", repo=s.docs),
                runtime=RuntimeSpec(backend="http", server=s.base_url, deterministic=False),
                inputs={"operations": [o.name for o in s.operations]},
                permissions=Permissions(network=(s.host,)),
                license=LicenseSpec(spdx=s.license, integration_mode="native",
                                    note=s.rate_note),
                validation=Validation(smoke_test=s.smoke),
                offline_capable=False,
            )


def render_call(source_key: str, operation: str, **kwargs: Any) -> dict[str, Any]:
    """Arguments for HTTPBackend.invoke for a named operation."""
    src = BY_KEY[source_key]
    op = src.op(operation)
    merged = {**op.example, **kwargs} if not kwargs else {**{k: v for k, v in op.example.items()
                                                                 if k not in op.args}, **kwargs}
    return op.render(**merged)

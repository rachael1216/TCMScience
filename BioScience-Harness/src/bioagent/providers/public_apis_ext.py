"""The extended public-source set: structures, expression, cancer genomics, pharmacology,
clinical terminology, literature graphs, ontologies and enrichment.

Same shape as ``public_apis.py``: one ``PublicSource`` per service, typed ``Operation``
templates, one smoke test each, no source-specific code. Every operation here is executed
live by ``scripts/verify_connectors.py`` and the outcome recorded in
``data/connector_live_verification.csv`` — a source is listed because it answered, not
because its documentation says it should.

Three families are deliberately absent. Services that need a credential (UMLS, OMIM,
DisGeNET, BioGRID, Orphanet, SNOMED CT Snowstorm) are not "public" in the sense this
module means, and a connector that silently fails without a key would report UNAVAILABLE
for a reason the operator did not choose. Services that did not answer the verification
run are not shipped whatever their documentation promises: Pathway Commons timed out,
PharmGKB was unreachable from the verification network, and Semantic Scholar throttles
unauthenticated shared addresses. And traditional-Chinese-medicine resources (TCMSP,
HERB, SymMap, BATMAN-TCM, ETCM) publish downloadable tables rather than stable JSON APIs;
they belong to the acquisition layer as datasets, not here as connectors.
"""

from __future__ import annotations

from .public_apis import Operation, PublicSource

J = {"content-type": "application/json"}

EXTENDED_SOURCES: tuple[PublicSource, ...] = (
    # ------------------------------------------------------------ genes / variants
    PublicSource(
        "hgnc", "HGNC REST", "https://rest.genenames.org", "rest.genenames.org", "CC0-1.0",
        "Approved human gene symbols, names, locus types and cross-references.", "genomics", (
            Operation("symbol", "Gene by approved symbol", "fetch/symbol/{symbol}",
                      args=("symbol",), example={"symbol": "TP53"}),
            Operation("hgnc_id", "Gene by HGNC id", "fetch/hgnc_id/{hgnc_id}",
                      args=("hgnc_id",), example={"hgnc_id": "11998"}),
            Operation("search", "Search symbols, names and aliases", "search/{query}",
                      args=("query",), example={"query": "BRCA*"}),
        ), smoke="symbol", docs="https://www.genenames.org/help/rest/", rate_note="10 req/s"),

    PublicSource(
        "ncbi_datasets", "NCBI Datasets v2", "https://api.ncbi.nlm.nih.gov/datasets/v2",
        "api.ncbi.nlm.nih.gov", "Public-domain (US Gov)",
        "Gene, genome-assembly and taxonomy reports.", "genomics", (
            Operation("gene_by_symbol", "Gene report by symbol and taxon",
                      "gene/symbol/{symbol}/taxon/{taxon}", args=("symbol",),
                      example={"symbol": "TP53", "taxon": "human"}),
            Operation("gene_by_id", "Gene report by NCBI Gene id", "gene/id/{gene_id}",
                      args=("gene_id",), example={"gene_id": 7157}),
            Operation("taxonomy", "Taxonomy node", "taxonomy/taxon/{taxon}", args=("taxon",),
                      example={"taxon": 9606}),
            Operation("genome_report", "Genome assembly reports for a taxon",
                      "genome/taxon/{taxon}/dataset_report", params={"page_size": "{page_size}"},
                      args=("taxon",), example={"taxon": 9606, "page_size": 2}),
        ), smoke="gene_by_symbol", docs="https://www.ncbi.nlm.nih.gov/datasets/docs/v2/api/",
        rate_note="5 req/s without API key"),

    PublicSource(
        "gwas_catalog", "GWAS Catalog", "https://www.ebi.ac.uk/gwas/rest/api", "www.ebi.ac.uk",
        "EMBL-EBI terms of use (open)", "Published genome-wide association studies and hits.",
        "genomics", (
            Operation("snp", "SNP record", "singleNucleotidePolymorphisms/{rs_id}",
                      args=("rs_id",), example={"rs_id": "rs7412"}),
            Operation("associations_by_rsid", "Associations for a SNP",
                      "singleNucleotidePolymorphisms/{rs_id}/associations",
                      params={"projection": "associationBySnp"}, args=("rs_id",),
                      example={"rs_id": "rs7412"}),
            Operation("snps_by_gene", "SNPs with associations in a gene",
                      "singleNucleotidePolymorphisms/search/findByGene",
                      params={"geneName": "{gene}", "size": "{size}"}, args=("gene",),
                      example={"gene": "TP53", "size": 3}),
        ), smoke="snp", docs="https://www.ebi.ac.uk/gwas/rest/docs/api"),

    PublicSource(
        "ucsc", "UCSC Genome Browser API", "https://api.genome.ucsc.edu", "api.genome.ucsc.edu",
        "Public (UCSC terms)", "Reference sequences and annotation tracks for assemblies.",
        "genomics", (
            Operation("sequence", "DNA sequence for a region", "getData/sequence",
                      params={"genome": "{genome}", "chrom": "{chrom}", "start": "{start}",
                              "end": "{end}"},
                      args=("genome", "chrom", "start", "end"),
                      example={"genome": "hg38", "chrom": "chr17", "start": 7668402,
                               "end": 7668502}),
            Operation("track", "Track items for a region", "getData/track",
                      params={"genome": "{genome}", "track": "{track}", "chrom": "{chrom}",
                              "start": "{start}", "end": "{end}"},
                      args=("genome", "track", "chrom", "start", "end"),
                      example={"genome": "hg38", "track": "knownGene", "chrom": "chr17",
                               "start": 7668402, "end": 7687538}),
            Operation("genomes", "Available genomes", "list/ucscGenomes"),
            Operation("chromosomes", "Chromosomes of a genome", "list/chromosomes",
                      params={"genome": "{genome}"}, args=("genome",), example={"genome": "hg38"}),
        ), smoke="sequence", docs="https://genome.ucsc.edu/goldenPath/help/api.html"),

    # ------------------------------------------------------- proteins / structures
    PublicSource(
        "alphafold", "AlphaFold DB", "https://alphafold.ebi.ac.uk/api", "alphafold.ebi.ac.uk",
        "CC-BY-4.0", "Predicted protein structures with per-residue confidence.",
        "structural-biology", (
            Operation("prediction", "Prediction metadata and model file URLs",
                      "prediction/{accession}", args=("accession",), example={"accession": "P04637"}),
            Operation("uniprot_summary", "Summary of models available for an accession",
                      "uniprot/summary/{accession}.json", args=("accession",),
                      example={"accession": "P04637"}),
        ), smoke="prediction", docs="https://alphafold.ebi.ac.uk/api-docs"),

    PublicSource(
        "pdbe", "PDBe API", "https://www.ebi.ac.uk/pdbe/api", "www.ebi.ac.uk", "CC-BY-4.0",
        "PDB entry summaries, molecules and SIFTS UniProt mappings.", "structural-biology", (
            Operation("entry_summary", "Entry summary", "pdb/entry/summary/{pdb_id}",
                      args=("pdb_id",), example={"pdb_id": "4hhb"}),
            Operation("molecules", "Molecules in an entry", "pdb/entry/molecules/{pdb_id}",
                      args=("pdb_id",), example={"pdb_id": "4hhb"}),
            Operation("uniprot_mapping", "SIFTS UniProt mapping for an entry",
                      "mappings/uniprot/{pdb_id}", args=("pdb_id",), example={"pdb_id": "4hhb"}),
            Operation("best_structures", "Best structures for a UniProt accession",
                      "mappings/best_structures/{accession}", args=("accession",),
                      example={"accession": "P04637"}),
        ), smoke="entry_summary", docs="https://www.ebi.ac.uk/pdbe/api/doc/"),

    PublicSource(
        "interpro", "InterPro API", "https://www.ebi.ac.uk/interpro/api", "www.ebi.ac.uk",
        "CC0-1.0", "Protein families, domains and functional sites.", "proteomics", (
            Operation("entry", "InterPro entry", "entry/interpro/{ipr}", args=("ipr",),
                      example={"ipr": "IPR011009"}),
            Operation("protein_entries", "InterPro entries matched by a protein",
                      "entry/interpro/protein/uniprot/{accession}",
                      params={"page_size": "{page_size}"}, args=("accession",),
                      example={"accession": "P04637", "page_size": 5}),
            Operation("protein", "Protein record", "protein/uniprot/{accession}",
                      args=("accession",), example={"accession": "P04637"}),
        ), smoke="entry",
        docs="https://interpro-documentation.readthedocs.io/en/latest/interpro-api.html"),

    PublicSource(
        "quickgo", "QuickGO", "https://www.ebi.ac.uk/QuickGO/services", "www.ebi.ac.uk",
        "CC-BY-4.0", "Gene Ontology terms and GO annotations.", "ontology", (
            Operation("go_term", "GO term", "ontology/go/terms/{go_id}", args=("go_id",),
                      example={"go_id": "GO:0006915"}),
            Operation("go_search", "Search GO terms", "ontology/go/search",
                      params={"query": "{query}", "limit": "{limit}"}, args=("query",),
                      example={"query": "apoptosis", "limit": 5}),
            Operation("annotations", "Annotations for a gene product", "annotation/search",
                      params={"geneProductId": "{gene_product}", "limit": "{limit}"},
                      args=("gene_product",), example={"gene_product": "P04637", "limit": 5}),
        ), smoke="go_term", docs="https://www.ebi.ac.uk/QuickGO/api/index.html"),

    PublicSource(
        "pride", "PRIDE Archive", "https://www.ebi.ac.uk/pride/ws/archive/v2", "www.ebi.ac.uk",
        "CC0-1.0 (metadata)", "Proteomics datasets and their metadata.", "proteomics", (
            Operation("project", "Project by accession", "projects/{accession}",
                      args=("accession",), example={"accession": "PXD000001"}),
            Operation("search", "Search projects", "search/projects",
                      params={"keyword": "{keyword}", "pageSize": "{page_size}"},
                      args=("keyword",), example={"keyword": "glioblastoma", "page_size": 3}),
        ), smoke="project", docs="https://www.ebi.ac.uk/pride/ws/archive/v2/swagger-ui.html"),

    PublicSource(
        "proteinatlas", "Human Protein Atlas", "https://www.proteinatlas.org",
        "www.proteinatlas.org", "CC-BY-SA-3.0",
        "Tissue, single-cell and pathology expression of human proteins.", "expression", (
            Operation("gene", "Gene entry as JSON", "{ensembl_id}.json", args=("ensembl_id",),
                      example={"ensembl_id": "ENSG00000141510"}),
            Operation("search", "Search download API", "api/search_download.php",
                      params={"search": "{query}", "format": "json", "columns": "{columns}",
                              "compress": "no"},
                      args=("query",), example={"query": "TP53", "columns": "g,gs,eg,up,rnats"}),
        ), smoke="gene", docs="https://www.proteinatlas.org/about/help/dataaccess"),

    # -------------------------------------------------------- expression / omics
    PublicSource(
        "gtex", "GTEx Portal API", "https://gtexportal.org/api/v2", "gtexportal.org",
        "Open (GTEx terms)", "Tissue expression and expression QTLs.", "expression", (
            Operation("gene", "Gene reference record", "reference/gene",
                      params={"geneId": "{gene_id}"}, args=("gene_id",), example={"gene_id": "TP53"}),
            Operation("median_expression", "Median gene expression by tissue",
                      "expression/medianGeneExpression",
                      params={"gencodeId": "{gencode_id}", "datasetId": "{dataset}"},
                      args=("gencode_id",),
                      example={"gencode_id": "ENSG00000141510.18", "dataset": "gtex_v8"}),
        ), smoke="gene", docs="https://gtexportal.org/api/v2/redoc"),

    PublicSource(
        "encode", "ENCODE Portal", "https://www.encodeproject.org", "www.encodeproject.org",
        "CC-BY-4.0", "Functional genomics experiments, files and annotations.", "epigenomics", (
            Operation("search", "Search objects", "search/",
                      params={"type": "{type}", "searchTerm": "{term}", "format": "json",
                              "limit": "{limit}"},
                      args=("type", "term"),
                      example={"type": "Experiment", "term": "ATAC-seq K562", "limit": 2}),
            Operation("experiment", "Experiment by accession", "experiments/{accession}/",
                      params={"format": "json"}, args=("accession",),
                      example={"accession": "ENCSR000AKS"}),
        ), smoke="experiment", docs="https://www.encodeproject.org/help/rest-api/"),

    PublicSource(
        "ena", "ENA Portal API", "https://www.ebi.ac.uk/ena/portal/api", "www.ebi.ac.uk",
        "Open (INSDC)", "Sequencing runs, samples and studies with file locations.", "genomics", (
            Operation("search", "Search a result type", "search",
                      params={"result": "{result}", "query": "{query}", "fields": "{fields}",
                              "limit": "{limit}", "format": "json"},
                      args=("result", "query"),
                      example={"result": "read_run",
                               "query": 'tax_eq(9606) AND library_strategy="RNA-Seq"',
                               "fields": "run_accession,sample_accession,study_accession",
                               "limit": 2}),
            Operation("filereport", "File report for an accession", "filereport",
                      params={"accession": "{accession}", "result": "read_run",
                              "fields": "run_accession,fastq_ftp,fastq_md5", "format": "json"},
                      args=("accession",), example={"accession": "SRR000001"}),
        ), smoke="filereport", docs="https://www.ebi.ac.uk/ena/portal/api/doc"),

    PublicSource(
        "biostudies", "BioStudies", "https://www.ebi.ac.uk/biostudies/api/v1", "www.ebi.ac.uk",
        "Open (EMBL-EBI terms)", "Studies, including the ArrayExpress functional genomics collection.",
        "expression", (
            Operation("search", "Search studies", "search",
                      params={"query": "{query}", "pageSize": "{page_size}"}, args=("query",),
                      example={"query": "single cell lung", "page_size": 3}),
            Operation("study", "Study by accession", "studies/{accession}", args=("accession",),
                      example={"accession": "E-MTAB-5214"}),
        ), smoke="study", docs="https://www.ebi.ac.uk/biostudies/help"),

    PublicSource(
        "cellxgene", "CZ CELLxGENE Discover", "https://api.cellxgene.cziscience.com/curation/v1",
        "api.cellxgene.cziscience.com", "CC-BY-4.0", "Single-cell collections and datasets.",
        "single-cell", (
            Operation("collection", "Collection by id", "collections/{collection_id}",
                      args=("collection_id",),
                      example={"collection_id": "e5f58829-1a66-40b5-a624-9046778e74f5"}),
        ), smoke="collection", docs="https://api.cellxgene.cziscience.com/curation/ui/"),

    PublicSource(
        "metabolights", "MetaboLights", "https://www.ebi.ac.uk/metabolights/ws", "www.ebi.ac.uk",
        "CC0-1.0", "Metabolomics studies.", "metabolomics", (
            Operation("study", "Study by id", "studies/{study_id}", args=("study_id",),
                      example={"study_id": "MTBLS1"}),
            Operation("studies", "Public study list", "studies"),
        ), smoke="study", docs="https://www.ebi.ac.uk/metabolights/ws/api/spec"),

    # ------------------------------------------------ pathways / networks / drugs
    PublicSource(
        "wikipathways", "WikiPathways", "https://www.wikipathways.org/json", "www.wikipathways.org",
        "CC0-1.0", "Community-curated pathway models.", "pathways", (
            Operation("info", "Pathway info", "getPathwayInfo.json", params={"pwId": "{pathway_id}"},
                      args=("pathway_id",), example={"pathway_id": "WP254"}),
            Operation("find", "Find pathways by text", "findPathwaysByText.json",
                      params={"query": "{query}"}, args=("query",), example={"query": "apoptosis"}),
            Operation("list", "All pathways", "listPathways.json"),
        ), smoke="info", docs="https://www.wikipathways.org/json/"),

    PublicSource(
        "omnipath", "OmniPath", "https://omnipathdb.org", "omnipathdb.org",
        "Mixed per resource (some CC-BY-NC-SA)",
        "Curated signalling interactions, enzyme-substrate relations and annotations.",
        "pathways", (
            Operation("interactions", "Interactions for partners", "interactions",
                      params={"partners": "{partners}", "genesymbols": 1, "format": "json",
                              "limit": "{limit}"},
                      args=("partners",), example={"partners": "TP53", "limit": 10}),
            Operation("annotations", "Annotations for proteins", "annotations",
                      params={"proteins": "{proteins}", "genesymbols": 1, "format": "json",
                              "limit": "{limit}"},
                      args=("proteins",), example={"proteins": "TP53", "limit": 10}),
        ), smoke="interactions", docs="https://omnipathdb.org/"),

    PublicSource(
        "dgidb", "DGIdb GraphQL", "https://dgidb.org/api/graphql", "dgidb.org",
        "MIT (code) / CC-BY-4.0 (data)", "Drug-gene interactions and druggable categories.",
        "drug-discovery", (
            Operation("gene_interactions", "Drugs interacting with a gene", "", method="POST",
                      graphql="query($names:[String!]){genes(names:$names){nodes{name "
                              "interactions{drug{name conceptId} interactionScore "
                              "interactionTypes{type directionality}}}}}",
                      variables={"names": "{gene}"}, args=("gene",), example={"gene": "BRAF"}),
            Operation("drug_interactions", "Genes interacting with a drug", "", method="POST",
                      graphql="query($names:[String!]){drugs(names:$names){nodes{name conceptId "
                              "interactions{gene{name} interactionScore}}}}",
                      variables={"names": "{drug}"}, args=("drug",), example={"drug": "IMATINIB"}),
        ), smoke="gene_interactions", docs="https://dgidb.org/api"),

    PublicSource(
        "civic", "CIViC GraphQL", "https://civicdb.org/api/graphql", "civicdb.org", "CC0-1.0",
        "Clinical interpretations of variants in cancer.", "cancer-genomics", (
            Operation("gene", "Gene with its variants", "", method="POST",
                      graphql="query($s:String!){gene(entrezSymbol:$s){id name entrezId "
                              "variants(first:10){nodes{id name}}}}",
                      variables={"s": "{gene}"}, args=("gene",), example={"gene": "BRAF"}),
            Operation("variant", "Variant by CIViC id", "", method="POST",
                      graphql="query($id:Int!){variant(id:$id){id name link}}",
                      variables={"id": "{variant_id}"}, args=("variant_id",),
                      example={"variant_id": 12}),
        ), smoke="gene", docs="https://docs.civicdb.org/en/latest/api.html"),

    PublicSource(
        "cbioportal", "cBioPortal", "https://www.cbioportal.org/api", "www.cbioportal.org",
        "ODbL / per-study terms", "Cancer genomics studies, samples and molecular profiles.",
        "cancer-genomics", (
            Operation("gene", "Gene by Hugo symbol or Entrez id", "genes/{gene}", args=("gene",),
                      example={"gene": "TP53"}),
            Operation("studies", "List studies", "studies",
                      params={"pageSize": "{page_size}", "projection": "SUMMARY"},
                      example={"page_size": 3}),
            Operation("study", "Study by id", "studies/{study_id}", args=("study_id",),
                      example={"study_id": "brca_tcga_pan_can_atlas_2018"}),
            Operation("molecular_profiles", "Molecular profiles of a study",
                      "studies/{study_id}/molecular-profiles", args=("study_id",),
                      example={"study_id": "brca_tcga_pan_can_atlas_2018"}),
        ), smoke="gene", docs="https://www.cbioportal.org/api/swagger-ui/index.html"),

    PublicSource(
        "gdc", "NCI GDC API", "https://api.gdc.cancer.gov", "api.gdc.cancer.gov",
        "Public (NCI GDC open data)", "Cancer genomics projects, cases and files.",
        "cancer-genomics", (
            Operation("project", "Project by id", "projects/{project_id}",
                      params={"expand": "summary"}, args=("project_id",),
                      example={"project_id": "TCGA-BRCA"}),
            Operation("projects", "List projects", "projects",
                      params={"size": "{size}", "fields": "project_id,name,primary_site,disease_type"},
                      example={"size": 3}),
            Operation("cases", "Cases matching a filter", "cases",
                      params={"filters": "{filters}", "size": "{size}",
                              "fields": "case_id,submitter_id,project.project_id"},
                      args=("filters",),
                      example={"filters": '{"op":"=","content":{"field":"project.project_id",'
                                          '"value":"TCGA-BRCA"}}', "size": 2}),
            Operation("status", "API status", "status"),
        ), smoke="project", docs="https://docs.gdc.cancer.gov/API/Users_Guide/Getting_Started/"),

    # ----------------------------------------- clinical & medical terminology
    PublicSource(
        "nlm_clinical_tables", "NLM Clinical Table Search Service",
        "https://clinicaltables.nlm.nih.gov/api", "clinicaltables.nlm.nih.gov",
        "Public-domain (US Gov)",
        "Lookups over ICD-10-CM, RxTerms, LOINC, HCPCS and medical condition vocabularies.",
        "medical-terminology", (
            Operation("icd10cm", "ICD-10-CM codes", "icd10cm/v3/search",
                      params={"sf": "code,name", "terms": "{terms}", "maxList": "{max_list}"},
                      args=("terms",), example={"terms": "type 2 diabetes", "max_list": 5}),
            Operation("rxterms", "RxTerms drug names", "rxterms/v3/search",
                      params={"terms": "{terms}", "maxList": "{max_list}",
                              "ef": "STRENGTHS_AND_FORMS"},
                      args=("terms",), example={"terms": "metformin", "max_list": 5}),
            Operation("loinc", "LOINC items", "loinc_items/v3/search",
                      params={"terms": "{terms}", "maxList": "{max_list}"}, args=("terms",),
                      example={"terms": "hemoglobin a1c", "max_list": 5}),
            Operation("conditions", "Medical conditions", "conditions/v3/search",
                      params={"terms": "{terms}", "maxList": "{max_list}"}, args=("terms",),
                      example={"terms": "asthma", "max_list": 5}),
            Operation("hcpcs", "HCPCS codes", "hcpcs/v3/search",
                      params={"terms": "{terms}", "maxList": "{max_list}"}, args=("terms",),
                      example={"terms": "wheelchair", "max_list": 5}),
        ), smoke="icd10cm", docs="https://clinicaltables.nlm.nih.gov/"),

    PublicSource(
        "rxnav", "RxNav RxNorm API", "https://rxnav.nlm.nih.gov/REST", "rxnav.nlm.nih.gov",
        "Public-domain (US Gov)",
        "RxNorm concepts, properties, related concepts and drug classes.", "clinical", (
            Operation("properties", "Properties of an RxCUI", "rxcui/{rxcui}/properties.json",
                      args=("rxcui",), example={"rxcui": "6809"}),
            Operation("rxcui_by_name", "RxCUI for a drug name", "rxcui.json",
                      params={"name": "{name}", "search": "{search}"}, args=("name",),
                      example={"name": "metformin", "search": 2}),
            Operation("related", "Related concepts by term type", "rxcui/{rxcui}/related.json",
                      params={"tty": "{tty}"}, args=("rxcui",),
                      example={"rxcui": "6809", "tty": "SCD SBD"}),
            Operation("approximate", "Approximate term match", "approximateTerm.json",
                      params={"term": "{term}", "maxEntries": "{max_entries}"}, args=("term",),
                      example={"term": "metfromin", "max_entries": 3}),
            Operation("drug_classes", "Drug classes for an RxCUI", "rxclass/class/byRxcui.json",
                      params={"rxcui": "{rxcui}"}, args=("rxcui",), example={"rxcui": "6809"}),
        ), smoke="properties", docs="https://lhncbc.nlm.nih.gov/RxNav/APIs/", rate_note="20 req/s"),

    PublicSource(
        "dailymed", "DailyMed", "https://dailymed.nlm.nih.gov/dailymed/services/v2",
        "dailymed.nlm.nih.gov", "Public-domain (US Gov)", "FDA structured product labels (SPL).",
        "clinical", (
            Operation("spls", "Labels by drug name", "spls.json",
                      params={"drug_name": "{drug_name}", "pagesize": "{page_size}"},
                      args=("drug_name",), example={"drug_name": "metformin", "page_size": 3}),
            Operation("drug_names", "Drug names matching", "drugnames.json",
                      params={"drug_name": "{drug_name}", "pagesize": "{page_size}"},
                      args=("drug_name",), example={"drug_name": "metfor", "page_size": 3}),
        ), smoke="spls", docs="https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm"),

    PublicSource(
        "mesh", "MeSH RDF lookup", "https://id.nlm.nih.gov/mesh/lookup", "id.nlm.nih.gov",
        "Public-domain (US Gov)", "MeSH descriptors and terms.", "medical-terminology", (
            Operation("descriptor", "Descriptors matching a label", "descriptor",
                      params={"label": "{label}", "match": "{match}", "limit": "{limit}"},
                      args=("label",),
                      example={"label": "Diabetes Mellitus, Type 2", "match": "contains", "limit": 5}),
            Operation("term", "Terms matching a label", "term",
                      params={"label": "{label}", "match": "{match}", "limit": "{limit}"},
                      args=("label",), example={"label": "asthma", "match": "contains", "limit": 5}),
            Operation("details", "Descriptor details", "details",
                      params={"descriptor": "{descriptor}"}, args=("descriptor",),
                      example={"descriptor": "D001249"}),
        ), smoke="descriptor", docs="https://id.nlm.nih.gov/mesh/swagger/ui"),

    # ------------------------------------------------------------- literature
    PublicSource(
        "pubtator", "PubTator 3", "https://www.ncbi.nlm.nih.gov/research/pubtator3-api",
        "www.ncbi.nlm.nih.gov", "Public-domain (US Gov)",
        "Biomedical entity annotations and relations over PubMed and PMC.", "literature", (
            Operation("export", "Annotated articles as BioC JSON", "publications/export/biocjson",
                      params={"pmids": "{pmids}"}, args=("pmids",), example={"pmids": "31452104"}),
            Operation("search", "Search annotated literature", "search/",
                      params={"text": "{text}", "page": "{page}"}, args=("text",),
                      example={"text": "@GENE_TP53 AND @DISEASE_Neoplasms", "page": 1}),
            Operation("autocomplete", "Entity autocomplete", "entity/autocomplete/",
                      params={"query": "{query}", "limit": "{limit}"}, args=("query",),
                      example={"query": "TP53", "limit": 5}),
        ), smoke="export", docs="https://www.ncbi.nlm.nih.gov/research/pubtator3/api",
        rate_note="3 req/s"),

    PublicSource(
        "europepmc_annotations", "Europe PMC Annotations",
        "https://www.ebi.ac.uk/europepmc/annotations_api", "www.ebi.ac.uk", "CC-BY",
        "Text-mined gene, disease and chemical annotations of the literature.", "literature", (
            Operation("by_article", "Annotations for articles", "annotationsByArticleIds",
                      params={"articleIds": "{article_ids}", "type": "{type}", "format": "JSON"},
                      args=("article_ids",),
                      example={"article_ids": "MED:31452104", "type": "Gene_Proteins"}),
            Operation("by_entity", "Articles annotated with an entity", "annotationsByEntity",
                      params={"entity": "{entity}", "filter": 1, "format": "JSON",
                              "pageSize": "{page_size}"},
                      args=("entity",), example={"entity": "TP53", "page_size": 3}),
        ), smoke="by_article", docs="https://europepmc.org/AnnotationsApi"),

    PublicSource(
        "crossref", "Crossref", "https://api.crossref.org", "api.crossref.org",
        "CC0-1.0 (metadata)", "Scholarly metadata by DOI.", "literature", (
            Operation("work", "Work by DOI", "works/{doi}", args=("doi",),
                      example={"doi": "10.1038/s41586-020-2308-7"}),
            Operation("search", "Search works", "works",
                      params={"query": "{query}", "rows": "{rows}",
                              "select": "DOI,title,author,issued,container-title"},
                      args=("query",), example={"query": "TP53 mutation cancer", "rows": 3}),
        ), smoke="work", docs="https://api.crossref.org/swagger-ui/index.html",
        rate_note="polite pool; be gentle"),

    PublicSource(
        "openalex", "OpenAlex", "https://api.openalex.org", "api.openalex.org", "CC0-1.0",
        "Open scholarly graph: works, authors, sources and topics.", "literature", (
            Operation("work", "Work by OpenAlex id or DOI", "works/{work_id}", args=("work_id",),
                      example={"work_id": "doi:10.1038/s41586-020-2308-7"}),
        ), smoke="work", docs="https://docs.openalex.org/",
        rate_note="10 req/s; search endpoints spend a per-address daily budget"),

    PublicSource(
        "biorxiv", "bioRxiv / medRxiv API", "https://api.biorxiv.org", "api.biorxiv.org",
        "CC-BY-4.0 (metadata)", "Preprint metadata by DOI or date range.", "literature", (
            Operation("details", "Preprint details by DOI", "details/{server}/{doi}/na/json",
                      args=("doi",), example={"server": "biorxiv", "doi": "10.1101/2020.03.24.004655"}),
        ), smoke="details", docs="https://api.biorxiv.org/"),

    PublicSource(
        "ebi_search", "EBI Search", "https://www.ebi.ac.uk/ebisearch/ws/rest", "www.ebi.ac.uk",
        "Apache-2.0 (service)", "Cross-resource search over EMBL-EBI databases.", "literature", (
            Operation("search", "Search a domain", "{domain}",
                      params={"query": "{query}", "size": "{size}", "format": "json"},
                      args=("domain", "query"),
                      example={"domain": "uniprot", "query": "TP53 AND organism_id:9606", "size": 3}),
            Operation("domains", "Available domains", "", params={"format": "json"}),
        ), smoke="search", docs="https://www.ebi.ac.uk/ebisearch/documentation"),

    # ------------------------------------------ ontologies / phenotypes / disease
    PublicSource(
        "ols", "EMBL-EBI OLS4", "https://www.ebi.ac.uk/ols4/api", "www.ebi.ac.uk",
        "Apache-2.0 (service) / per ontology",
        "Ontology lookup: HP, MONDO, EFO, GO, CL, UBERON, ChEBI and 250 more.", "ontology", (
            Operation("term", "Term by OBO id", "ontologies/{ontology}/terms",
                      params={"obo_id": "{obo_id}"}, args=("ontology", "obo_id"),
                      example={"ontology": "hp", "obo_id": "HP:0001250"}),
            Operation("search", "Search terms", "search",
                      params={"q": "{q}", "ontology": "{ontology}", "rows": "{rows}"},
                      args=("q",), example={"q": "seizure", "ontology": "hp", "rows": 5}),
            Operation("ontologies", "Ontology list", "ontologies", params={"size": "{size}"},
                      example={"size": 5}),
        ), smoke="term", docs="https://www.ebi.ac.uk/ols4/help"),

    PublicSource(
        "hpo", "HPO (JAX Ontology API)", "https://ontology.jax.org/api", "ontology.jax.org",
        "HPO licence (free with attribution)", "Human Phenotype Ontology terms and annotations.",
        "ontology", (
            Operation("term", "HPO term", "hp/terms/{hpo_id}", args=("hpo_id",),
                      example={"hpo_id": "HP:0001250"}),
            Operation("search", "Search HPO terms", "hp/search",
                      params={"q": "{q}", "limit": "{limit}"}, args=("q",),
                      example={"q": "seizure", "limit": 5}),
            Operation("term_annotations", "Genes and diseases annotated to a term",
                      "network/annotation/{hpo_id}", args=("hpo_id",),
                      example={"hpo_id": "HP:0001250"}),
        ), smoke="term", docs="https://ontology.jax.org/api/hp/docs"),

    PublicSource(
        "monarch", "Monarch Initiative API v3", "https://api.monarchinitiative.org/v3/api",
        "api.monarchinitiative.org", "BSD-3-Clause (service) / per source",
        "Gene, disease and phenotype knowledge graph.", "ontology", (
            Operation("entity", "Entity by CURIE", "entity/{id}", args=("id",),
                      example={"id": "MONDO:0005015"}),
            Operation("search", "Search entities", "search",
                      params={"q": "{q}", "limit": "{limit}"}, args=("q",),
                      example={"q": "cystic fibrosis", "limit": 5}),
            Operation("associations", "Associations for a subject", "association",
                      params={"subject": "{subject}", "limit": "{limit}"}, args=("subject",),
                      example={"subject": "MONDO:0005015", "limit": 5}),
        ), smoke="entity", docs="https://api.monarchinitiative.org/v3/docs"),

    PublicSource(
        "disease_ontology", "Disease Ontology", "https://disease-ontology.org/api",
        "disease-ontology.org", "CC0-1.0", "Human Disease Ontology terms.", "ontology", (
            Operation("metadata", "Term metadata", "metadata/{doid}", args=("doid",),
                      example={"doid": "DOID:1612"}),
        ), smoke="metadata", docs="https://disease-ontology.org/resources/apis"),

    PublicSource(
        "bioregistry", "Bioregistry", "https://bioregistry.io/api", "bioregistry.io", "CC0-1.0",
        "Registry of identifier prefixes, patterns and resolvers.", "ontology", (
            Operation("registry", "Prefix record", "registry/{prefix}", args=("prefix",),
                      example={"prefix": "hgnc"}),
            Operation("reference", "Resolve a CURIE", "reference/{prefix}:{identifier}",
                      args=("prefix", "identifier"), example={"prefix": "hgnc", "identifier": "11998"}),
        ), smoke="registry", docs="https://bioregistry.io/apidocs/"),

    PublicSource(
        "identifiers_org", "Identifiers.org resolver", "https://resolver.api.identifiers.org",
        "resolver.api.identifiers.org", "CC0-1.0", "Resolve compact identifiers to provider URLs.",
        "ontology", (
            Operation("resolve", "Resolve a CURIE", "{curie}", args=("curie",),
                      example={"curie": "hgnc:11998"}),
        ), smoke="resolve", docs="https://docs.identifiers.org/"),

    # -------------------------------------------- natural products / taxonomy
    PublicSource(
        "wikidata_sparql", "Wikidata SPARQL", "https://query.wikidata.org", "query.wikidata.org",
        "CC0-1.0", "Knowledge-graph queries: taxa (P225), LOTUS natural-product occurrences "
        "(P703 found in taxon, P235 InChIKey, P233 SMILES), Chinese herbology items, diseases, "
        "genes and drugs, by SPARQL.", "natural-products", (
            Operation("taxon_by_name", "Taxon items by scientific name", "sparql",
                      params={"format": "json", "query": (
                          "SELECT ?item ?itemLabel ?rank ?rankLabel WHERE { ?item wdt:P225 "
                          "\"{name}\" . OPTIONAL { ?item wdt:P105 ?rank } SERVICE wikibase:label "
                          "{ bd:serviceParam wikibase:language \"en\" } } LIMIT {limit}")},
                      args=("name",), example={"name": "Panax ginseng", "limit": 5}),
            Operation("compounds_in_taxon", "Natural products recorded in a taxon (LOTUS)", "sparql",
                      params={"format": "json", "query": (
                          "SELECT ?compound ?compoundLabel ?inchikey ?smiles WHERE { ?compound "
                          "wdt:P703 wd:{taxon_qid} . OPTIONAL { ?compound wdt:P235 ?inchikey } "
                          "OPTIONAL { ?compound wdt:P233 ?smiles } SERVICE wikibase:label "
                          "{ bd:serviceParam wikibase:language \"en\" } } LIMIT {limit}")},
                      args=("taxon_qid",), example={"taxon_qid": "Q182881", "limit": 20}),
            Operation("taxa_with_compound", "Taxa in which a compound (by InChIKey) occurs", "sparql",
                      params={"format": "json", "query": (
                          "SELECT ?compound ?compoundLabel ?taxon ?taxonLabel WHERE { ?compound "
                          "wdt:P235 \"{inchikey}\" ; wdt:P703 ?taxon . SERVICE wikibase:label "
                          "{ bd:serviceParam wikibase:language \"en\" } } LIMIT {limit}")},
                      args=("inchikey",),
                      example={"inchikey": "YBHILYKTIRIUTE-UHFFFAOYSA-N", "limit": 20}),
            Operation("sparql", "Any read-only SPARQL query", "sparql",
                      params={"format": "json", "query": "{query}"}, args=("query",),
                      example={"query": "SELECT ?item ?itemLabel WHERE { ?item wdt:P31 wd:Q12140 ; "
                                        "wdt:P2275 ?name . SERVICE wikibase:label { bd:serviceParam "
                                        "wikibase:language \"en\" } } LIMIT 3"}),
        ), smoke="taxon_by_name", docs="https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service",
        rate_note="be gentle: 60 s query timeout, shared service"),

    PublicSource(
        "gbif", "GBIF API", "https://api.gbif.org/v1", "api.gbif.org", "CC-BY-4.0 / CC0 (per dataset)",
        "Taxonomic backbone, name matching and species occurrences.", "natural-products", (
            Operation("match", "Match a scientific name to the backbone", "species/match",
                      params={"name": "{name}"}, args=("name",), example={"name": "Panax ginseng"}),
            Operation("species", "Species record by GBIF key", "species/{key}", args=("key",),
                      example={"key": 3596893}),
            Operation("occurrences", "Occurrence records for a taxon key", "occurrence/search",
                      params={"taxonKey": "{taxon_key}", "limit": "{limit}"}, args=("taxon_key",),
                      example={"taxon_key": 3596893, "limit": 3}),
        ), smoke="match", docs="https://techdocs.gbif.org/en/openapi/"),

    # --------------------------------------------------------------- enrichment
    PublicSource(
        "gprofiler", "g:Profiler", "https://biit.cs.ut.ee/gprofiler/api", "biit.cs.ut.ee",
        "BSD-3-Clause (service)", "Functional enrichment (g:GOSt) and identifier conversion.",
        "pathways", (
            Operation("gost", "Enrichment of a gene list", "gost/profile/", method="POST",
                      json_body={"organism": "{organism}", "query": "{query}",
                                 "sources": ["GO:BP", "KEGG", "REAC"], "user_threshold": 0.05},
                      args=("query",),
                      example={"organism": "hsapiens", "query": "TP53 BRCA1 BRCA2 ATM CHEK2"}),
            Operation("convert", "Convert identifiers", "convert/convert/", method="POST",
                      json_body={"organism": "{organism}", "query": "{query}", "target": "{target}"},
                      args=("query",), example={"organism": "hsapiens", "query": "TP53 BRCA1",
                                                "target": "ENSG"}),
        ), smoke="gost", docs="https://biit.cs.ut.ee/gprofiler/page/apis"),

    PublicSource(
        "panther", "PANTHER", "https://pantherdb.org/services/oai/pantherdb", "pantherdb.org",
        "Free for research use (PANTHER terms)",
        "Gene family classification and overrepresentation tests.", "pathways", (
            Operation("geneinfo", "Gene information", "geneinfo",
                      params={"geneInputList": "{genes}", "organism": "{organism}"},
                      args=("genes",), example={"genes": "TP53,BRCA1", "organism": 9606}),
            Operation("enrich", "Overrepresentation test", "enrich/overrep",
                      params={"geneInputList": "{genes}", "organism": "{organism}",
                              "annotDataSet": "{annot}", "enrichmentTestType": "FISHER",
                              "correction": "FDR"},
                      args=("genes",),
                      example={"genes": "TP53,BRCA1,BRCA2,ATM,CHEK2", "organism": 9606,
                               "annot": "GO:0008150"}),
        ), smoke="geneinfo", docs="https://pantherdb.org/services/openAPISpec.jsp"),
)

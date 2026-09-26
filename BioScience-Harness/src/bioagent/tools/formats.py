"""Parsers for the text formats bioinformatics runs on: FASTA, FASTQ, VCF, BED, GFF."""

from __future__ import annotations

import re
import statistics
from typing import Any

__all__ = ["parse_fasta", "parse_fastq", "parse_vcf", "parse_bed", "parse_gff", "parse_sam",
           "parse_pdb", "parse_obo"]


def _text(value: Any, what: str = "text") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string")
    if not value.strip():
        raise ValueError(f"{what} is empty")
    return value


def parse_fasta(text: str, max_records: int = 1000) -> dict[str, Any]:
    """FASTA records: id (first token of the header), description, sequence, length."""
    records: list[dict[str, Any]] = []
    header, chunks = None, []
    for line in _text(text).splitlines():
        if line.startswith(">"):
            if header is not None:
                records.append(_fasta_record(header, chunks))
            header, chunks = line[1:].strip(), []
        elif header is not None:
            chunks.append(line.strip())
        elif line.strip():
            raise ValueError("FASTA text must start with a '>' header line")
    if header is not None:
        records.append(_fasta_record(header, chunks))
    return {"count": len(records), "records": records[:max_records],
            "total_length": sum(r["length"] for r in records)}


def _fasta_record(header: str, chunks: list[str]) -> dict[str, Any]:
    seq = "".join(chunks).upper()
    ident, _, desc = header.partition(" ")
    return {"id": ident, "description": desc, "sequence": seq, "length": len(seq)}


def parse_fastq(text: str, max_records: int = 200, phred_offset: int = 33) -> dict[str, Any]:
    """FASTQ summary: read count, length and quality statistics, first records."""
    lines = [ln.rstrip("\n") for ln in _text(text).splitlines() if ln.strip()]
    if len(lines) % 4:
        raise ValueError("FASTQ text is not a multiple of four lines")
    records, lengths, means, q30 = [], [], [], 0
    for i in range(0, len(lines), 4):
        head, seq, plus, qual = lines[i:i + 4]
        if not head.startswith("@") or not plus.startswith("+"):
            raise ValueError(f"malformed FASTQ record at line {i + 1}")
        if len(seq) != len(qual):
            raise ValueError(f"sequence and quality lengths differ at line {i + 1}")
        scores = [ord(c) - phred_offset for c in qual]
        mean_q = sum(scores) / len(scores) if scores else 0.0
        q30 += sum(1 for s in scores if s >= 30)
        lengths.append(len(seq)); means.append(mean_q)
        if len(records) < max_records:
            records.append({"id": head[1:].split()[0], "sequence": seq, "mean_quality": round(mean_q, 2)})
    total_bases = sum(lengths)
    return {"count": len(lengths), "total_bases": total_bases,
            "mean_length": round(statistics.mean(lengths), 2) if lengths else 0,
            "mean_quality": round(statistics.mean(means), 2) if means else 0,
            "q30_fraction": round(q30 / total_bases, 4) if total_bases else 0,
            "records": records}


def _info(field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if field in (".", ""):
        return out
    for item in field.split(";"):
        key, sep, value = item.partition("=")
        out[key] = value if sep else True
    return out


def parse_vcf(text: str, max_records: int = 500) -> dict[str, Any]:
    """VCF records with parsed INFO and per-sample genotype fields."""
    samples: list[str] = []
    records: list[dict[str, Any]] = []
    seen_header = False
    for line in _text(text).splitlines():
        if not line.strip():
            continue
        if line.startswith("##"):
            continue
        if line.startswith("#CHROM"):
            cols = line[1:].split("\t")
            samples = cols[9:]
            seen_header = True
            continue
        if not seen_header:
            raise ValueError("VCF text has no #CHROM header line")
        cols = line.split("\t")
        if len(cols) < 8:
            raise ValueError(f"VCF record has {len(cols)} columns; at least 8 are required")
        record: dict[str, Any] = {
            "chrom": cols[0], "pos": int(cols[1]), "id": None if cols[2] == "." else cols[2],
            "ref": cols[3], "alt": cols[4].split(","),
            "qual": None if cols[5] == "." else float(cols[5]), "filter": cols[6],
            "info": _info(cols[7])}
        if samples and len(cols) > 9:
            keys = cols[8].split(":")
            record["genotypes"] = {
                sample: dict(zip(keys, cols[9 + k].split(":")))
                for k, sample in enumerate(samples) if 9 + k < len(cols)}
        records.append(record)
    return {"samples": samples, "count": len(records), "records": records[:max_records]}


def parse_bed(text: str, max_records: int = 1000) -> dict[str, Any]:
    """BED intervals (0-based, half-open) with optional name, score and strand."""
    records = []
    for line in _text(text).splitlines():
        if not line.strip() or line.startswith(("track", "browser", "#")):
            continue
        cols = line.split("\t") if "\t" in line else line.split()
        if len(cols) < 3:
            raise ValueError(f"BED line has fewer than three columns: {line[:60]!r}")
        rec: dict[str, Any] = {"chrom": cols[0], "start": int(cols[1]), "end": int(cols[2])}
        if len(cols) > 3:
            rec["name"] = cols[3]
        if len(cols) > 4:
            rec["score"] = cols[4]
        if len(cols) > 5:
            rec["strand"] = cols[5]
        rec["length"] = rec["end"] - rec["start"]
        records.append(rec)
    return {"count": len(records), "records": records[:max_records],
            "total_span": sum(r["length"] for r in records)}


def parse_gff(text: str, max_records: int = 1000) -> dict[str, Any]:
    """GFF3/GTF features with attributes parsed into a mapping."""
    records = []
    for line in _text(text).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 9:
            raise ValueError(f"GFF line does not have nine columns: {line[:60]!r}")
        attrs: dict[str, str] = {}
        raw = cols[8].strip().rstrip(";")
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" in item:                       # GFF3
                k, _, v = item.partition("=")
            else:                                 # GTF: key "value"
                k, _, v = item.partition(" ")
            attrs[k.strip()] = v.strip().strip('"')
        records.append({"seqid": cols[0], "source": cols[1], "type": cols[2],
                        "start": int(cols[3]), "end": int(cols[4]),
                        "score": None if cols[5] == "." else float(cols[5]),
                        "strand": cols[6], "phase": cols[7], "attributes": attrs})
    types: dict[str, int] = {}
    for r in records:
        types[r["type"]] = types.get(r["type"], 0) + 1
    return {"count": len(records), "records": records[:max_records], "feature_types": types}


# ----------------------------------------------------------- SAM, PDB, OBO

_SAM_FLAGS = (("paired", 0x1), ("proper_pair", 0x2), ("unmapped", 0x4), ("mate_unmapped", 0x8),
              ("reverse", 0x10), ("mate_reverse", 0x20), ("first_in_pair", 0x40),
              ("second_in_pair", 0x80), ("secondary", 0x100), ("qc_fail", 0x200),
              ("duplicate", 0x400), ("supplementary", 0x800))
_CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


def parse_sam(text: str, max_records: int = 500) -> dict[str, Any]:
    """SAM alignment records: decoded flags, CIGAR operation totals, aligned reference
    span, and a mapping summary. Header ``@SQ`` lines give the reference names."""
    body = _text(value=text)
    references, records = {}, []
    n_lines = mapped = 0
    mapq_sum = 0
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith("@"):
            if line.startswith("@SQ"):
                fields = dict(f.split(":", 1) for f in line.split("\t")[1:] if ":" in f)
                if "SN" in fields:
                    references[fields["SN"]] = int(fields.get("LN", 0) or 0)
            continue
        parts = line.split("\t")
        if len(parts) < 11:
            raise ValueError(f"SAM record has {len(parts)} fields, expected at least 11")
        n_lines += 1
        flag = int(parts[1])
        ops: dict[str, int] = {}
        for count, op in _CIGAR.findall(parts[5]):
            ops[op] = ops.get(op, 0) + int(count)
        is_mapped = not flag & 0x4
        if is_mapped:
            mapped += 1
            mapq_sum += int(parts[4])
        if len(records) < max_records:
            records.append({
                "qname": parts[0], "flag": flag,
                "flags": [name for name, bit in _SAM_FLAGS if flag & bit],
                "rname": parts[2], "pos": int(parts[3]), "mapq": int(parts[4]),
                "cigar": parts[5], "cigar_ops": ops,
                "aligned_ref_span": sum(ops.get(o, 0) for o in "MDN=X"),
                "soft_clipped": ops.get("S", 0), "read_length": len(parts[9]) if parts[9] != "*" else 0,
                "mate_rname": parts[6], "template_length": int(parts[8])})
    if n_lines == 0:
        raise ValueError("no alignment records found")
    return {"references": references, "n_records": n_lines, "mapped": mapped,
            "mapped_fraction": round(mapped / n_lines, 4),
            "mean_mapq_mapped": round(mapq_sum / mapped, 2) if mapped else None,
            "records": records, "truncated": n_lines > max_records}


_AA3 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLU": "E", "GLN": "Q",
        "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
        "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "SEC": "U",
        "MSE": "M"}


def parse_pdb(text: str) -> dict[str, Any]:
    """PDB coordinates: atom counts, chains with residue counts and one-letter sequences,
    hetero groups (ligands, water counted separately), centroid and bounding box. Only the
    first MODEL is read."""
    body = _text(value=text)
    atoms = hetatms = models = 0
    waters = 0
    chains: dict[str, dict[str, Any]] = {}
    ligands: dict[str, int] = {}
    xs, ys, zs = [], [], []
    seen_residues: set[tuple[str, str, str]] = set()
    for line in body.splitlines():
        record = line[0:6].strip()
        if record == "MODEL":
            models += 1
            if models > 1:
                break
            continue
        if record == "ENDMDL":
            break
        if record not in ("ATOM", "HETATM"):
            continue
        if len(line) < 54:
            raise ValueError("coordinate line shorter than the fixed-column format allows")
        res_name = line[17:20].strip()
        chain = line[21].strip() or "_"
        res_seq = line[22:27].strip()
        try:
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
        except ValueError:
            raise ValueError("unparseable coordinates on a coordinate line") from None
        xs.append(x); ys.append(y); zs.append(z)
        key = (chain, res_seq, res_name)
        if record == "ATOM":
            atoms += 1
            entry = chains.setdefault(chain, {"residues": 0, "atoms": 0, "sequence": ""})
            entry["atoms"] += 1
            if key not in seen_residues:
                seen_residues.add(key)
                entry["residues"] += 1
                entry["sequence"] += _AA3.get(res_name, "X")
        else:
            hetatms += 1
            if res_name == "HOH":
                waters += 1
            elif key not in seen_residues:
                seen_residues.add(key)
                ligands[res_name] = ligands.get(res_name, 0) + 1
    if not xs:
        raise ValueError("no ATOM or HETATM records found")
    n = len(xs)
    return {"atoms": atoms, "hetatms": hetatms, "waters": waters, "models_read": max(models, 1),
            "chains": chains, "hetero_groups": ligands,
            "centroid": [round(sum(xs) / n, 3), round(sum(ys) / n, 3), round(sum(zs) / n, 3)],
            "bounding_box": {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}}


def parse_obo(text: str, max_terms: int = 2000) -> dict[str, Any]:
    """OBO ontology terms (GO, HPO, DO, ...): id, name, namespace, definition, parents
    (``is_a``), obsolescence, synonym count; plus root terms and a count by namespace."""
    body = _text(value=text)
    terms: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    header: dict[str, str] = {}
    in_term = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("["):
            if current is not None and in_term:
                terms.append(current)
            in_term = line == "[Term]"
            current = {"id": "", "name": "", "namespace": "", "definition": "", "is_a": [],
                       "obsolete": False, "synonyms": 0} if in_term else None
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if current is None:
            if not in_term and not terms and key not in header:
                header[key] = value
            continue
        if key == "id":
            current["id"] = value
        elif key == "name":
            current["name"] = value
        elif key == "namespace":
            current["namespace"] = value
        elif key == "def":
            m = re.match(r'"((?:[^"\\]|\\.)*)"', value)
            current["definition"] = m.group(1) if m else value
        elif key == "is_a":
            target = value.split("!")[0].strip()
            current["is_a"].append(target)
        elif key == "is_obsolete":
            current["obsolete"] = value.lower() == "true"
        elif key == "synonym":
            current["synonyms"] += 1
    if current is not None and in_term:
        terms.append(current)
    if not terms:
        raise ValueError("no [Term] stanzas found")
    by_ns: dict[str, int] = {}
    for t in terms:
        by_ns[t["namespace"] or "(none)"] = by_ns.get(t["namespace"] or "(none)", 0) + 1
    roots = [t["id"] for t in terms if not t["is_a"] and not t["obsolete"]]
    return {"header": header, "n_terms": len(terms), "obsolete": sum(1 for t in terms if t["obsolete"]),
            "by_namespace": by_ns, "roots": roots[:50], "terms": terms[:max_terms],
            "truncated": len(terms) > max_terms}

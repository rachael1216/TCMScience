"""Variant tools: HGVS parsing, allele normalisation, allele frequencies, Ts/Tv."""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

__all__ = ["parse_hgvs", "normalise_variant", "allele_frequencies", "transition_transversion",
           "annotate_coding_variant"]

_HGVS = re.compile(r"^(?:(?P<reference>[A-Za-z0-9_.()-]+):)?(?P<type>[cgmnpr])\.(?P<body>.+)$")
_POS = r"[-*]?\d+(?:[+-]\d+)?"
_SUB = re.compile(rf"^(?P<start>{_POS})(?P<ref>[ACGTUN])>(?P<alt>[ACGTUN])$")
_DEL = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?del(?P<seq>[ACGTUN]+)?$")
_DUP = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?dup(?P<seq>[ACGTUN]+)?$")
_INS = re.compile(rf"^(?P<start>{_POS})_(?P<end>{_POS})ins(?P<seq>[ACGTUN]+)$")
_DELINS = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?delins(?P<seq>[ACGTUN]+)$")
_PROT = re.compile(r"^(?P<ref>[A-Z][a-z]{2}|[A-Z*])(?P<pos>\d+)"
                   r"(?P<alt>[A-Z][a-z]{2}|Ter|[A-Z*]|fs(?:\*\d+|Ter\d+)?|=|del|dup)$")

_THREE = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "E": "Glu", "Q": "Gln",
          "G": "Gly", "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe",
          "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter",
          "X": "Ter"}
_ONE = {v: k for k, v in _THREE.items() if k not in ("X",)}


def _aa3(token: str) -> str:
    if token in ("*", "Ter", "X"):
        return "Ter"
    if len(token) == 1:
        if token not in _THREE:
            raise ValueError(f"unknown amino acid code {token!r}")
        return _THREE[token]
    if token not in _ONE:
        raise ValueError(f"unknown amino acid code {token!r}")
    return token


def parse_hgvs(hgvs: str) -> dict[str, Any]:
    """Parse the common HGVS shapes: substitution, deletion, duplication, insertion,
    deletion-insertion at the c./g./n./m./r. level, and substitution, frameshift,
    nonsense, synonymous, deletion and duplication at the p. level."""
    if not isinstance(hgvs, str) or not hgvs.strip():
        raise ValueError("hgvs must be a non-empty string")
    text = hgvs.strip()
    m = _HGVS.match(text)
    if not m:
        raise ValueError(f"{text!r} is not an HGVS expression of the form [ref:]t.change")
    reference, kind, body = m.group("reference") or "", m.group("type"), m.group("body")
    out: dict[str, Any] = {"input": text, "reference": reference, "level": kind}
    if kind == "p":
        pm = _PROT.match(body)
        if not pm:
            raise ValueError(f"unsupported protein-level change {body!r}")
        ref3 = _aa3(pm.group("ref"))
        alt = pm.group("alt")
        if alt.startswith("fs"):
            change, alt3 = "frameshift", None
        elif alt == "=":
            change, alt3 = "synonymous", ref3
        elif alt in ("del", "dup"):
            change, alt3 = "deletion" if alt == "del" else "duplication", None
        else:
            alt3 = _aa3(alt)
            change = "nonsense" if alt3 == "Ter" else "substitution"
        out.update({"change": change, "position": int(pm.group("pos")), "ref": ref3,
                    "alt": alt3, "normalised": f"p.{ref3}{pm.group('pos')}"
                    + (alt if alt.startswith('fs') or alt in ('=', 'del', 'dup') else alt3)})
        return out
    for pattern, change in ((_SUB, "substitution"), (_DELINS, "delins"), (_DEL, "deletion"),
                            (_DUP, "duplication"), (_INS, "insertion")):
        bm = pattern.match(body)
        if bm:
            groups = bm.groupdict()
            out["change"] = change
            out["start"] = groups.get("start")
            out["end"] = groups.get("end") or groups.get("start")
            out["ref"] = groups.get("ref")
            out["alt"] = groups.get("alt") or groups.get("seq")
            out["intronic"] = any(sym in (out["start"] or "") for sym in "+-") or \
                any(sym in (out["end"] or "") for sym in "+-")
            return out
    raise ValueError(f"unsupported {kind}. change {body!r}")


def normalise_variant(chrom: str, pos: int, ref: str, alt: str) -> dict[str, Any]:
    """Trim shared suffix then shared prefix so equivalent representations share a key."""
    if not isinstance(chrom, str) or not chrom:
        raise ValueError("chrom is required")
    if not isinstance(pos, int) or pos < 1:
        raise ValueError("pos must be a positive 1-based integer")
    r, a = str(ref).upper(), str(alt).upper()
    if not r or not a or any(c not in "ACGTN" for c in r + a):
        raise ValueError("ref and alt must be non-empty ACGTN strings")
    original = (r, a)
    while len(r) > 1 and len(a) > 1 and r[-1] == a[-1]:
        r, a = r[:-1], a[:-1]
    while len(r) > 1 and len(a) > 1 and r[0] == a[0]:
        r, a = r[1:], a[1:]
        pos += 1
    name = chrom[3:] if chrom.lower().startswith("chr") else chrom
    return {"chrom": chrom, "pos": pos, "ref": r, "alt": a, "key": f"{name}-{pos}-{r}-{a}",
            "trimmed": original != (r, a),
            "type": ("snv" if len(r) == len(a) == 1 else "mnv" if len(r) == len(a)
                     else "deletion" if len(r) > len(a) else "insertion")}


def allele_frequencies(genotypes: Sequence[str]) -> dict[str, Any]:
    """Allele frequencies, heterozygosity and a Hardy–Weinberg chi-square (1 df) from
    diploid genotype calls such as ``0/0``, ``0/1``, ``1|1``; ``./.`` is missing."""
    if not isinstance(genotypes, (list, tuple)) or not genotypes:
        raise ValueError("genotypes must be a non-empty list of calls")
    hom_ref = het = hom_alt = missing = 0
    for call in genotypes:
        alleles = re.split(r"[/|]", str(call).strip())
        if len(alleles) != 2 or "." in alleles:
            missing += 1
            continue
        try:
            a, b = int(alleles[0]), int(alleles[1])
        except ValueError:
            raise ValueError(f"unparseable genotype {call!r}") from None
        if a == b == 0:
            hom_ref += 1
        elif a != b:
            het += 1
        else:
            hom_alt += 1
    n = hom_ref + het + hom_alt
    if n == 0:
        raise ValueError("no called genotypes")
    p = (2 * hom_ref + het) / (2 * n)
    q = 1 - p
    expected = [n * p * p, 2 * n * p * q, n * q * q]
    observed = [hom_ref, het, hom_alt]
    chi2 = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    return {"n_called": n, "n_missing": missing,
            "counts": {"hom_ref": hom_ref, "het": het, "hom_alt": hom_alt},
            "ref_allele_frequency": round(p, 6), "alt_allele_frequency": round(q, 6),
            "observed_heterozygosity": round(het / n, 6),
            "expected_heterozygosity": round(2 * p * q, 6),
            "hwe_chi_square": round(chi2, 6),
            "hwe_p_value": round(math.erfc(math.sqrt(chi2 / 2)), 6)}


def transition_transversion(changes: Sequence[Sequence[str]]) -> dict[str, Any]:
    """Ts/Tv ratio over ``[ref, alt]`` single-nucleotide changes."""
    if not isinstance(changes, (list, tuple)) or not changes:
        raise ValueError("changes must be a non-empty list of [ref, alt] pairs")
    purines, ts, tv, skipped = {"A", "G"}, 0, 0, 0
    for item in changes:
        if len(item) != 2:
            raise ValueError(f"each change must be a [ref, alt] pair, got {item!r}")
        r, a = str(item[0]).upper(), str(item[1]).upper()
        if len(r) != 1 or len(a) != 1 or r == a or r not in "ACGT" or a not in "ACGT":
            skipped += 1
            continue
        if (r in purines) == (a in purines):
            ts += 1
        else:
            tv += 1
    return {"transitions": ts, "transversions": tv, "skipped": skipped,
            "ts_tv_ratio": round(ts / tv, 4) if tv else None}


# ------------------------------------------------------- coding consequence

def annotate_coding_variant(cds: str, position: int, ref: str, alt: str) -> dict[str, Any]:
    """Consequence of a variant in a coding sequence (``c.`` coordinates, 1-based):
    synonymous, missense, nonsense (stop gained), stop lost, start lost, in-frame or
    frameshift indels. ``ref``/``alt`` may be empty for pure insertions/deletions."""
    from .sequence import CODON_TABLE, _clean

    seq = _clean(cds, what="cds").replace("U", "T")
    if any(c not in "ACGT" for c in seq):
        raise ValueError("cds must be an unambiguous ACGT sequence")
    if len(seq) % 3 != 0 or len(seq) < 3:
        raise ValueError("cds length must be a positive multiple of three")
    if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= len(seq):
        raise ValueError("position must be a 1-based integer within the cds")
    r = "" if ref in ("", "-", None) else _clean(ref, what="ref").replace("U", "T")
    a = "" if alt in ("", "-", None) else _clean(alt, what="alt").replace("U", "T")
    if not r and not a:
        raise ValueError("ref and alt cannot both be empty")
    if r and seq[position - 1:position - 1 + len(r)] != r:
        raise ValueError(f"reference mismatch: cds has {seq[position - 1:position - 1 + len(r)]!r} "
                         f"at c.{position}, not {r!r}")
    if r:
        mutated = seq[:position - 1] + a + seq[position - 1 + len(r):]
    else:                                     # insertion after position
        mutated = seq[:position] + a + seq[position:]

    def protein(s: str) -> str:
        return "".join(CODON_TABLE.get(s[i:i + 3], "X") for i in range(0, len(s) - len(s) % 3, 3))

    ref_prot, alt_prot = protein(seq), protein(mutated)
    if r and a and len(r) == len(a) == 1:
        hgvs_c = f"c.{position}{r}>{a}"
    elif not a:
        hgvs_c = f"c.{position}_{position + len(r) - 1}del" if len(r) > 1 else f"c.{position}del"
    elif not r:
        hgvs_c = f"c.{position}_{position + 1}ins{a}"
    else:
        hgvs_c = f"c.{position}_{position + len(r) - 1}delins{a}" if len(r) > 1 else f"c.{position}delins{a}"
    shift = (len(a) - len(r)) % 3
    codon_number = (position - 1) // 3 + 1
    ref_codon = seq[(codon_number - 1) * 3:codon_number * 3]
    ref_aa = CODON_TABLE[ref_codon]
    first_diff = next((i for i, (x, y) in enumerate(zip(ref_prot, alt_prot)) if x != y), None)
    if first_diff is None and len(alt_prot) != len(ref_prot):
        first_diff = min(len(ref_prot), len(alt_prot))
    if shift != 0:
        consequence = "frameshift"
        if first_diff is None:
            first_diff = codon_number - 1
        stop_at = alt_prot.find("*", first_diff)
        pos_aa = min(first_diff, len(ref_prot) - 1)
        new_aa = alt_prot[pos_aa] if pos_aa < len(alt_prot) else "*"
        if new_aa == "*":
            # The shifted frame reads a stop at the first altered codon: HGVS writes this
            # as a plain substitution to Ter, not as a frameshift.
            hgvs_p = f"p.{_THREE[ref_prot[pos_aa]]}{pos_aa + 1}Ter"
        else:
            fs = f"fs*{stop_at - first_diff + 1}" if stop_at >= 0 else "fs"
            hgvs_p = f"p.{_THREE[ref_prot[pos_aa]]}{pos_aa + 1}{_THREE.get(new_aa, 'Xaa')}{fs}"
        alt_codon = mutated[(codon_number - 1) * 3:codon_number * 3]
        alt_aa = CODON_TABLE.get(alt_codon, "X")
    elif len(r) == len(a):
        alt_codon = mutated[(codon_number - 1) * 3:codon_number * 3]
        alt_aa = CODON_TABLE[alt_codon]
        if ref_prot == alt_prot:
            consequence, hgvs_p = "synonymous", f"p.{_THREE[ref_aa]}{codon_number}="
        elif codon_number == 1 and ref_aa == "M" and alt_aa != "M":
            consequence, hgvs_p = "start_lost", f"p.Met1?"
        elif alt_aa == "*":
            consequence, hgvs_p = "nonsense", f"p.{_THREE[ref_aa]}{codon_number}Ter"
        elif ref_aa == "*":
            ext = alt_prot.find("*", codon_number - 1)
            consequence = "stop_lost"
            hgvs_p = f"p.Ter{codon_number}{_THREE[alt_aa]}ext*{ext - codon_number + 2 if ext >= 0 else '?'}"
        else:
            consequence, hgvs_p = "missense", f"p.{_THREE[ref_aa]}{codon_number}{_THREE[alt_aa]}"
    else:
        alt_codon = mutated[(codon_number - 1) * 3:codon_number * 3]
        alt_aa = CODON_TABLE.get(alt_codon, "X")
        consequence = "inframe_deletion" if len(a) < len(r) else "inframe_insertion"
        n_res = abs(len(a) - len(r)) // 3
        ref_stop = ref_prot.find("*") if "*" in ref_prot else len(ref_prot)
        alt_stop = alt_prot.find("*") if "*" in alt_prot else len(alt_prot)
        expected_stop = ref_stop - n_res if len(a) < len(r) else ref_stop + n_res
        if alt_stop < expected_stop:
            consequence += "_stop_gained"
        elif alt_stop > expected_stop:
            consequence += "_stop_lost"
        if first_diff is None or ref_prot == alt_prot:
            hgvs_p = "p.="
        elif len(a) < len(r):
            last = first_diff + n_res - 1
            hgvs_p = (f"p.{_THREE[ref_prot[first_diff]]}{first_diff + 1}"
                      + (f"_{_THREE[ref_prot[last]]}{last + 1}" if n_res > 1 else "") + "del")
        else:
            inserted = "".join(_THREE.get(x, "Xaa") for x in alt_prot[first_diff:first_diff + n_res])
            left = first_diff - 1
            if left >= 0:
                hgvs_p = (f"p.{_THREE[ref_prot[left]]}{left + 1}_{_THREE[ref_prot[first_diff]]}"
                          f"{first_diff + 1}ins{inserted}")
            else:
                hgvs_p = f"p.Met1_{_THREE[ref_prot[0]]}1ins{inserted}"
    return {"hgvs_c": hgvs_c, "hgvs_p": hgvs_p, "consequence": consequence,
            "codon_number": codon_number, "ref_codon": ref_codon, "alt_codon": alt_codon,
            "ref_aa": ref_aa, "alt_aa": alt_aa,
            "protein_length_before": len(ref_prot.split("*")[0]),
            "protein_length_after": len(alt_prot.split("*")[0]),
            "frame_shift": shift != 0}

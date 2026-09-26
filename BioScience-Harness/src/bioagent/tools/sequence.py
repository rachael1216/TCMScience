"""Nucleotide sequence tools. Pure Python, deterministic, offline.

Every function takes keyword arguments and returns a JSON-serialisable dict, so it can be
a BioScience ``python`` component, run in a PSH child process, and have its output
schema-checked by the loop's evaluator. Input is validated and refused with a reason;
nothing is guessed from a malformed sequence.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

__all__ = ["reverse_complement", "transcribe", "translate", "gc_content", "find_orfs",
           "kmer_counts", "hamming_distance", "edit_distance", "codon_usage",
           "melting_temperature", "restriction_sites", "nucleic_acid_weight",
           "CODON_TABLE", "RESTRICTION_ENZYMES", "motif_search", "cpg_islands",
           "six_frame_translation", "crispr_guides", "sequence_entropy", "primer_check"]

_COMPLEMENT = str.maketrans("ACGTUNRYKMSWBDHVacgtunrykmswbdhv",
                            "TGCAANYRMKSWVHDBtgcaanyrmkswvhdb")
_VALID_NT = re.compile(r"^[ACGTUNRYKMSWBDHV]+$")

#: The standard genetic code (NCBI translation table 1). ``*`` is a stop.
CODON_TABLE: Mapping[str, str] = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L",
    "CTG": "L", "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V",
    "GTA": "V", "GTG": "V", "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P",
    "CCC": "P", "CCA": "P", "CCG": "P", "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A", "TAT": "Y", "TAC": "Y", "TAA": "*",
    "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q", "AAT": "N", "AAC": "N",
    "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E", "TGT": "C",
    "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G",
    "GGG": "G",
}

#: Recognition sites of common type II restriction enzymes (5'→3').
RESTRICTION_ENZYMES: Mapping[str, str] = {
    "EcoRI": "GAATTC", "BamHI": "GGATCC", "HindIII": "AAGCTT", "NotI": "GCGGCCGC",
    "XhoI": "CTCGAG", "PstI": "CTGCAG", "SmaI": "CCCGGG", "KpnI": "GGTACC", "SacI": "GAGCTC",
    "XbaI": "TCTAGA", "NcoI": "CCATGG", "NdeI": "CATATG", "BglII": "AGATCT", "SalI": "GTCGAC",
    "SpeI": "ACTAGT", "EcoRV": "GATATC", "ClaI": "ATCGAT", "NheI": "GCTAGC", "ApaI": "GGGCCC",
    "SphI": "GCATGC",
}


def _clean(sequence: Any, *, what: str = "sequence") -> str:
    if not isinstance(sequence, str):
        raise ValueError(f"{what} must be a string, got {type(sequence).__name__}")
    text = "".join(sequence.split()).upper()
    if not text:
        raise ValueError(f"{what} is empty")
    if not _VALID_NT.match(text):
        bad = sorted({c for c in text if c not in "ACGTUNRYKMSWBDHV"})
        raise ValueError(f"{what} contains characters that are not IUPAC nucleotides: {bad}")
    return text


def reverse_complement(sequence: str) -> dict[str, Any]:
    """Reverse complement of a DNA or RNA sequence (IUPAC ambiguity codes honoured)."""
    seq = _clean(sequence)
    return {"sequence": seq.translate(_COMPLEMENT)[::-1], "length": len(seq)}


def transcribe(sequence: str) -> dict[str, Any]:
    """DNA coding strand to mRNA (T → U)."""
    seq = _clean(sequence)
    return {"rna": seq.replace("T", "U"), "length": len(seq)}


def translate(sequence: str, frame: int = 0, to_stop: bool = False) -> dict[str, Any]:
    """Translate with the standard genetic code; ``X`` for a codon with ambiguity."""
    seq = _clean(sequence).replace("U", "T")
    if frame not in (0, 1, 2):
        raise ValueError("frame must be 0, 1 or 2")
    protein: list[str] = []
    stops = 0
    for i in range(frame, len(seq) - 2, 3):
        codon = seq[i:i + 3]
        aa = CODON_TABLE.get(codon, "X")
        if aa == "*":
            stops += 1
            if to_stop:
                break
        protein.append(aa)
    return {"protein": "".join(protein), "codons": len(protein), "stop_codons": stops,
            "frame": frame}


def gc_content(sequence: str, window: int = 0) -> dict[str, Any]:
    """GC fraction overall and, with ``window`` > 0, in sliding windows of that width."""
    seq = _clean(sequence)
    gc = sum(seq.count(b) for b in "GCS")
    out: dict[str, Any] = {"gc_fraction": round(gc / len(seq), 6),
                           "gc_percent": round(100.0 * gc / len(seq), 3), "length": len(seq)}
    if window > 0:
        if window > len(seq):
            raise ValueError("window is longer than the sequence")
        out["window"] = window
        out["windows"] = [round(sum(seq[i:i + window].count(b) for b in "GCS") / window, 4)
                          for i in range(0, len(seq) - window + 1)]
    return out


def find_orfs(sequence: str, min_length_aa: int = 30, both_strands: bool = True
              ) -> dict[str, Any]:
    """Open reading frames (ATG … stop) of at least ``min_length_aa`` codons, both strands."""
    seq = _clean(sequence).replace("U", "T")
    if min_length_aa < 1:
        raise ValueError("min_length_aa must be at least 1")
    strands = [("+", seq)]
    if both_strands:
        strands.append(("-", seq.translate(_COMPLEMENT)[::-1]))
    found: list[dict[str, Any]] = []
    for strand, s in strands:
        for frame in range(3):
            i = frame
            while i <= len(s) - 3:
                if s[i:i + 3] == "ATG":
                    j = i
                    protein = []
                    while j <= len(s) - 3:
                        aa = CODON_TABLE.get(s[j:j + 3], "X")
                        if aa == "*":
                            break
                        protein.append(aa)
                        j += 3
                    else:
                        j = None                           # ran off the end: no stop
                    if j is not None and len(protein) >= min_length_aa:
                        found.append({"strand": strand, "frame": frame, "start": i,
                                      "end": j + 3, "length_aa": len(protein),
                                      "protein": "".join(protein)})
                        i = j + 3
                        continue
                i += 3
    found.sort(key=lambda o: -o["length_aa"])
    return {"orfs": found, "count": len(found), "min_length_aa": min_length_aa}


def kmer_counts(sequence: str, k: int = 3, top: int = 20) -> dict[str, Any]:
    """Counts of k-mers, most frequent first."""
    seq = _clean(sequence)
    if k < 1 or k > len(seq):
        raise ValueError("k must be between 1 and the sequence length")
    counts: dict[str, int] = {}
    for i in range(len(seq) - k + 1):
        counts[seq[i:i + k]] = counts.get(seq[i:i + k], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"k": k, "total": len(seq) - k + 1, "distinct": len(counts),
            "top": [[kmer, n] for kmer, n in ranked[:max(top, 0)]]}


def hamming_distance(a: str, b: str) -> dict[str, Any]:
    """Mismatches between two equal-length sequences."""
    x, y = _clean(a, what="a"), _clean(b, what="b")
    if len(x) != len(y):
        raise ValueError(f"sequences differ in length ({len(x)} vs {len(y)})")
    d = sum(1 for p, q in zip(x, y) if p != q)
    return {"distance": d, "length": len(x), "identity": round(1 - d / len(x), 6)}


def edit_distance(a: str, b: str) -> dict[str, Any]:
    """Levenshtein distance between two strings (any alphabet)."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("a and b must be strings")
    x, y = a.strip().upper(), b.strip().upper()
    prev = list(range(len(y) + 1))
    for i, ca in enumerate(x, 1):
        cur = [i]
        for j, cb in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return {"distance": prev[-1], "length_a": len(x), "length_b": len(y)}


def codon_usage(sequence: str, frame: int = 0) -> dict[str, Any]:
    """Codon counts and per-amino-acid relative usage in one reading frame."""
    seq = _clean(sequence).replace("U", "T")
    if frame not in (0, 1, 2):
        raise ValueError("frame must be 0, 1 or 2")
    codons: dict[str, int] = {}
    for i in range(frame, len(seq) - 2, 3):
        c = seq[i:i + 3]
        if c in CODON_TABLE:
            codons[c] = codons.get(c, 0) + 1
    by_aa: dict[str, int] = {}
    for c, n in codons.items():
        by_aa[CODON_TABLE[c]] = by_aa.get(CODON_TABLE[c], 0) + n
    relative = {c: round(n / by_aa[CODON_TABLE[c]], 4) for c, n in codons.items()}
    return {"codons": dict(sorted(codons.items())), "amino_acids": dict(sorted(by_aa.items())),
            "relative_usage": dict(sorted(relative.items())), "total_codons": sum(codons.values())}


def melting_temperature(sequence: str, method: str = "auto", sodium_mM: float = 50.0
                        ) -> dict[str, Any]:
    """Primer melting temperature.

    ``wallace``: Tm = 2(A+T) + 4(G+C), the rule of thumb for oligos under 14 nt.
    ``salt_adjusted``: Tm = 81.5 + 16.6·log10([Na+]) + 0.41·%GC − 675/N (Howley et al.).
    ``auto`` picks Wallace below 14 nt and salt-adjusted otherwise.
    """
    seq = _clean(sequence).replace("U", "T")
    if any(c not in "ACGT" for c in seq):
        raise ValueError("melting temperature needs an unambiguous ACGT sequence")
    if sodium_mM <= 0:
        raise ValueError("sodium_mM must be positive")
    chosen = method
    if method == "auto":
        chosen = "wallace" if len(seq) < 14 else "salt_adjusted"
    at = seq.count("A") + seq.count("T")
    gc = seq.count("G") + seq.count("C")
    if chosen == "wallace":
        tm = 2.0 * at + 4.0 * gc
    elif chosen == "salt_adjusted":
        tm = (81.5 + 16.6 * math.log10(sodium_mM / 1000.0) + 0.41 * (100.0 * gc / len(seq))
              - 675.0 / len(seq))
    else:
        raise ValueError("method must be 'auto', 'wallace' or 'salt_adjusted'")
    return {"tm_celsius": round(tm, 2), "method": chosen, "length": len(seq),
            "gc_percent": round(100.0 * gc / len(seq), 2), "sodium_mM": sodium_mM}


def restriction_sites(sequence: str, enzymes: Sequence[str] | None = None) -> dict[str, Any]:
    """0-based positions of recognition sites for the named (or all known) enzymes."""
    seq = _clean(sequence).replace("U", "T")
    names = list(enzymes) if enzymes else list(RESTRICTION_ENZYMES)
    unknown = [n for n in names if n not in RESTRICTION_ENZYMES]
    if unknown:
        raise ValueError(f"unknown enzyme(s) {unknown}; known: {sorted(RESTRICTION_ENZYMES)}")
    sites: dict[str, list[int]] = {}
    for name in names:
        site = RESTRICTION_ENZYMES[name]
        positions = [m.start() for m in re.finditer(f"(?={re.escape(site)})", seq)]
        if positions:
            sites[name] = positions
    return {"sites": sites, "length": len(seq),
            "enzymes_with_sites": len(sites), "enzymes_checked": len(names)}


def nucleic_acid_weight(sequence: str, kind: str = "dna") -> dict[str, Any]:
    """Molecular weight (g/mol) of a single-stranded oligo, IDT's linear formulas."""
    seq = _clean(sequence)
    if kind == "dna":
        seq = seq.replace("U", "T")
        masses = {"A": 313.21, "C": 289.18, "G": 329.21, "T": 304.2}
        offset = -61.96
    elif kind == "rna":
        seq = seq.replace("T", "U")
        masses = {"A": 329.21, "C": 305.18, "G": 345.21, "U": 306.17}
        offset = 159.0
    else:
        raise ValueError("kind must be 'dna' or 'rna'")
    if any(c not in masses for c in seq):
        raise ValueError("molecular weight needs an unambiguous sequence")
    mw = sum(masses[c] for c in seq) + offset
    return {"molecular_weight": round(mw, 2), "kind": kind, "length": len(seq)}


# ---------------------------------------------------------------- motifs & scans

_IUPAC_REGEX: Mapping[str, str] = {
    "A": "A", "C": "C", "G": "G", "T": "T", "U": "T", "R": "[AG]", "Y": "[CT]", "K": "[GT]",
    "M": "[AC]", "S": "[CG]", "W": "[AT]", "B": "[CGT]", "D": "[AGT]", "H": "[ACT]",
    "V": "[ACG]", "N": "[ACGT]",
}


def _motif_pattern(motif: str) -> str:
    cleaned = _clean(motif, what="motif").replace("U", "T")
    return "".join(_IUPAC_REGEX[c] for c in cleaned)


def motif_search(sequence: str, motif: str, both_strands: bool = True, max_hits: int = 500
                 ) -> dict[str, Any]:
    """Find a motif written in IUPAC codes (``TATAWAW``, ``GGNNCC``) on one or both strands.
    Positions are 0-based on the forward strand; overlapping hits are reported."""
    seq = _clean(sequence).replace("U", "T")
    pattern = re.compile(f"(?=({_motif_pattern(motif)}))")
    width = len("".join(motif.split()))
    hits = [{"position": m.start(), "strand": "+", "match": m.group(1)}
            for m in pattern.finditer(seq)]
    if both_strands:
        rc = seq.translate(_COMPLEMENT)[::-1]
        for m in pattern.finditer(rc):
            hits.append({"position": len(seq) - m.start() - width, "strand": "-",
                         "match": m.group(1)})
    hits.sort(key=lambda h: (h["position"], h["strand"]))
    return {"motif": motif.upper(), "regex": pattern.pattern, "count": len(hits),
            "hits": hits[:max_hits], "truncated": len(hits) > max_hits}


def cpg_islands(sequence: str, window: int = 200, min_gc: float = 0.5,
                min_obs_exp: float = 0.6, min_length: int = 200) -> dict[str, Any]:
    """CpG islands by the Gardiner-Garden & Frommer (1987) criteria: windows with GC ≥ 50 %
    and observed/expected CpG ≥ 0.6, merged into islands of at least ``min_length`` bp."""
    seq = _clean(sequence).replace("U", "T")
    n = len(seq)
    if window < 10 or window > n:
        raise ValueError("window must be between 10 and the sequence length")
    c_pref, g_pref, cg_pref = [0], [0], [0]
    for i, base in enumerate(seq):
        c_pref.append(c_pref[-1] + (base == "C"))
        g_pref.append(g_pref[-1] + (base == "G"))
        cg_pref.append(cg_pref[-1] + (1 if base == "C" and i + 1 < n and seq[i + 1] == "G" else 0))
    islands: list[dict[str, Any]] = []
    current: list[int] | None = None
    for start in range(0, n - window + 1):
        end = start + window
        c = c_pref[end] - c_pref[start]
        g = g_pref[end] - g_pref[start]
        cg = cg_pref[end - 1] - cg_pref[start]          # CpG pairs fully inside the window
        gc = (c + g) / window
        obs_exp = cg * window / (c * g) if c * g > 0 else 0.0
        if gc >= min_gc and obs_exp >= min_obs_exp:
            if current is not None and start <= current[1]:
                current[1] = end
            else:
                if current is not None:
                    islands.append({"start": current[0], "end": current[1]})
                current = [start, end]
    if current is not None:
        islands.append({"start": current[0], "end": current[1]})
    out = []
    for island in islands:
        s, e = island["start"], island["end"]
        if e - s < min_length:
            continue
        c = c_pref[e] - c_pref[s]
        g = g_pref[e] - g_pref[s]
        cg = cg_pref[e - 1] - cg_pref[s]
        out.append({"start": s, "end": e, "length": e - s, "gc_fraction": round((c + g) / (e - s), 4),
                    "obs_exp_cpg": round(cg * (e - s) / (c * g), 4) if c * g > 0 else 0.0})
    return {"count": len(out), "islands": out, "window": window,
            "criteria": "GC >= 50 %, observed/expected CpG >= 0.6 (Gardiner-Garden & Frommer 1987)"}


def six_frame_translation(sequence: str) -> dict[str, Any]:
    """Translate all six reading frames and report the longest stop-free stretch."""
    seq = _clean(sequence).replace("U", "T")
    if any(c not in "ACGT" for c in seq):
        raise ValueError("six-frame translation needs an unambiguous ACGT sequence")
    rc = seq.translate(_COMPLEMENT)[::-1]
    frames: dict[str, str] = {}
    for f in range(3):
        frames[f"+{f + 1}"] = translate(seq, frame=f)["protein"]
        frames[f"-{f + 1}"] = translate(rc, frame=f)["protein"]
    best_frame, best_len = None, -1
    for name, prot in frames.items():
        longest = max((len(part) for part in prot.split("*")), default=0)
        if longest > best_len:
            best_frame, best_len = name, longest
    return {"frames": frames, "longest_open_stretch_frame": best_frame,
            "longest_open_stretch_aa": best_len}


def crispr_guides(sequence: str, pam: str = "NGG", guide_length: int = 20, max_guides: int = 100
                  ) -> dict[str, Any]:
    """Enumerate SpCas9-style guide candidates: ``guide_length`` bases 5′ of a PAM on either
    strand, with GC content and the poly-T flag. No on-target score is computed."""
    seq = _clean(sequence).replace("U", "T")
    if guide_length < 15 or guide_length > 30:
        raise ValueError("guide_length must be between 15 and 30")
    pattern = re.compile(f"(?=({_motif_pattern(pam)}))")
    width = len(pam)
    n = len(seq)
    guides = []

    def scan(strand_seq: str, strand: str) -> None:
        for m in pattern.finditer(strand_seq):
            p = m.start()
            if p < guide_length:
                continue
            guide = strand_seq[p - guide_length:p]
            if strand == "+":
                start, end = p - guide_length, p
            else:
                start, end = n - p, n - p + guide_length
            gc = 100.0 * sum(guide.count(b) for b in "GC") / guide_length
            guides.append({"strand": strand, "start": start, "end": end, "guide": guide,
                           "pam": m.group(1), "gc_percent": round(gc, 1),
                           "poly_t": "TTTT" in guide,
                           "gc_in_range": 40.0 <= gc <= 60.0})

    scan(seq, "+")
    scan(seq.translate(_COMPLEMENT)[::-1], "-")
    guides.sort(key=lambda g: (g["start"], g["strand"]))
    return {"pam": pam.upper(), "guide_length": guide_length, "count": len(guides),
            "guides": guides[:max_guides], "truncated": len(guides) > max_guides,
            "note": "candidates only; specificity and efficiency scoring need a genome index"}


def sequence_entropy(sequence: str, window: int = 0, low_complexity_threshold: float = 1.0
                     ) -> dict[str, Any]:
    """Shannon entropy (bits per base) of the whole sequence or of sliding windows, with the
    fraction of windows below a low-complexity threshold."""
    seq = _clean(sequence)

    def entropy(s: str) -> float:
        total = len(s)
        return abs(sum((s.count(b) / total) * math.log2(s.count(b) / total) for b in set(s)))

    if window <= 0:
        return {"entropy_bits": round(entropy(seq), 4), "length": len(seq),
                "max_possible_bits": round(math.log2(len(set(seq))), 4) if len(set(seq)) > 1 else 0.0}
    if window > len(seq):
        raise ValueError("window is longer than the sequence")
    step = max(1, window // 4)
    values = [entropy(seq[i:i + window]) for i in range(0, len(seq) - window + 1, step)]
    low = [i * step for i, v in enumerate(values) if v < low_complexity_threshold]
    return {"window": window, "step": step, "windows": len(values),
            "mean_entropy_bits": round(sum(values) / len(values), 4),
            "min_entropy_bits": round(min(values), 4), "max_entropy_bits": round(max(values), 4),
            "low_complexity_fraction": round(len(low) / len(values), 4),
            "low_complexity_window_starts": low[:50]}


def primer_check(primer: str, template: str = "") -> dict[str, Any]:
    """Primer sanity checks: length, GC, Tm, 3′ GC clamp, longest homopolymer run, the
    longest self-complementary stretch, a simple hairpin scan, and binding sites in an
    optional template (exact matches on both strands)."""
    p = _clean(primer, what="primer").replace("U", "T")
    if any(c not in "ACGT" for c in p):
        raise ValueError("primer must be an unambiguous ACGT sequence")
    n = len(p)
    gc = 100.0 * sum(p.count(b) for b in "GC") / n
    tm = melting_temperature(p)["tm_celsius"]
    clamp = sum(1 for b in p[-5:] if b in "GC")
    run, best_run = 1, 1
    for a, b in zip(p, p[1:]):
        run = run + 1 if a == b else 1
        best_run = max(best_run, run)
    rc = p.translate(_COMPLEMENT)[::-1]
    # Longest common substring of the primer and its reverse complement: the longest
    # stretch that can pair with another copy of the primer (self-dimer).
    longest = 0
    prev = [0] * (n + 1)
    for i in range(1, n + 1):
        cur = [0] * (n + 1)
        for j in range(1, n + 1):
            if p[i - 1] == rc[j - 1]:
                cur[j] = prev[j - 1] + 1
                longest = max(longest, cur[j])
        prev = cur
    hairpin = False
    for i in range(n - 4):
        stem_rc = p[i:i + 4].translate(_COMPLEMENT)[::-1]
        if stem_rc in p[i + 7:]:
            hairpin = True
            break
    warnings = []
    if n < 18 or n > 30:
        warnings.append("length outside 18-30 nt")
    if gc < 40 or gc > 60:
        warnings.append("GC outside 40-60 %")
    if clamp == 0:
        warnings.append("no G/C in the last five 3' bases")
    if clamp > 3:
        warnings.append("more than three G/C in the last five 3' bases")
    if best_run >= 5:
        warnings.append("homopolymer run of five or more")
    if longest >= 6:
        warnings.append("self-complementary stretch of six or more")
    if hairpin:
        warnings.append("possible hairpin (4-bp stem, loop >= 3)")
    out: dict[str, Any] = {"primer": p, "length": n, "gc_percent": round(gc, 1), "tm_celsius": tm,
                           "gc_clamp_3prime_count": clamp, "longest_homopolymer_run": best_run,
                           "longest_self_complementary_stretch": longest, "hairpin_possible": hairpin,
                           "warnings": warnings}
    if template:
        t = _clean(template, what="template").replace("U", "T")
        forward = [m.start() for m in re.finditer(f"(?={p})", t)]
        reverse = [m.start() for m in re.finditer(f"(?={rc})", t)]
        out["template_sites"] = {"forward_strand": forward, "reverse_strand": reverse,
                                 "unique": len(forward) + len(reverse) == 1}
    return out

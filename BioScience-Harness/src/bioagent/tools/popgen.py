"""Population genetics: linkage disequilibrium, nucleotide diversity with Tajima's D, F_ST."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["linkage_disequilibrium", "nucleotide_diversity", "fst"]


def linkage_disequilibrium(haplotype_counts: Mapping[str, int]) -> dict[str, Any]:
    """D, D′ and r² for two biallelic loci from haplotype counts keyed ``AB``, ``Ab``,
    ``aB``, ``ab`` (upper case = first allele at each locus)."""
    if not isinstance(haplotype_counts, Mapping):
        raise ValueError("haplotype_counts must be a mapping with keys AB, Ab, aB, ab")
    counts = {}
    for key in ("AB", "Ab", "aB", "ab"):
        v = haplotype_counts.get(key, 0)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"{key} must be a non-negative integer count")
        counts[key] = v
    n = sum(counts.values())
    if n == 0:
        raise ValueError("no haplotypes counted")
    p_a = (counts["AB"] + counts["Ab"]) / n
    p_b = (counts["AB"] + counts["aB"]) / n
    p_ab = counts["AB"] / n
    d = p_ab - p_a * p_b
    if d >= 0:
        d_max = min(p_a * (1 - p_b), (1 - p_a) * p_b)
    else:
        d_max = min(p_a * p_b, (1 - p_a) * (1 - p_b))
    denom = p_a * (1 - p_a) * p_b * (1 - p_b)
    d_prime = d / d_max if d_max > 0 else None
    r2 = d * d / denom if denom > 0 else None
    chi2 = n * r2 if r2 is not None else None
    return {"n_haplotypes": n, "p_A": round(p_a, 6), "p_B": round(p_b, 6),
            "D": round(d, 6), "D_prime": round(d_prime, 6) if d_prime is not None else None,
            "r_squared": round(r2, 6) if r2 is not None else None,
            "chi_square": round(chi2, 4) if chi2 is not None else None,
            "p_value": round(math.erfc(math.sqrt(chi2 / 2.0)), 8) if chi2 is not None else None}


def _aligned(sequences: Any) -> list[str]:
    values = list(sequences.values()) if isinstance(sequences, Mapping) else sequences
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        raise ValueError("at least two aligned sequences are needed")
    seqs = []
    for s in values:
        if not isinstance(s, str) or not s.strip():
            raise ValueError("every sequence must be a non-empty string")
        seqs.append("".join(s.split()).upper().replace("U", "T"))
    length = len(seqs[0])
    if any(len(s) != length for s in seqs):
        raise ValueError("sequences must be aligned to the same length")
    return seqs


def nucleotide_diversity(sequences: Sequence[str] | Mapping[str, str]) -> dict[str, Any]:
    """Segregating sites S, average pairwise differences π (per sequence pair and per site),
    Watterson's θ and Tajima's D (Tajima 1989). Columns with a gap or ambiguity code in any
    sequence are excluded."""
    seqs = _aligned(sequences)
    n, length = len(seqs), len(seqs[0])
    usable = [i for i in range(length) if all(s[i] in "ACGT" for s in seqs)]
    if not usable:
        raise ValueError("no columns are unambiguous in every sequence")
    segregating = sum(1 for i in usable if len({s[i] for s in seqs}) > 1)
    pairs = n * (n - 1) // 2
    total_diffs = 0
    for a in range(n):
        for b in range(a + 1, n):
            total_diffs += sum(1 for i in usable if seqs[a][i] != seqs[b][i])
    k = total_diffs / pairs
    a1 = sum(1.0 / i for i in range(1, n))
    a2 = sum(1.0 / (i * i) for i in range(1, n))
    b1 = (n + 1) / (3.0 * (n - 1))
    b2 = 2.0 * (n * n + n + 3) / (9.0 * n * (n - 1))
    c1 = b1 - 1.0 / a1
    c2 = b2 - (n + 2) / (a1 * n) + a2 / (a1 * a1)
    e1 = c1 / a1
    e2 = c2 / (a1 * a1 + a2)
    theta_w = segregating / a1
    tajima = None
    if segregating > 0:
        denom = math.sqrt(e1 * segregating + e2 * segregating * (segregating - 1))
        tajima = (k - theta_w) / denom if denom > 0 else None
    return {"n_sequences": n, "sites_used": len(usable), "segregating_sites": segregating,
            "pi_per_pair": round(k, 6), "pi_per_site": round(k / len(usable), 6),
            "theta_w_per_sequence": round(theta_w, 6),
            "theta_w_per_site": round(theta_w / len(usable), 6),
            "tajimas_d": round(tajima, 4) if tajima is not None else None,
            "note": "Tajima's D is undefined without segregating sites; |D| > 2 is the usual "
                    "informal threshold, the exact distribution depends on n"}


def fst(allele_frequencies: Sequence[float], sample_sizes: Sequence[int] | None = None
        ) -> dict[str, Any]:
    """Population differentiation for one biallelic locus: Nei's G_ST = (H_T − H_S)/H_T over
    any number of populations, and for exactly two populations with sample sizes, Hudson's
    F_ST = 1 − H_w/H_b with the sample-size-corrected within-population heterozygosity."""
    if not isinstance(allele_frequencies, (list, tuple)) or len(allele_frequencies) < 2:
        raise ValueError("allele_frequencies must list one frequency per population (≥ 2)")
    p = []
    for v in allele_frequencies:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError("every allele frequency must lie in [0, 1]")
        p.append(float(v))
    hs = sum(2 * x * (1 - x) for x in p) / len(p)
    pbar = sum(p) / len(p)
    ht = 2 * pbar * (1 - pbar)
    gst = (ht - hs) / ht if ht > 0 else None
    out: dict[str, Any] = {"populations": len(p), "H_S": round(hs, 6), "H_T": round(ht, 6),
                           "G_ST": round(gst, 6) if gst is not None else None}
    if sample_sizes is not None:
        if not isinstance(sample_sizes, (list, tuple)) or len(sample_sizes) != len(p):
            raise ValueError("sample_sizes must match allele_frequencies")
        sizes = []
        for v in sample_sizes:
            if isinstance(v, bool) or not isinstance(v, int) or v < 2:
                raise ValueError("every sample size must be an integer ≥ 2 (allele copies)")
            sizes.append(v)
        if len(p) == 2:
            hw = sum(2 * x * (1 - x) * m / (m - 1) for x, m in zip(p, sizes)) / 2
            hb = p[0] * (1 - p[1]) + p[1] * (1 - p[0])
            out["hudson_fst"] = round(1 - hw / hb, 6) if hb > 0 else None
        else:
            out["hudson_fst"] = None
            out["note"] = "Hudson's estimator is defined here for two populations"
    return out

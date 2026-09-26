"""Protein sequence tools: mass, pI, hydropathy, composition, extinction coefficient."""

from __future__ import annotations

import re
from typing import Any, Sequence

__all__ = ["protein_properties", "hydropathy_profile", "AVERAGE_RESIDUE_MASS",
           "KYTE_DOOLITTLE", "PKA_EMBOSS", "peptide_mass", "in_silico_digest",
           "MONOISOTOPIC_RESIDUE_MASS", "PROTEASES"]

#: Average residue masses (Da), the values Expasy's ProtParam uses.
AVERAGE_RESIDUE_MASS = {
    "A": 71.0788, "R": 156.1875, "N": 114.1038, "D": 115.0886, "C": 103.1388, "E": 129.1155,
    "Q": 128.1307, "G": 57.0519, "H": 137.1411, "I": 113.1594, "L": 113.1594, "K": 128.1741,
    "M": 131.1926, "F": 147.1766, "P": 97.1167, "S": 87.0782, "T": 101.1051, "W": 186.2132,
    "Y": 163.1760, "V": 99.1326,
}
_WATER = 18.01524

#: Kyte & Doolittle (1982) hydropathy index.
KYTE_DOOLITTLE = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5, "G": -0.4,
    "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6, "S": -0.8,
    "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}

#: pKa values used by EMBOSS iep.
PKA_EMBOSS = {"N_term": 8.6, "C_term": 3.6, "C": 8.5, "D": 3.9, "E": 4.1, "H": 6.5,
              "K": 10.8, "R": 12.5, "Y": 10.1}

_VALID_AA = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]+$")


def _clean(sequence: Any) -> str:
    if not isinstance(sequence, str):
        raise ValueError(f"sequence must be a string, got {type(sequence).__name__}")
    text = "".join(sequence.split()).upper().rstrip("*")
    if not text:
        raise ValueError("sequence is empty")
    if not _VALID_AA.match(text):
        bad = sorted({c for c in text if c not in AVERAGE_RESIDUE_MASS})
        raise ValueError(f"sequence contains non-standard amino acid codes: {bad}")
    return text


def _charge_at(seq: str, ph: float) -> float:
    positive = 10 ** (PKA_EMBOSS["N_term"] - ph)
    charge = positive / (1 + positive)
    for aa, pka in (("K", PKA_EMBOSS["K"]), ("R", PKA_EMBOSS["R"]), ("H", PKA_EMBOSS["H"])):
        n = seq.count(aa)
        if n:
            ratio = 10 ** (pka - ph)
            charge += n * ratio / (1 + ratio)
    negative = 10 ** (ph - PKA_EMBOSS["C_term"])
    charge -= negative / (1 + negative)
    for aa, pka in (("D", PKA_EMBOSS["D"]), ("E", PKA_EMBOSS["E"]), ("C", PKA_EMBOSS["C"]),
                    ("Y", PKA_EMBOSS["Y"])):
        n = seq.count(aa)
        if n:
            ratio = 10 ** (ph - pka)
            charge -= n * ratio / (1 + ratio)
    return charge


def protein_properties(sequence: str) -> dict[str, Any]:
    """Length, average mass, pI (EMBOSS pKa set, bisection), GRAVY, composition,
    aromaticity and the Pace/Expasy molar extinction coefficient at 280 nm."""
    seq = _clean(sequence)
    n = len(seq)
    mass = sum(AVERAGE_RESIDUE_MASS[a] for a in seq) + _WATER
    low, high = 0.0, 14.0
    for _ in range(60):
        mid = (low + high) / 2
        if _charge_at(seq, mid) > 0:
            low = mid
        else:
            high = mid
    pi = (low + high) / 2
    composition = {a: seq.count(a) for a in sorted(set(seq))}
    gravy = sum(KYTE_DOOLITTLE[a] for a in seq) / n
    w, y, c = seq.count("W"), seq.count("Y"), seq.count("C")
    return {
        "length": n, "molecular_weight": round(mass, 2), "isoelectric_point": round(pi, 2),
        "gravy": round(gravy, 4), "aromaticity": round((w + y + seq.count("F")) / n, 4),
        "composition": composition,
        "extinction_coefficient_280nm": {"reduced": 5500 * w + 1490 * y,
                                         "cystines": 5500 * w + 1490 * y + 125 * (c // 2)},
        "net_charge_ph7": round(_charge_at(seq, 7.0), 3),
    }


def hydropathy_profile(sequence: str, window: int = 9) -> dict[str, Any]:
    """Kyte–Doolittle hydropathy averaged over a sliding window (odd width)."""
    seq = _clean(sequence)
    if window < 1 or window % 2 == 0:
        raise ValueError("window must be an odd positive integer")
    if window > len(seq):
        raise ValueError("window is longer than the sequence")
    values = [KYTE_DOOLITTLE[a] for a in seq]
    half = window // 2
    profile = [round(sum(values[i - half:i + half + 1]) / window, 3)
               for i in range(half, len(seq) - half)]
    peak = max(range(len(profile)), key=lambda i: profile[i]) if profile else 0
    return {"window": window, "profile": profile, "max": max(profile), "min": min(profile),
            "max_centre_position": peak + half + 1,
            "putative_transmembrane_segments": sum(1 for v in profile if v > 1.6)}


# ------------------------------------------------------------- mass spectrometry

#: Monoisotopic residue masses (Da), the values used for peptide m/z.
MONOISOTOPIC_RESIDUE_MASS = {
    "A": 71.03711, "R": 156.10111, "N": 114.04293, "D": 115.02694, "C": 103.00919,
    "E": 129.04259, "Q": 128.05858, "G": 57.02146, "H": 137.05891, "I": 113.08406,
    "L": 113.08406, "K": 128.09496, "M": 131.04049, "F": 147.06841, "P": 97.05276,
    "S": 87.03203, "T": 101.04768, "W": 186.07931, "Y": 163.06333, "V": 99.06841,
}
_WATER_MONO = 18.010565
_PROTON = 1.007276

#: Cleavage rules: (residues cut after, residues that block when following) or, for
#: Asp-N, cut *before* the residue.
PROTEASES = {
    "trypsin": ("KR", "P", "after"), "trypsin/p": ("KR", "", "after"),
    "lys-c": ("K", "", "after"), "arg-c": ("R", "", "after"),
    "chymotrypsin": ("FWY", "P", "after"), "glu-c": ("E", "", "after"),
    "asp-n": ("D", "", "before"),
}


def peptide_mass(sequence: str, charges: Sequence[int] = (1, 2, 3)) -> dict[str, Any]:
    """Monoisotopic and average mass of a peptide, and m/z for the requested charge states."""
    seq = _clean(sequence)
    mono = sum(MONOISOTOPIC_RESIDUE_MASS[a] for a in seq) + _WATER_MONO
    average = sum(AVERAGE_RESIDUE_MASS[a] for a in seq) + _WATER
    if not isinstance(charges, (list, tuple)) or not charges:
        raise ValueError("charges must be a non-empty list of positive integers")
    mz = {}
    for z in charges:
        if isinstance(z, bool) or not isinstance(z, int) or z < 1:
            raise ValueError("charges must be positive integers")
        mz[str(z)] = round((mono + z * _PROTON) / z, 5)
    return {"sequence": seq, "length": len(seq), "monoisotopic_mass": round(mono, 5),
            "average_mass": round(average, 4), "mz": mz}


def in_silico_digest(sequence: str, enzyme: str = "trypsin", missed_cleavages: int = 0,
                     min_length: int = 1) -> dict[str, Any]:
    """Proteolytic digestion by rule (trypsin cuts after K/R unless P follows, and so on),
    with up to ``missed_cleavages`` skipped sites, and monoisotopic masses per peptide."""
    seq = _clean(sequence)
    name = str(enzyme).strip().lower()
    if name not in PROTEASES:
        raise ValueError(f"unknown enzyme {name!r}; known: {sorted(PROTEASES)}")
    if not isinstance(missed_cleavages, int) or missed_cleavages < 0 or missed_cleavages > 5:
        raise ValueError("missed_cleavages must be an integer between 0 and 5")
    residues, blockers, side = PROTEASES[name]
    cuts = []
    for i in range(len(seq) - 1):
        if side == "after" and seq[i] in residues and seq[i + 1] not in blockers:
            cuts.append(i + 1)
        elif side == "before" and seq[i + 1] in residues:
            cuts.append(i + 1)
    bounds = [0] + cuts + [len(seq)]
    peptides = []
    for start_idx in range(len(bounds) - 1):
        for skipped in range(missed_cleavages + 1):
            end_idx = start_idx + 1 + skipped
            if end_idx >= len(bounds):
                break
            start, end = bounds[start_idx], bounds[end_idx]
            pep = seq[start:end]
            if len(pep) < min_length:
                continue
            mono = sum(MONOISOTOPIC_RESIDUE_MASS[a] for a in pep) + _WATER_MONO
            peptides.append({"sequence": pep, "start": start + 1, "end": end,
                             "missed_cleavages": skipped, "monoisotopic_mass": round(mono, 4)})
    return {"enzyme": name, "cleavage_sites": len(cuts), "count": len(peptides),
            "peptides": peptides, "rule": {"after": residues if side == "after" else "",
                                           "before": residues if side == "before" else "",
                                           "blocked_by": blockers}}

"""Pharmacokinetics and dosing calculators. Deterministic, offline, the formula named on each.

Every function validates its inputs and refuses implausible values with a reason. None of
them is a substitute for a pharmacist: they compute the textbook quantity from the
declared model and say which model that is.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .clinical import _num, body_surface_area

__all__ = ["pk_one_compartment", "half_life_from_levels", "loading_dose", "maintenance_dose",
           "steady_state", "carboplatin_calvert", "glucocorticoid_equivalent",
           "morphine_milligram_equivalents", "bsa_dose", "GLUCOCORTICOID_EQUIVALENT_MG",
           "MME_CONVERSION_FACTORS"]

_LN2 = math.log(2.0)


def pk_one_compartment(dose_mg: float, volume_l: float, half_life_h: float,
                       times_h: Sequence[float] = (0, 1, 2, 4, 8, 12, 24),
                       bioavailability: float = 1.0) -> dict[str, Any]:
    """One-compartment model with first-order elimination after a bolus:
    C(t) = F·D/V · e^(−k·t), k = ln2 / t½, CL = k·V, AUC(0→∞) = C0 / k."""
    dose = _num(dose_mg, "dose_mg", 0, 1e6, allow_zero=False)
    v = _num(volume_l, "volume_l", 0, 1e5, allow_zero=False)
    t_half = _num(half_life_h, "half_life_h", 0, 1e5, allow_zero=False)
    f = _num(bioavailability, "bioavailability", 0, 1, allow_zero=False)
    if not isinstance(times_h, (list, tuple)) or not times_h:
        raise ValueError("times_h must be a non-empty list of times in hours")
    times = [_num(t, "times_h", 0, 1e6) for t in times_h]
    k = _LN2 / t_half
    c0 = f * dose / v
    curve = [{"time_h": t, "concentration_mg_per_l": round(c0 * math.exp(-k * t), 6)}
             for t in times]
    return {"model": "one-compartment, first-order elimination, instantaneous input",
            "k_per_h": round(k, 6), "half_life_h": t_half, "c0_mg_per_l": round(c0, 6),
            "clearance_l_per_h": round(k * v, 6), "auc_0_inf_mg_h_per_l": round(c0 / k, 6),
            "concentrations": curve}


def half_life_from_levels(c1: float, t1_h: float, c2: float, t2_h: float) -> dict[str, Any]:
    """Elimination rate and half-life from two post-distribution levels:
    k = ln(C1/C2) / (t2 − t1), t½ = ln2 / k."""
    a = _num(c1, "c1", 0, 1e6, allow_zero=False)
    b = _num(c2, "c2", 0, 1e6, allow_zero=False)
    t1 = _num(t1_h, "t1_h", 0, 1e6)
    t2 = _num(t2_h, "t2_h", 0, 1e6)
    if t2 <= t1:
        raise ValueError("t2_h must be later than t1_h")
    if b >= a:
        raise ValueError("the second level must be lower than the first for elimination")
    k = math.log(a / b) / (t2 - t1)
    return {"k_per_h": round(k, 6), "half_life_h": round(_LN2 / k, 4),
            "hours_between_samples": t2 - t1}


def loading_dose(target_concentration_mg_per_l: float, volume_of_distribution_l_per_kg: float,
                 weight_kg: float, bioavailability: float = 1.0) -> dict[str, Any]:
    """Loading dose = Vd · C_target / F."""
    ct = _num(target_concentration_mg_per_l, "target_concentration_mg_per_l", 0, 1e4,
              allow_zero=False)
    vd = _num(volume_of_distribution_l_per_kg, "volume_of_distribution_l_per_kg", 0, 100,
              allow_zero=False)
    w = _num(weight_kg, "weight_kg", 0.3, 700, allow_zero=False)
    f = _num(bioavailability, "bioavailability", 0, 1, allow_zero=False)
    total_vd = vd * w
    return {"loading_dose_mg": round(total_vd * ct / f, 3), "volume_of_distribution_l": round(total_vd, 3),
            "formula": "Vd × C_target / F"}


def maintenance_dose(target_css_mg_per_l: float, clearance_l_per_h: float, interval_h: float,
                     bioavailability: float = 1.0) -> dict[str, Any]:
    """Maintenance dose per interval = CL · Css · τ / F; the equivalent infusion rate is CL · Css."""
    css = _num(target_css_mg_per_l, "target_css_mg_per_l", 0, 1e4, allow_zero=False)
    cl = _num(clearance_l_per_h, "clearance_l_per_h", 0, 1e4, allow_zero=False)
    tau = _num(interval_h, "interval_h", 0, 24 * 14, allow_zero=False)
    f = _num(bioavailability, "bioavailability", 0, 1, allow_zero=False)
    return {"dose_per_interval_mg": round(cl * css * tau / f, 3),
            "infusion_rate_mg_per_h": round(cl * css, 4), "interval_h": tau,
            "formula": "CL × Css × τ / F"}


def steady_state(half_life_h: float, interval_h: float) -> dict[str, Any]:
    """Accumulation ratio R = 1 / (1 − e^(−kτ)), the fraction of steady state reached after
    n doses (1 − e^(−nkτ)), and the time to 90 % and 97 % of steady state (3.32 and 5 t½)."""
    t_half = _num(half_life_h, "half_life_h", 0, 1e5, allow_zero=False)
    tau = _num(interval_h, "interval_h", 0, 24 * 14, allow_zero=False)
    k = _LN2 / t_half
    r = 1.0 / (1.0 - math.exp(-k * tau))
    fractions = {n: round(1.0 - math.exp(-n * k * tau), 4) for n in (1, 2, 3, 4, 5, 7, 10)}
    doses_to_90 = math.ceil(-math.log(0.1) / (k * tau))
    return {"k_per_h": round(k, 6), "accumulation_ratio": round(r, 4),
            "fraction_of_steady_state_after_doses": fractions,
            "doses_to_90_percent": doses_to_90,
            "time_to_90_percent_h": round(3.32 * t_half, 2),
            "time_to_97_percent_h": round(5.0 * t_half, 2)}


def carboplatin_calvert(target_auc_mg_ml_min: float, gfr_ml_min: float,
                        cap_gfr_at: float | None = 125.0) -> dict[str, Any]:
    """Calvert formula: dose (mg) = target AUC × (GFR + 25). Many protocols cap the GFR
    used in the formula (commonly 125 mL/min); the cap applied is reported."""
    auc = _num(target_auc_mg_ml_min, "target_auc_mg_ml_min", 0, 10, allow_zero=False)
    gfr = _num(gfr_ml_min, "gfr_ml_min", 0, 400)
    used = gfr
    capped = False
    if cap_gfr_at is not None:
        cap = _num(cap_gfr_at, "cap_gfr_at", 1, 400)
        if gfr > cap:
            used, capped = cap, True
    return {"dose_mg": round(auc * (used + 25.0), 1), "gfr_used_ml_min": used,
            "gfr_capped": capped, "formula": "AUC × (GFR + 25)"}


#: Anti-inflammatory equivalent doses (mg), the standard corticosteroid comparison table.
GLUCOCORTICOID_EQUIVALENT_MG: Mapping[str, float] = {
    "hydrocortisone": 20.0, "cortisone": 25.0, "prednisone": 5.0, "prednisolone": 5.0,
    "methylprednisolone": 4.0, "triamcinolone": 4.0, "dexamethasone": 0.75,
    "betamethasone": 0.6,
}


def glucocorticoid_equivalent(drug: str, dose_mg: float, to_drug: str = "prednisone"
                              ) -> dict[str, Any]:
    """Convert a glucocorticoid dose by the anti-inflammatory equivalence table."""
    src, dst = str(drug).strip().lower(), str(to_drug).strip().lower()
    for name in (src, dst):
        if name not in GLUCOCORTICOID_EQUIVALENT_MG:
            raise ValueError(f"unknown glucocorticoid {name!r}; known: "
                             f"{sorted(GLUCOCORTICOID_EQUIVALENT_MG)}")
    dose = _num(dose_mg, "dose_mg", 0, 1e4, allow_zero=False)
    factor = GLUCOCORTICOID_EQUIVALENT_MG[dst] / GLUCOCORTICOID_EQUIVALENT_MG[src]
    return {"from": src, "to": dst, "dose_mg": dose, "equivalent_dose_mg": round(dose * factor, 3),
            "hydrocortisone_equivalent_mg": round(dose * 20.0 / GLUCOCORTICOID_EQUIVALENT_MG[src], 3),
            "note": "anti-inflammatory equivalence; mineralocorticoid activity differs"}


#: Morphine milligram equivalent conversion factors (CDC 2022 opioid prescribing guideline).
#: Fentanyl is in micrograms per hour (transdermal); everything else in mg per day.
MME_CONVERSION_FACTORS: Mapping[str, float] = {
    "codeine": 0.15, "fentanyl_transdermal_mcg_per_h": 2.4, "hydrocodone": 1.0,
    "hydromorphone": 5.0, "methadone": 4.7, "morphine": 1.0, "oxycodone": 1.5,
    "oxymorphone": 3.0, "tapentadol": 0.4, "tramadol": 0.2,
}


def morphine_milligram_equivalents(regimen: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Total daily MME for a list of ``{"opioid": name, "dose_per_day": mg}`` entries
    (fentanyl transdermal in mcg/h). Buprenorphine is not converted, by the guideline."""
    if not isinstance(regimen, (list, tuple)) or not regimen:
        raise ValueError("regimen must be a non-empty list of {opioid, dose_per_day}")
    total = 0.0
    lines = []
    for entry in regimen:
        if not isinstance(entry, Mapping):
            raise ValueError("each regimen entry must be a mapping")
        name = str(entry.get("opioid", "")).strip().lower()
        if name not in MME_CONVERSION_FACTORS:
            raise ValueError(f"no conversion factor for {name!r}; known: "
                             f"{sorted(MME_CONVERSION_FACTORS)}")
        dose = _num(entry.get("dose_per_day"), "dose_per_day", 0, 1e5)
        mme = dose * MME_CONVERSION_FACTORS[name]
        total += mme
        lines.append({"opioid": name, "dose_per_day": dose,
                      "factor": MME_CONVERSION_FACTORS[name], "mme_per_day": round(mme, 2)})
    band = ("below 50 MME/day" if total < 50 else
            "50 to 89 MME/day: increased overdose risk" if total < 90 else
            "90 MME/day or more: high overdose risk")
    return {"total_mme_per_day": round(total, 2), "band": band, "components": lines,
            "source": "CDC Clinical Practice Guideline for Prescribing Opioids, 2022"}


def bsa_dose(dose_mg_per_m2: float, weight_kg: float, height_cm: float,
             cap_bsa_m2: float | None = None) -> dict[str, Any]:
    """Body-surface-area dosing: dose = mg/m² × BSA (Mosteller), with an optional BSA cap."""
    per_m2 = _num(dose_mg_per_m2, "dose_mg_per_m2", 0, 1e5, allow_zero=False)
    bsa = body_surface_area(weight_kg, height_cm)["mosteller_m2"]
    used, capped = bsa, False
    if cap_bsa_m2 is not None:
        cap = _num(cap_bsa_m2, "cap_bsa_m2", 0.1, 5, allow_zero=False)
        if bsa > cap:
            used, capped = cap, True
    return {"dose_mg": round(per_m2 * used, 2), "bsa_m2": bsa, "bsa_used_m2": used,
            "bsa_capped": capped}

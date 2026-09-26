"""Clinical calculators. Deterministic, offline, and the formula is named on each one.

These run on patient data by design, which is the case the label model exists for: a
calculator is a ``LOCAL_COMPUTE`` component with the local ceiling, so a PHI payload
reaches it in process and the same payload is refused at any public connector. Every
function validates its inputs and refuses out-of-range values with a reason; none of
them is a substitute for clinical judgement, and each returns the interpretation bands
its source publishes rather than a recommendation.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["bmi", "body_surface_area", "ideal_body_weight", "egfr_ckd_epi_2021",
           "creatinine_clearance_cockcroft_gault", "corrected_calcium", "anion_gap",
           "corrected_sodium", "qtc", "mean_arterial_pressure", "cha2ds2_vasc", "has_bled",
           "wells_dvt", "wells_pe", "curb65", "meld_na", "child_pugh", "news2",
           "glasgow_coma_scale", "qsofa", "fractional_excretion_sodium",
           "henderson_hasselbalch", "alveolar_gas", "friedewald_ldl", "hba1c_to_eag",
           "basal_metabolic_rate", "parkland_formula", "weight_based_dose",
           "convert_units", "tidal_volume", "UNIT_FACTORS", "phq9", "gad7", "apgar",
           "bishop_score", "gestational_age",
           "ascvd_pooled_cohort", "sofa", "calculated_osmolality", "winters_formula", "acid_base_interpretation", "holliday_segar", "free_water_deficit", "allowable_blood_loss", "infusion_rate", "heart_score", "centor_mcisaac", "alvarado", "timi_ua_nstemi", "abcd2", "sirs", "rcri", "stop_bang", "fib4", "apri", "homa_ir"]


def _num(value: Any, name: str, lo: float | None = None, hi: float | None = None,
         *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"{name} must be finite")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise ValueError(f"{name}={v} is outside the plausible range [{lo}, {hi}]")
    if not allow_zero and v == 0:
        raise ValueError(f"{name} must not be zero")
    return v


def _sex(value: Any) -> str:
    s = str(value).strip().lower()
    if s in ("f", "female", "woman", "w"):
        return "female"
    if s in ("m", "male", "man"):
        return "male"
    raise ValueError("sex must be 'female' or 'male'")


def _flag(value: Any, name: str) -> int:
    if isinstance(value, bool) or value in (0, 1):
        return int(bool(value))
    raise ValueError(f"{name} must be true/false")


# --------------------------------------------------------------- anthropometry

def bmi(weight_kg: float, height_cm: float) -> dict[str, Any]:
    """Body mass index (kg/m²) with WHO adult categories."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272) / 100.0
    value = w / (h * h)
    band = ("underweight" if value < 18.5 else "normal" if value < 25 else
            "overweight" if value < 30 else "obese")
    return {"bmi": round(value, 2), "category": band}


def body_surface_area(weight_kg: float, height_cm: float) -> dict[str, Any]:
    """BSA (m²) by Mosteller and by Du Bois & Du Bois."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272)
    return {"mosteller_m2": round(math.sqrt(h * w / 3600.0), 3),
            "dubois_m2": round(0.007184 * (w ** 0.425) * (h ** 0.725), 3)}


def ideal_body_weight(height_cm: float, sex: str, actual_weight_kg: float | None = None
                      ) -> dict[str, Any]:
    """Devine ideal body weight; adjusted body weight when an actual weight is given."""
    h_in = _num(height_cm, "height_cm", 100, 272) / 2.54
    s = _sex(sex)
    base = 50.0 if s == "male" else 45.5
    ibw = base + 2.3 * max(0.0, h_in - 60.0)
    out: dict[str, Any] = {"ideal_body_weight_kg": round(ibw, 1), "sex": s, "method": "Devine"}
    if actual_weight_kg is not None:
        actual = _num(actual_weight_kg, "actual_weight_kg", 1, 500)
        out["adjusted_body_weight_kg"] = round(ibw + 0.4 * (actual - ibw), 1)
        out["percent_of_ideal"] = round(100.0 * actual / ibw, 1)
    return out


# --------------------------------------------------------------------- renal

def egfr_ckd_epi_2021(creatinine_mg_dl: float, age_years: float, sex: str) -> dict[str, Any]:
    """CKD-EPI 2021 creatinine equation (race-free), mL/min/1.73 m², with KDIGO G stage."""
    scr = _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)
    age = _num(age_years, "age_years", 18, 120)
    s = _sex(sex)
    kappa, alpha = (0.7, -0.241) if s == "female" else (0.9, -0.302)
    ratio = scr / kappa
    egfr = 142 * min(ratio, 1.0) ** alpha * max(ratio, 1.0) ** -1.200 * 0.9938 ** age
    if s == "female":
        egfr *= 1.012
    stage = ("G1" if egfr >= 90 else "G2" if egfr >= 60 else "G3a" if egfr >= 45 else
             "G3b" if egfr >= 30 else "G4" if egfr >= 15 else "G5")
    return {"egfr_ml_min_1_73m2": round(egfr, 1), "kdigo_stage": stage,
            "equation": "CKD-EPI 2021 creatinine"}


def creatinine_clearance_cockcroft_gault(creatinine_mg_dl: float, age_years: float,
                                         weight_kg: float, sex: str) -> dict[str, Any]:
    """Cockcroft–Gault creatinine clearance (mL/min)."""
    scr = _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)
    age = _num(age_years, "age_years", 18, 120)
    w = _num(weight_kg, "weight_kg", 1, 500)
    s = _sex(sex)
    crcl = (140 - age) * w / (72 * scr) * (0.85 if s == "female" else 1.0)
    return {"creatinine_clearance_ml_min": round(crcl, 1), "equation": "Cockcroft-Gault"}


def fractional_excretion_sodium(urine_sodium: float, plasma_sodium: float,
                                urine_creatinine: float, plasma_creatinine: float
                                ) -> dict[str, Any]:
    """FENa (%) = (UNa × PCr) / (PNa × UCr) × 100; same units within each pair."""
    una = _num(urine_sodium, "urine_sodium", 0, 500)
    pna = _num(plasma_sodium, "plasma_sodium", 100, 200)
    ucr = _num(urine_creatinine, "urine_creatinine", 0.1, 1000, allow_zero=False)
    pcr = _num(plasma_creatinine, "plasma_creatinine", 0.1, 30)
    fena = (una * pcr) / (pna * ucr) * 100.0
    return {"fena_percent": round(fena, 2),
            "interpretation": "prerenal pattern (<1%)" if fena < 1 else
            "intrinsic pattern (>2%)" if fena > 2 else "indeterminate (1-2%)"}


# ---------------------------------------------------------------- chemistry

def corrected_calcium(calcium_mg_dl: float, albumin_g_dl: float) -> dict[str, Any]:
    """Albumin-corrected calcium: Ca + 0.8 × (4.0 − albumin)."""
    ca = _num(calcium_mg_dl, "calcium_mg_dl", 2, 20)
    alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
    return {"corrected_calcium_mg_dl": round(ca + 0.8 * (4.0 - alb), 2)}


def anion_gap(sodium: float, chloride: float, bicarbonate: float, potassium: float | None = None,
              albumin_g_dl: float | None = None) -> dict[str, Any]:
    """Anion gap (mmol/L), optionally with potassium and albumin correction."""
    na = _num(sodium, "sodium", 100, 200)
    cl = _num(chloride, "chloride", 50, 150)
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    gap = na - (cl + hco3)
    out: dict[str, Any] = {"anion_gap": round(gap, 1), "includes_potassium": False}
    if potassium is not None:
        gap += _num(potassium, "potassium", 1, 10)
        out.update({"anion_gap": round(gap, 1), "includes_potassium": True})
    if albumin_g_dl is not None:
        alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
        out["albumin_corrected_gap"] = round(gap + 2.5 * (4.0 - alb), 1)
    return out


def corrected_sodium(sodium: float, glucose_mg_dl: float, method: str = "katz") -> dict[str, Any]:
    """Sodium corrected for hyperglycaemia: Katz 1.6 or Hillier 2.4 mmol/L per 100 mg/dL."""
    na = _num(sodium, "sodium", 100, 200)
    glu = _num(glucose_mg_dl, "glucose_mg_dl", 10, 3000)
    factor = {"katz": 1.6, "hillier": 2.4}.get(method.lower())
    if factor is None:
        raise ValueError("method must be 'katz' or 'hillier'")
    return {"corrected_sodium": round(na + factor * (glu - 100.0) / 100.0, 1), "method": method}


def henderson_hasselbalch(bicarbonate: float, pco2_mmHg: float) -> dict[str, Any]:
    """pH = 6.1 + log10(HCO3⁻ / (0.03 × pCO2))."""
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    pco2 = _num(pco2_mmHg, "pco2_mmHg", 5, 200)
    return {"ph": round(6.1 + math.log10(hco3 / (0.03 * pco2)), 3)}


def alveolar_gas(fio2: float, paco2_mmHg: float, pao2_mmHg: float | None = None,
                 age_years: float | None = None, patm_mmHg: float = 760.0,
                 respiratory_quotient: float = 0.8) -> dict[str, Any]:
    """Alveolar gas equation PAO2 = FiO2 × (Patm − 47) − PaCO2 / RQ, and the A–a gradient."""
    f = _num(fio2, "fio2", 0.21, 1.0)
    paco2 = _num(paco2_mmHg, "paco2_mmHg", 5, 200)
    patm = _num(patm_mmHg, "patm_mmHg", 300, 800)
    rq = _num(respiratory_quotient, "respiratory_quotient", 0.5, 1.2)
    pao2_alv = f * (patm - 47.0) - paco2 / rq
    out: dict[str, Any] = {"alveolar_po2_mmHg": round(pao2_alv, 1)}
    if pao2_mmHg is not None:
        arterial = _num(pao2_mmHg, "pao2_mmHg", 10, 700)
        out["aa_gradient_mmHg"] = round(pao2_alv - arterial, 1)
        if age_years is not None:
            out["expected_gradient_for_age"] = round(_num(age_years, "age_years", 0, 120) / 4 + 4, 1)
    return out


# ------------------------------------------------------------ cardiovascular

def qtc(qt_ms: float, heart_rate_bpm: float) -> dict[str, Any]:
    """QT corrected by Bazett, Fridericia, Framingham and Hodges (ms)."""
    qt = _num(qt_ms, "qt_ms", 100, 800)
    hr = _num(heart_rate_bpm, "heart_rate_bpm", 20, 300)
    rr = 60.0 / hr
    return {"rr_s": round(rr, 3),
            "bazett_ms": round(qt / math.sqrt(rr), 1),
            "fridericia_ms": round(qt / rr ** (1.0 / 3.0), 1),
            "framingham_ms": round(qt + 154.0 * (1.0 - rr), 1),
            "hodges_ms": round(qt + 1.75 * (hr - 60.0), 1)}


def mean_arterial_pressure(systolic: float, diastolic: float) -> dict[str, Any]:
    """MAP = DBP + (SBP − DBP) / 3."""
    sbp = _num(systolic, "systolic", 30, 300)
    dbp = _num(diastolic, "diastolic", 10, 200)
    if dbp > sbp:
        raise ValueError("diastolic cannot exceed systolic")
    return {"map_mmHg": round(dbp + (sbp - dbp) / 3.0, 1), "pulse_pressure": round(sbp - dbp, 1)}


def cha2ds2_vasc(age_years: float, sex: str, heart_failure: bool = False, hypertension: bool = False,
                 diabetes: bool = False, stroke_or_tia: bool = False,
                 vascular_disease: bool = False) -> dict[str, Any]:
    """CHA₂DS₂-VASc stroke risk score for atrial fibrillation."""
    age = _num(age_years, "age_years", 0, 120)
    s = _sex(sex)
    score = (_flag(heart_failure, "heart_failure") + _flag(hypertension, "hypertension")
             + (2 if age >= 75 else 1 if age >= 65 else 0) + _flag(diabetes, "diabetes")
             + 2 * _flag(stroke_or_tia, "stroke_or_tia")
             + _flag(vascular_disease, "vascular_disease") + (1 if s == "female" else 0))
    return {"score": score, "max": 9,
            "risk": "low" if score == 0 or (score == 1 and s == "female") else
            "intermediate" if score == 1 else "high"}


def has_bled(hypertension_uncontrolled: bool = False, abnormal_renal: bool = False,
             abnormal_liver: bool = False, stroke: bool = False, bleeding_history: bool = False,
             labile_inr: bool = False, age_over_65: bool = False, drugs: bool = False,
             alcohol: bool = False) -> dict[str, Any]:
    """HAS-BLED bleeding risk score (0–9)."""
    items = {k: _flag(v, k) for k, v in locals().items()}
    score = sum(items.values())
    return {"score": score, "max": 9, "risk": "high (≥3)" if score >= 3 else "moderate" if score == 2
            else "low", "items": items}


def wells_dvt(active_cancer: bool = False, paralysis_or_immobilisation: bool = False,
              bedridden_or_surgery: bool = False, localised_tenderness: bool = False,
              entire_leg_swollen: bool = False, calf_swelling_over_3cm: bool = False,
              pitting_oedema: bool = False, collateral_veins: bool = False,
              previous_dvt: bool = False, alternative_diagnosis_likely: bool = False
              ) -> dict[str, Any]:
    """Wells score for deep-vein thrombosis (alternative diagnosis subtracts 2)."""
    items = {k: _flag(v, k) for k, v in locals().items()}
    score = sum(v for k, v in items.items() if k != "alternative_diagnosis_likely")
    score -= 2 * items["alternative_diagnosis_likely"]
    return {"score": score, "three_tier": "high" if score >= 3 else "moderate" if score >= 1 else "low",
            "two_tier": "DVT likely" if score >= 2 else "DVT unlikely"}


def wells_pe(clinical_signs_of_dvt: bool = False, pe_most_likely: bool = False,
             heart_rate_over_100: bool = False, immobilisation_or_surgery: bool = False,
             previous_dvt_or_pe: bool = False, haemoptysis: bool = False,
             malignancy: bool = False) -> dict[str, Any]:
    """Wells score for pulmonary embolism."""
    weights = {"clinical_signs_of_dvt": 3.0, "pe_most_likely": 3.0, "heart_rate_over_100": 1.5,
               "immobilisation_or_surgery": 1.5, "previous_dvt_or_pe": 1.5, "haemoptysis": 1.0,
               "malignancy": 1.0}
    values = locals()
    score = sum(w * _flag(values[k], k) for k, w in weights.items())
    return {"score": score, "three_tier": "high" if score > 6 else "moderate" if score >= 2 else "low",
            "two_tier": "PE likely" if score > 4 else "PE unlikely"}


# ------------------------------------------------------------------ severity

def curb65(confusion: bool, urea_mmol_l: float, respiratory_rate: float, systolic: float,
           diastolic: float, age_years: float) -> dict[str, Any]:
    """CURB-65 pneumonia severity (urea > 7 mmol/L, RR ≥ 30, SBP < 90 or DBP ≤ 60, age ≥ 65)."""
    score = (_flag(confusion, "confusion")
             + (1 if _num(urea_mmol_l, "urea_mmol_l", 0, 100) > 7 else 0)
             + (1 if _num(respiratory_rate, "respiratory_rate", 0, 80) >= 30 else 0)
             + (1 if (_num(systolic, "systolic", 30, 300) < 90
                      or _num(diastolic, "diastolic", 10, 200) <= 60) else 0)
             + (1 if _num(age_years, "age_years", 0, 120) >= 65 else 0))
    return {"score": score, "risk": "low (0-1)" if score <= 1 else "moderate (2)" if score == 2
            else "high (3-5)"}


def meld_na(bilirubin_mg_dl: float, inr: float, creatinine_mg_dl: float, sodium: float,
            dialysis_twice_past_week: bool = False) -> dict[str, Any]:
    """MELD-Na as adopted by UNOS/OPTN in 2016 (values floored at 1, creatinine capped at 4,
    sodium bounded to 125–137, score capped at 40)."""
    bili = max(1.0, _num(bilirubin_mg_dl, "bilirubin_mg_dl", 0, 100))
    inr_v = max(1.0, _num(inr, "inr", 0.5, 20))
    cr = max(1.0, min(4.0, _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)))
    if _flag(dialysis_twice_past_week, "dialysis_twice_past_week"):
        cr = 4.0
    na = min(137.0, max(125.0, _num(sodium, "sodium", 100, 200)))
    meld_i = round(10 * (0.957 * math.log(cr) + 0.378 * math.log(bili) + 1.120 * math.log(inr_v)
                         + 0.643))
    score = meld_i
    if meld_i > 11:
        score = round(meld_i + 1.32 * (137 - na) - 0.033 * meld_i * (137 - na))
    score = min(40, max(6, score))
    return {"meld_na": score, "meld_initial": meld_i,
            "mortality_90d_band": "<6%" if score < 10 else "6-20%" if score < 20 else
            "20-50%" if score < 30 else ">50%"}


def child_pugh(bilirubin_mg_dl: float, albumin_g_dl: float, inr: float, ascites: str = "none",
               encephalopathy: str = "none") -> dict[str, Any]:
    """Child–Pugh class for cirrhosis. ascites: none|mild|moderate; encephalopathy: none|grade1-2|grade3-4."""
    bili = _num(bilirubin_mg_dl, "bilirubin_mg_dl", 0, 100)
    alb = _num(albumin_g_dl, "albumin_g_dl", 0.5, 7)
    inr_v = _num(inr, "inr", 0.5, 20)
    asc = {"none": 1, "mild": 2, "moderate": 3, "severe": 3}.get(str(ascites).lower())
    enc = {"none": 1, "grade1-2": 2, "grade 1-2": 2, "grade3-4": 3, "grade 3-4": 3}.get(
        str(encephalopathy).lower())
    if asc is None or enc is None:
        raise ValueError("ascites must be none|mild|moderate and encephalopathy none|grade1-2|grade3-4")
    points = ((1 if bili < 2 else 2 if bili <= 3 else 3) + (1 if alb > 3.5 else 2 if alb >= 2.8 else 3)
              + (1 if inr_v < 1.7 else 2 if inr_v <= 2.3 else 3) + asc + enc)
    return {"points": points, "class": "A" if points <= 6 else "B" if points <= 9 else "C"}


def news2(respiratory_rate: float, spo2_percent: float, supplemental_oxygen: bool, systolic: float,
          heart_rate: float, alert: bool, temperature_c: float) -> dict[str, Any]:
    """National Early Warning Score 2 (SpO₂ scale 1)."""
    rr = _num(respiratory_rate, "respiratory_rate", 0, 80)
    spo2 = _num(spo2_percent, "spo2_percent", 40, 100)
    sbp = _num(systolic, "systolic", 30, 300)
    hr = _num(heart_rate, "heart_rate", 20, 300)
    temp = _num(temperature_c, "temperature_c", 25, 45)
    parts = {
        "respiratory_rate": 3 if rr <= 8 else 1 if rr <= 11 else 0 if rr <= 20 else 2 if rr <= 24 else 3,
        "spo2": 3 if spo2 <= 91 else 2 if spo2 <= 93 else 1 if spo2 <= 95 else 0,
        "oxygen": 2 * _flag(supplemental_oxygen, "supplemental_oxygen"),
        "systolic": 3 if sbp <= 90 else 2 if sbp <= 100 else 1 if sbp <= 110 else 0 if sbp <= 219 else 3,
        "heart_rate": 3 if hr <= 40 else 1 if hr <= 50 else 0 if hr <= 90 else 1 if hr <= 110
        else 2 if hr <= 130 else 3,
        "consciousness": 0 if _flag(alert, "alert") else 3,
        "temperature": 3 if temp <= 35.0 else 1 if temp <= 36.0 else 0 if temp <= 38.0
        else 1 if temp <= 39.0 else 2,
    }
    total = sum(parts.values())
    single_three = any(v == 3 for k, v in parts.items() if k != "oxygen")
    risk = ("high" if total >= 7 else "medium" if total >= 5 or single_three else "low")
    return {"score": total, "components": parts, "risk": risk}


def glasgow_coma_scale(eyes: int, verbal: int, motor: int) -> dict[str, Any]:
    """GCS total (3–15) and the conventional severity bands."""
    for name, v, hi in (("eyes", eyes, 4), ("verbal", verbal, 5), ("motor", motor, 6)):
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= hi:
            raise ValueError(f"{name} must be an integer from 1 to {hi}")
    total = eyes + verbal + motor
    return {"total": total, "severity": "severe (3-8)" if total <= 8 else "moderate (9-12)"
            if total <= 12 else "mild (13-15)"}


def qsofa(respiratory_rate: float, systolic: float, altered_mentation: bool) -> dict[str, Any]:
    """Quick SOFA: RR ≥ 22, SBP ≤ 100, altered mentation."""
    score = ((1 if _num(respiratory_rate, "respiratory_rate", 0, 80) >= 22 else 0)
             + (1 if _num(systolic, "systolic", 30, 300) <= 100 else 0)
             + _flag(altered_mentation, "altered_mentation"))
    return {"score": score, "positive": score >= 2}


# ------------------------------------------------------------------ metabolic

def friedewald_ldl(total_cholesterol_mg_dl: float, hdl_mg_dl: float, triglycerides_mg_dl: float
                   ) -> dict[str, Any]:
    """LDL = TC − HDL − TG/5 (mg/dL); not valid above 400 mg/dL triglycerides."""
    tc = _num(total_cholesterol_mg_dl, "total_cholesterol_mg_dl", 50, 1000)
    hdl = _num(hdl_mg_dl, "hdl_mg_dl", 5, 200)
    tg = _num(triglycerides_mg_dl, "triglycerides_mg_dl", 10, 5000)
    if tg > 400:
        raise ValueError("Friedewald is not valid when triglycerides exceed 400 mg/dL")
    return {"ldl_mg_dl": round(tc - hdl - tg / 5.0, 1), "non_hdl_mg_dl": round(tc - hdl, 1)}


def hba1c_to_eag(hba1c_percent: float) -> dict[str, Any]:
    """Estimated average glucose: eAG (mg/dL) = 28.7 × HbA1c − 46.7 (ADAG study)."""
    a1c = _num(hba1c_percent, "hba1c_percent", 3, 20)
    eag = 28.7 * a1c - 46.7
    return {"eag_mg_dl": round(eag, 1), "eag_mmol_l": round(eag / 18.016, 2),
            "hba1c_mmol_mol": round((a1c - 2.15) * 10.929, 1)}


def basal_metabolic_rate(weight_kg: float, height_cm: float, age_years: float, sex: str,
                         activity_factor: float = 1.2) -> dict[str, Any]:
    """Mifflin–St Jeor BMR (kcal/day) and total energy at an activity factor."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    h = _num(height_cm, "height_cm", 30, 272)
    age = _num(age_years, "age_years", 0, 120)
    s = _sex(sex)
    bmr = 10 * w + 6.25 * h - 5 * age + (5 if s == "male" else -161)
    factor = _num(activity_factor, "activity_factor", 1.0, 2.5)
    return {"bmr_kcal_day": round(bmr), "tdee_kcal_day": round(bmr * factor), "equation": "Mifflin-St Jeor"}


# ------------------------------------------------------- dosing and fluids

def parkland_formula(weight_kg: float, tbsa_burned_percent: float) -> dict[str, Any]:
    """Parkland: 4 mL × kg × %TBSA of crystalloid over 24 h, half in the first 8 h."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    tbsa = _num(tbsa_burned_percent, "tbsa_burned_percent", 0, 100)
    total = 4.0 * w * tbsa
    return {"total_24h_ml": round(total), "first_8h_ml": round(total / 2),
            "first_8h_rate_ml_h": round(total / 16), "next_16h_rate_ml_h": round(total / 32)}


def weight_based_dose(dose_mg_per_kg: float, weight_kg: float, max_dose_mg: float | None = None,
                      doses_per_day: int = 1) -> dict[str, Any]:
    """Weight-based dose with an optional per-dose cap; flags when the cap applied."""
    per_kg = _num(dose_mg_per_kg, "dose_mg_per_kg", 0, 10000)
    w = _num(weight_kg, "weight_kg", 0.3, 500)
    if not isinstance(doses_per_day, int) or doses_per_day < 1:
        raise ValueError("doses_per_day must be a positive integer")
    dose = per_kg * w
    capped = False
    if max_dose_mg is not None:
        cap = _num(max_dose_mg, "max_dose_mg", 0, 100000)
        if dose > cap:
            dose, capped = cap, True
    return {"dose_mg": round(dose, 2), "daily_total_mg": round(dose * doses_per_day, 2),
            "capped_at_max": capped}


def tidal_volume(height_cm: float, sex: str, ml_per_kg: float = 6.0) -> dict[str, Any]:
    """Lung-protective tidal volume from predicted (ideal) body weight."""
    ibw = ideal_body_weight(height_cm, sex)["ideal_body_weight_kg"]
    per_kg = _num(ml_per_kg, "ml_per_kg", 4, 10)
    return {"predicted_body_weight_kg": ibw, "tidal_volume_ml": round(ibw * per_kg),
            "range_6_to_8_ml_kg": [round(ibw * 6), round(ibw * 8)]}


#: Conventional → SI factors (multiply). Keys are (analyte, from_unit, to_unit).
UNIT_FACTORS: dict[tuple[str, str, str], float] = {
    ("glucose", "mg/dL", "mmol/L"): 0.0555, ("creatinine", "mg/dL", "umol/L"): 88.4,
    ("cholesterol", "mg/dL", "mmol/L"): 0.02586, ("triglycerides", "mg/dL", "mmol/L"): 0.01129,
    ("bilirubin", "mg/dL", "umol/L"): 17.1, ("urea", "mg/dL", "mmol/L"): 0.357,
    ("bun", "mg/dL", "mmol/L"): 0.357, ("calcium", "mg/dL", "mmol/L"): 0.2495,
    ("hemoglobin", "g/dL", "g/L"): 10.0, ("albumin", "g/dL", "g/L"): 10.0,
    ("uric_acid", "mg/dL", "umol/L"): 59.48, ("lactate", "mg/dL", "mmol/L"): 0.111,
    ("magnesium", "mg/dL", "mmol/L"): 0.4114, ("phosphate", "mg/dL", "mmol/L"): 0.3229,
    ("iron", "ug/dL", "umol/L"): 0.179, ("vitamin_d", "ng/mL", "nmol/L"): 2.496,
    ("temperature", "F", "C"): None,  # handled specially
    ("weight", "lb", "kg"): 0.45359237, ("height", "in", "cm"): 2.54,
}


def convert_units(analyte: str, value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert a laboratory or anthropometric value between conventional and SI units."""
    name = str(analyte).strip().lower().replace(" ", "_")
    v = _num(value, "value")
    key = (name, from_unit, to_unit)
    reverse = (name, to_unit, from_unit)
    if name == "temperature":
        if (from_unit, to_unit) == ("F", "C"):
            return {"value": round((v - 32) * 5 / 9, 2), "unit": "C", "analyte": name}
        if (from_unit, to_unit) == ("C", "F"):
            return {"value": round(v * 9 / 5 + 32, 2), "unit": "F", "analyte": name}
        raise ValueError("temperature converts between C and F")
    if key in UNIT_FACTORS and UNIT_FACTORS[key]:
        return {"value": round(v * UNIT_FACTORS[key], 4), "unit": to_unit, "analyte": name}
    if reverse in UNIT_FACTORS and UNIT_FACTORS[reverse]:
        return {"value": round(v / UNIT_FACTORS[reverse], 4), "unit": to_unit, "analyte": name}
    known = sorted({f"{a}: {f} <-> {t}" for a, f, t in UNIT_FACTORS if a != "temperature"})
    raise ValueError(f"no conversion for {name} {from_unit} -> {to_unit}; known: {known}")


# ------------------------------------------------- questionnaires and obstetrics

def phq9(answers: list) -> dict[str, Any]:
    """PHQ-9 depression severity from nine item scores (0–3 each)."""
    if not isinstance(answers, (list, tuple)) or len(answers) != 9:
        raise ValueError("phq9 needs exactly nine item scores")
    if any(isinstance(a, bool) or a not in (0, 1, 2, 3) for a in answers):
        raise ValueError("each PHQ-9 item is scored 0, 1, 2 or 3")
    total = int(sum(answers))
    band = ("minimal" if total <= 4 else "mild" if total <= 9 else "moderate" if total <= 14
            else "moderately severe" if total <= 19 else "severe")
    return {"score": total, "severity": band, "item9_self_harm_flag": int(answers[8]) > 0}


def gad7(answers: list) -> dict[str, Any]:
    """GAD-7 anxiety severity from seven item scores (0–3 each)."""
    if not isinstance(answers, (list, tuple)) or len(answers) != 7:
        raise ValueError("gad7 needs exactly seven item scores")
    if any(isinstance(a, bool) or a not in (0, 1, 2, 3) for a in answers):
        raise ValueError("each GAD-7 item is scored 0, 1, 2 or 3")
    total = int(sum(answers))
    return {"score": total, "severity": "minimal" if total <= 4 else "mild" if total <= 9
            else "moderate" if total <= 14 else "severe"}


def apgar(appearance: int, pulse: int, grimace: int, activity: int, respiration: int
          ) -> dict[str, Any]:
    """Apgar score: five signs scored 0–2."""
    parts = {"appearance": appearance, "pulse": pulse, "grimace": grimace,
             "activity": activity, "respiration": respiration}
    for name, v in parts.items():
        if isinstance(v, bool) or v not in (0, 1, 2):
            raise ValueError(f"{name} must be 0, 1 or 2")
    total = sum(parts.values())
    return {"score": total, "band": "reassuring (7-10)" if total >= 7 else
            "moderately abnormal (4-6)" if total >= 4 else "low (0-3)"}


def bishop_score(dilation_cm: float, effacement_percent: float, station: int,
                 consistency: str, position: str) -> dict[str, Any]:
    """Bishop score for cervical favourability (0–13)."""
    d = _num(dilation_cm, "dilation_cm", 0, 10)
    e = _num(effacement_percent, "effacement_percent", 0, 100)
    if not isinstance(station, int) or isinstance(station, bool) or not -3 <= station <= 3:
        raise ValueError("station must be an integer from -3 to +3")
    cons = {"firm": 0, "medium": 1, "soft": 2}.get(str(consistency).lower())
    pos = {"posterior": 0, "mid": 1, "midposition": 1, "anterior": 2}.get(str(position).lower())
    if cons is None or pos is None:
        raise ValueError("consistency must be firm|medium|soft and position posterior|mid|anterior")
    points = ((0 if d == 0 else 1 if d <= 2 else 2 if d <= 4 else 3)
              + (0 if e < 40 else 1 if e < 60 else 2 if e < 80 else 3)
              + (0 if station <= -3 else 1 if station == -2 else 2 if station in (-1, 0) else 3)
              + cons + pos)
    return {"score": points, "favourable": points >= 8, "unfavourable": points <= 5}


def gestational_age(last_menstrual_period: str, reference_date: str) -> dict[str, Any]:
    """Gestational age and Naegele's estimated due date from the LMP (ISO dates)."""
    from datetime import date, timedelta

    try:
        lmp = date.fromisoformat(str(last_menstrual_period))
        ref = date.fromisoformat(str(reference_date))
    except ValueError:
        raise ValueError("dates must be ISO format YYYY-MM-DD") from None
    days = (ref - lmp).days
    if days < 0 or days > 320:
        raise ValueError("reference_date must be 0-320 days after the last menstrual period")
    edd = lmp + timedelta(days=280)
    return {"weeks": days // 7, "days": days % 7, "gestational_age": f"{days // 7}w{days % 7}d",
            "estimated_due_date": edd.isoformat(), "days_to_due_date": (edd - ref).days,
            "trimester": 1 if days < 98 else 2 if days < 196 else 3}


# ------------------------------------------------------ cardiovascular risk

#: Pooled Cohort Equations (Goff et al. 2013, ACC/AHA), per sex and race group.
_PCE = {
    ("female", "white"): {"ln_age": -29.799, "ln_age_sq": 4.884, "ln_tc": 13.540,
                          "ln_age_ln_tc": -3.114, "ln_hdl": -13.578, "ln_age_ln_hdl": 3.149,
                          "ln_sbp_treated": 2.019, "ln_sbp_untreated": 1.957, "smoker": 7.574,
                          "ln_age_smoker": -1.665, "diabetes": 0.661, "mean": -29.18,
                          "baseline_survival": 0.9665},
    ("female", "african_american"): {"ln_age": 17.114, "ln_tc": 0.940, "ln_hdl": -18.920,
                                     "ln_age_ln_hdl": 4.475, "ln_sbp_treated": 29.291,
                                     "ln_age_ln_sbp_treated": -6.432, "ln_sbp_untreated": 27.820,
                                     "ln_age_ln_sbp_untreated": -6.087, "smoker": 0.691,
                                     "diabetes": 0.874, "mean": 86.61, "baseline_survival": 0.9533},
    ("male", "white"): {"ln_age": 12.344, "ln_tc": 11.853, "ln_age_ln_tc": -2.664,
                        "ln_hdl": -7.990, "ln_age_ln_hdl": 1.769, "ln_sbp_treated": 1.797,
                        "ln_sbp_untreated": 1.764, "smoker": 7.837, "ln_age_smoker": -1.795,
                        "diabetes": 0.658, "mean": 61.18, "baseline_survival": 0.9144},
    ("male", "african_american"): {"ln_age": 2.469, "ln_tc": 0.302, "ln_hdl": -0.307,
                                   "ln_sbp_treated": 1.916, "ln_sbp_untreated": 1.809,
                                   "smoker": 0.549, "diabetes": 0.645, "mean": 19.54,
                                   "baseline_survival": 0.8954},
}


def ascvd_pooled_cohort(age_years: float, sex: str, race: str, total_cholesterol_mg_dl: float,
                        hdl_mg_dl: float, systolic: float, treated_hypertension: bool = False,
                        smoker: bool = False, diabetes: bool = False) -> dict[str, Any]:
    """Ten-year atherosclerotic cardiovascular disease risk by the 2013 ACC/AHA Pooled
    Cohort Equations. Valid for ages 40-79; ``race`` is ``white`` (also used for other
    groups, as the guideline specifies) or ``african_american``."""
    age = _num(age_years, "age_years", 40, 79)
    s = _sex(sex)
    r = str(race).strip().lower().replace(" ", "_").replace("-", "_")
    if r in ("black", "aa", "african american"):
        r = "african_american"
    if r not in ("white", "african_american"):
        r = "white"
    tc = _num(total_cholesterol_mg_dl, "total_cholesterol_mg_dl", 130, 320)
    hdl = _num(hdl_mg_dl, "hdl_mg_dl", 20, 100)
    sbp = _num(systolic, "systolic", 90, 200)
    coef = _PCE[(s, r)]
    ln_age, ln_tc, ln_hdl, ln_sbp = math.log(age), math.log(tc), math.log(hdl), math.log(sbp)
    treated = _flag(treated_hypertension, "treated_hypertension")
    smoke = _flag(smoker, "smoker")
    dm = _flag(diabetes, "diabetes")
    terms = {"ln_age": ln_age, "ln_age_sq": ln_age * ln_age, "ln_tc": ln_tc,
             "ln_age_ln_tc": ln_age * ln_tc, "ln_hdl": ln_hdl, "ln_age_ln_hdl": ln_age * ln_hdl,
             "ln_sbp_treated": ln_sbp if treated else 0.0,
             "ln_age_ln_sbp_treated": ln_age * ln_sbp if treated else 0.0,
             "ln_sbp_untreated": ln_sbp if not treated else 0.0,
             "ln_age_ln_sbp_untreated": ln_age * ln_sbp if not treated else 0.0,
             "smoker": smoke, "ln_age_smoker": ln_age * smoke, "diabetes": dm}
    total = sum(coef[k] * v for k, v in terms.items() if k in coef)
    risk = 1.0 - coef["baseline_survival"] ** math.exp(total - coef["mean"])
    pct = 100.0 * risk
    band = ("low (< 5 %)" if pct < 5 else "borderline (5 to < 7.5 %)" if pct < 7.5 else
            "intermediate (7.5 to < 20 %)" if pct < 20 else "high (>= 20 %)")
    return {"ten_year_ascvd_risk_percent": round(pct, 1), "band": band,
            "equation": f"{s}, {r}", "source": "Goff et al. 2013 Pooled Cohort Equations",
            "note": "risk categories follow the 2018 ACC/AHA cholesterol guideline"}


# ----------------------------------------------------------- organ failure

def sofa(pao2_fio2: float, mechanically_ventilated: bool, platelets_10e9_per_l: float,
         bilirubin_mg_dl: float, mean_arterial_pressure_mmHg: float, vasopressor: str,
         gcs: int, creatinine_mg_dl: float, urine_ml_per_day: float | None = None
         ) -> dict[str, Any]:
    """Sequential Organ Failure Assessment (Vincent 1996). ``vasopressor`` is one of
    ``none``, ``dopamine_le_5_or_dobutamine``, ``dopamine_gt_5_or_epi_norepi_le_0.1``,
    ``dopamine_gt_15_or_epi_norepi_gt_0.1`` (doses in mcg/kg/min)."""
    pf = _num(pao2_fio2, "pao2_fio2", 20, 800)
    vent = _flag(mechanically_ventilated, "mechanically_ventilated")
    plt = _num(platelets_10e9_per_l, "platelets_10e9_per_l", 0, 2000)
    bili = _num(bilirubin_mg_dl, "bilirubin_mg_dl", 0, 60)
    map_ = _num(mean_arterial_pressure_mmHg, "mean_arterial_pressure_mmHg", 20, 200)
    levels = {"none": 0, "dopamine_le_5_or_dobutamine": 2, "dopamine_gt_5_or_epi_norepi_le_0.1": 3,
              "dopamine_gt_15_or_epi_norepi_gt_0.1": 4}
    vaso = str(vasopressor).strip().lower()
    if vaso not in levels:
        raise ValueError(f"vasopressor must be one of {sorted(levels)}")
    g = gcs
    if isinstance(g, bool) or not isinstance(g, int) or not 3 <= g <= 15:
        raise ValueError("gcs must be an integer from 3 to 15")
    creat = _num(creatinine_mg_dl, "creatinine_mg_dl", 0.1, 30)
    urine = None if urine_ml_per_day is None else _num(urine_ml_per_day, "urine_ml_per_day", 0, 20000)

    resp = 0 if pf >= 400 else 1 if pf >= 300 else 2 if (pf >= 200 or not vent) else 3 if pf >= 100 else 4
    coag = 0 if plt >= 150 else 1 if plt >= 100 else 2 if plt >= 50 else 3 if plt >= 20 else 4
    liver = 0 if bili < 1.2 else 1 if bili < 2.0 else 2 if bili < 6.0 else 3 if bili < 12.0 else 4
    cardio = levels[vaso] if vaso != "none" else (0 if map_ >= 70 else 1)
    cns = 0 if g == 15 else 1 if g >= 13 else 2 if g >= 10 else 3 if g >= 6 else 4
    renal = 0 if creat < 1.2 else 1 if creat < 2.0 else 2 if creat < 3.5 else 3 if creat < 5.0 else 4
    if urine is not None:
        renal = max(renal, 4 if urine < 200 else 3 if urine < 500 else 0)
    components = {"respiration": resp, "coagulation": coag, "liver": liver,
                  "cardiovascular": cardio, "cns": cns, "renal": renal}
    total = sum(components.values())
    return {"score": total, "components": components,
            "note": "an increase of >= 2 points from baseline defines organ dysfunction in Sepsis-3"}


# ------------------------------------------------------------ acid-base & fluids

def calculated_osmolality(sodium: float, glucose_mg_dl: float, bun_mg_dl: float,
                          ethanol_mg_dl: float = 0.0, measured_osmolality: float | None = None
                          ) -> dict[str, Any]:
    """Serum osmolality = 2·Na + glucose/18 + BUN/2.8 (+ ethanol/4.6), and the osmolar gap
    when a measured value is given (> 10 mOsm/kg is the usual threshold)."""
    na = _num(sodium, "sodium", 100, 200)
    glu = _num(glucose_mg_dl, "glucose_mg_dl", 10, 2000)
    bun = _num(bun_mg_dl, "bun_mg_dl", 0, 300)
    etoh = _num(ethanol_mg_dl, "ethanol_mg_dl", 0, 1000)
    calc = 2 * na + glu / 18.0 + bun / 2.8 + etoh / 4.6
    out: dict[str, Any] = {"calculated_osmolality_mosm_kg": round(calc, 1),
                           "formula": "2 Na + glucose/18 + BUN/2.8 + ethanol/4.6"}
    if measured_osmolality is not None:
        measured = _num(measured_osmolality, "measured_osmolality", 200, 500)
        gap = measured - calc
        out["osmolar_gap"] = round(gap, 1)
        out["gap_elevated"] = gap > 10
    return out


def winters_formula(bicarbonate: float, pco2_mmHg: float | None = None) -> dict[str, Any]:
    """Expected PaCO₂ in metabolic acidosis: 1.5 × HCO₃⁻ + 8 ± 2 (Winters 1967)."""
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    expected = 1.5 * hco3 + 8.0
    out: dict[str, Any] = {"expected_pco2_mmHg": round(expected, 1),
                           "expected_range_mmHg": [round(expected - 2, 1), round(expected + 2, 1)]}
    if pco2_mmHg is not None:
        pco2 = _num(pco2_mmHg, "pco2_mmHg", 5, 150)
        out["measured_pco2_mmHg"] = pco2
        out["compensation"] = ("appropriate" if expected - 2 <= pco2 <= expected + 2 else
                               "concurrent respiratory alkalosis" if pco2 < expected - 2 else
                               "concurrent respiratory acidosis")
    return out


def acid_base_interpretation(ph: float, pco2_mmHg: float, bicarbonate: float,
                             sodium: float | None = None, chloride: float | None = None,
                             albumin_g_dl: float | None = None) -> dict[str, Any]:
    """Primary acid-base disorder with the expected compensation (Winters for metabolic
    acidosis, 0.7·HCO₃ + 21 for metabolic alkalosis, acute and chronic rules for the
    respiratory disorders) and, with electrolytes, the anion gap and delta ratio."""
    p = _num(ph, "ph", 6.5, 8.0)
    pco2 = _num(pco2_mmHg, "pco2_mmHg", 5, 150)
    hco3 = _num(bicarbonate, "bicarbonate", 2, 60)
    findings: list[str] = []
    if p < 7.35:
        state = "acidaemia"
    elif p > 7.45:
        state = "alkalaemia"
    else:
        state = "normal pH"
    primary = "none"
    compensation: dict[str, Any] = {}
    if state == "acidaemia":
        if hco3 < 22 and pco2 > 45:
            primary = "mixed metabolic and respiratory acidosis"
        elif hco3 < 22:
            primary = "metabolic acidosis"
        elif pco2 > 45:
            primary = "respiratory acidosis"
        else:
            primary = "acidaemia without a clear primary process"
    elif state == "alkalaemia":
        if hco3 > 26 and pco2 < 35:
            primary = "mixed metabolic and respiratory alkalosis"
        elif hco3 > 26:
            primary = "metabolic alkalosis"
        elif pco2 < 35:
            primary = "respiratory alkalosis"
        else:
            primary = "alkalaemia without a clear primary process"
    else:
        if (hco3 < 22 and pco2 < 35) or (hco3 > 26 and pco2 > 45):
            primary = "normal pH with opposing abnormalities: fully compensated or mixed disorder"
        else:
            primary = "no acid-base disorder"
    if primary == "metabolic acidosis":
        expected = 1.5 * hco3 + 8.0
        compensation = {"rule": "Winters: PaCO2 = 1.5 x HCO3 + 8 +/- 2",
                        "expected_pco2_mmHg": round(expected, 1),
                        "verdict": ("appropriate" if expected - 2 <= pco2 <= expected + 2 else
                                    "additional respiratory alkalosis" if pco2 < expected - 2 else
                                    "additional respiratory acidosis")}
    elif primary == "metabolic alkalosis":
        expected = 0.7 * hco3 + 21.0
        compensation = {"rule": "PaCO2 = 0.7 x HCO3 + 21 +/- 2",
                        "expected_pco2_mmHg": round(expected, 1),
                        "verdict": ("appropriate" if expected - 2 <= pco2 <= expected + 2 else
                                    "additional respiratory alkalosis" if pco2 < expected - 2 else
                                    "additional respiratory acidosis")}
    elif primary == "respiratory acidosis":
        delta = (pco2 - 40.0) / 10.0
        compensation = {"rule": "HCO3 rises 1 (acute) or 3.5 (chronic) mmol/L per 10 mmHg PaCO2",
                        "expected_hco3_acute": round(24 + 1.0 * delta, 1),
                        "expected_hco3_chronic": round(24 + 3.5 * delta, 1)}
        compensation["verdict"] = ("acute pattern" if abs(hco3 - (24 + delta)) <= 2 else
                                   "chronic pattern" if abs(hco3 - (24 + 3.5 * delta)) <= 2 else
                                   "between acute and chronic, or a second metabolic process")
    elif primary == "respiratory alkalosis":
        delta = (40.0 - pco2) / 10.0
        compensation = {"rule": "HCO3 falls 2 (acute) or 4 (chronic) mmol/L per 10 mmHg PaCO2",
                        "expected_hco3_acute": round(24 - 2.0 * delta, 1),
                        "expected_hco3_chronic": round(24 - 4.0 * delta, 1)}
        compensation["verdict"] = ("acute pattern" if abs(hco3 - (24 - 2 * delta)) <= 2 else
                                   "chronic pattern" if abs(hco3 - (24 - 4 * delta)) <= 2 else
                                   "between acute and chronic, or a second metabolic process")
    out: dict[str, Any] = {"ph_state": state, "primary_disorder": primary,
                           "compensation": compensation, "findings": findings}
    if sodium is not None and chloride is not None:
        gap = anion_gap(sodium, chloride, hco3, albumin_g_dl=albumin_g_dl)
        out["anion_gap"] = gap
        ag = gap.get("albumin_corrected_gap") or gap["anion_gap"]
        if ag > 12:
            findings.append("elevated anion gap")
            if hco3 < 24:
                ratio = (ag - 12.0) / (24.0 - hco3)
                out["delta_ratio"] = round(ratio, 2)
                findings.append("delta ratio < 1: concurrent non-gap acidosis" if ratio < 1 else
                                "delta ratio > 2: concurrent metabolic alkalosis" if ratio > 2 else
                                "delta ratio 1-2: pure high anion gap acidosis")
    return out


def holliday_segar(weight_kg: float) -> dict[str, Any]:
    """Maintenance fluids by the Holliday–Segar 4-2-1 rule (hourly) and 100-50-20 (daily)."""
    w = _num(weight_kg, "weight_kg", 1, 300)
    first, second, rest = min(w, 10.0), min(max(w - 10.0, 0.0), 10.0), max(w - 20.0, 0.0)
    hourly = 4 * first + 2 * second + 1 * rest
    daily = 100 * first + 50 * second + 20 * rest
    return {"ml_per_hour": round(hourly, 1), "ml_per_day": round(daily, 0),
            "rule": "4-2-1 mL/kg/h and 100-50-20 mL/kg/day"}


def free_water_deficit(weight_kg: float, sodium: float, sex: str, age_years: float | None = None,
                       target_sodium: float = 140.0) -> dict[str, Any]:
    """Free water deficit = TBW × (Na/target − 1); TBW fraction 0.6 (men) or 0.5 (women),
    0.5 / 0.45 above 65 years."""
    w = _num(weight_kg, "weight_kg", 1, 500)
    na = _num(sodium, "sodium", 100, 200)
    target = _num(target_sodium, "target_sodium", 120, 160)
    s = _sex(sex)
    elderly = age_years is not None and _num(age_years, "age_years", 0, 130) > 65
    fraction = (0.5 if elderly else 0.6) if s == "male" else (0.45 if elderly else 0.5)
    tbw = fraction * w
    deficit = tbw * (na / target - 1.0)
    return {"total_body_water_l": round(tbw, 2), "tbw_fraction": fraction,
            "free_water_deficit_l": round(deficit, 2),
            "note": "correct hypernatraemia slowly (about 10 mmol/L per day) to avoid cerebral oedema"}


def allowable_blood_loss(weight_kg: float, haematocrit_initial: float, haematocrit_minimum: float,
                         population: str = "adult_male") -> dict[str, Any]:
    """Maximum allowable blood loss = EBV × (Hct_initial − Hct_minimum) / Hct_initial, with
    the estimated blood volume by population (mL/kg): premature 95, neonate 85, infant 80,
    child 75, adult male 75, adult female 65."""
    w = _num(weight_kg, "weight_kg", 0.3, 500)
    hi = _num(haematocrit_initial, "haematocrit_initial", 10, 70)
    hf = _num(haematocrit_minimum, "haematocrit_minimum", 10, 70)
    if hf >= hi:
        raise ValueError("haematocrit_minimum must be below haematocrit_initial")
    ebv_per_kg = {"premature": 95, "neonate": 85, "infant": 80, "child": 75,
                  "adult_male": 75, "adult_female": 65}
    pop = str(population).strip().lower()
    if pop not in ebv_per_kg:
        raise ValueError(f"population must be one of {sorted(ebv_per_kg)}")
    ebv = ebv_per_kg[pop] * w
    average = ebv * (hi - hf) / ((hi + hf) / 2.0)
    return {"estimated_blood_volume_ml": round(ebv, 0), "mabl_ml": round(ebv * (hi - hf) / hi, 0),
            "mabl_ml_average_hct_method": round(average, 0), "population": pop}


def infusion_rate(volume_ml: float, duration_min: float, drop_factor_gtt_per_ml: float | None = None
                  ) -> dict[str, Any]:
    """Infusion rate in mL/h and, with a giving-set drop factor, drops per minute."""
    v = _num(volume_ml, "volume_ml", 0, 100000, allow_zero=False)
    t = _num(duration_min, "duration_min", 0, 100000, allow_zero=False)
    out: dict[str, Any] = {"ml_per_hour": round(v / t * 60.0, 1)}
    if drop_factor_gtt_per_ml is not None:
        df = _num(drop_factor_gtt_per_ml, "drop_factor_gtt_per_ml", 1, 100)
        out["drops_per_minute"] = round(v * df / t, 0)
    return out


# ------------------------------------------------------------ risk scores

def heart_score(history: int, ecg: int, age_years: float, risk_factors: int, troponin: int,
                known_atherosclerotic_disease: bool = False) -> dict[str, Any]:
    """HEART score for chest pain (Six 2008): history, ECG and troponin each 0-2 as
    judged; age and risk-factor points are computed."""
    for name, v in (("history", history), ("ecg", ecg), ("troponin", troponin)):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 2:
            raise ValueError(f"{name} must be an integer 0, 1 or 2")
    age = _num(age_years, "age_years", 18, 120)
    if isinstance(risk_factors, bool) or not isinstance(risk_factors, int) or risk_factors < 0:
        raise ValueError("risk_factors must be a non-negative integer count")
    age_pts = 0 if age < 45 else 1 if age < 65 else 2
    rf_pts = 2 if (risk_factors >= 3 or _flag(known_atherosclerotic_disease, "known_atherosclerotic_disease")) \
        else 1 if risk_factors >= 1 else 0
    total = history + ecg + age_pts + rf_pts + troponin
    band = "low (0-3)" if total <= 3 else "moderate (4-6)" if total <= 6 else "high (7-10)"
    return {"score": total, "band": band,
            "components": {"history": history, "ecg": ecg, "age": age_pts,
                           "risk_factors": rf_pts, "troponin": troponin}}


def centor_mcisaac(fever_over_38: bool, absence_of_cough: bool, tender_anterior_nodes: bool,
                   tonsillar_exudate: bool, age_years: float) -> dict[str, Any]:
    """Modified Centor (McIsaac) score for streptococcal pharyngitis, with the age adjustment."""
    age = _num(age_years, "age_years", 1, 120)
    pts = sum(_flag(v, n) for n, v in (("fever_over_38", fever_over_38),
                                       ("absence_of_cough", absence_of_cough),
                                       ("tender_anterior_nodes", tender_anterior_nodes),
                                       ("tonsillar_exudate", tonsillar_exudate)))
    age_pts = 1 if age < 15 else 0 if age < 45 else -1
    total = pts + age_pts
    guidance = ("no testing or antibiotics" if total <= 1 else
                "consider rapid antigen test / culture" if total <= 3 else
                "test, or treat empirically per local guidance")
    return {"score": total, "age_adjustment": age_pts, "guidance": guidance}


def alvarado(migration_to_rlq: bool, anorexia: bool, nausea_vomiting: bool, rlq_tenderness: bool,
             rebound_pain: bool, fever: bool, leukocytosis: bool, left_shift: bool
             ) -> dict[str, Any]:
    """Alvarado (MANTRELS) score for suspected appendicitis."""
    weights = {"migration_to_rlq": 1, "anorexia": 1, "nausea_vomiting": 1, "rlq_tenderness": 2,
               "rebound_pain": 1, "fever": 1, "leukocytosis": 2, "left_shift": 1}
    values = {"migration_to_rlq": migration_to_rlq, "anorexia": anorexia,
              "nausea_vomiting": nausea_vomiting, "rlq_tenderness": rlq_tenderness,
              "rebound_pain": rebound_pain, "fever": fever, "leukocytosis": leukocytosis,
              "left_shift": left_shift}
    total = sum(weights[k] * _flag(v, k) for k, v in values.items())
    band = ("unlikely (<= 4)" if total <= 4 else "possible (5-6)" if total <= 6 else
            "probable (7-8)" if total <= 8 else "very probable (9-10)")
    return {"score": total, "band": band}


def timi_ua_nstemi(age_65_or_over: bool, three_or_more_cad_risk_factors: bool,
                   known_coronary_stenosis_50_percent: bool, aspirin_in_past_7_days: bool,
                   severe_angina_2_episodes_24h: bool, st_deviation_0_5mm: bool,
                   positive_cardiac_marker: bool) -> dict[str, Any]:
    """TIMI risk score for UA/NSTEMI (Antman 2000) with the 14-day event rates it reported."""
    items = {"age_65_or_over": age_65_or_over,
             "three_or_more_cad_risk_factors": three_or_more_cad_risk_factors,
             "known_coronary_stenosis_50_percent": known_coronary_stenosis_50_percent,
             "aspirin_in_past_7_days": aspirin_in_past_7_days,
             "severe_angina_2_episodes_24h": severe_angina_2_episodes_24h,
             "st_deviation_0_5mm": st_deviation_0_5mm,
             "positive_cardiac_marker": positive_cardiac_marker}
    total = sum(_flag(v, k) for k, v in items.items())
    rates = {0: 4.7, 1: 4.7, 2: 8.3, 3: 13.2, 4: 19.9, 5: 26.2, 6: 40.9, 7: 40.9}
    return {"score": total, "event_rate_14_day_percent": rates[total],
            "band": "low (0-2)" if total <= 2 else "intermediate (3-4)" if total <= 4 else "high (5-7)"}


def abcd2(age_60_or_over: bool, bp_140_90_or_over: bool, unilateral_weakness: bool,
          speech_disturbance_without_weakness: bool, duration_minutes: float, diabetes: bool
          ) -> dict[str, Any]:
    """ABCD² score for early stroke risk after a transient ischaemic attack."""
    dur = _num(duration_minutes, "duration_minutes", 0, 10000)
    clinical = 2 if _flag(unilateral_weakness, "unilateral_weakness") else \
        1 if _flag(speech_disturbance_without_weakness, "speech_disturbance_without_weakness") else 0
    duration = 2 if dur >= 60 else 1 if dur >= 10 else 0
    total = (_flag(age_60_or_over, "age_60_or_over") + _flag(bp_140_90_or_over, "bp_140_90_or_over")
             + clinical + duration + _flag(diabetes, "diabetes"))
    band = "low (0-3)" if total <= 3 else "moderate (4-5)" if total <= 5 else "high (6-7)"
    return {"score": total, "band": band,
            "components": {"age": int(bool(age_60_or_over)), "blood_pressure": int(bool(bp_140_90_or_over)),
                           "clinical_features": clinical, "duration": duration, "diabetes": int(bool(diabetes))}}


def sirs(temperature_c: float, heart_rate: float, respiratory_rate: float, wbc_10e9_per_l: float,
         paco2_mmHg: float | None = None, bands_percent: float | None = None) -> dict[str, Any]:
    """SIRS criteria (ACCP/SCCM 1992): two or more of temperature, heart rate, respiratory
    rate or PaCO₂, and white cell count or band forms."""
    temp = _num(temperature_c, "temperature_c", 25, 45)
    hr = _num(heart_rate, "heart_rate", 20, 300)
    rr = _num(respiratory_rate, "respiratory_rate", 0, 80)
    wbc = _num(wbc_10e9_per_l, "wbc_10e9_per_l", 0, 500)
    pco2 = None if paco2_mmHg is None else _num(paco2_mmHg, "paco2_mmHg", 5, 150)
    bands = None if bands_percent is None else _num(bands_percent, "bands_percent", 0, 100)
    criteria = {"temperature": temp > 38 or temp < 36, "heart_rate": hr > 90,
                "respiratory": rr > 20 or (pco2 is not None and pco2 < 32),
                "white_cells": wbc > 12 or wbc < 4 or (bands is not None and bands > 10)}
    met = sum(criteria.values())
    return {"criteria_met": met, "sirs_positive": met >= 2, "criteria": criteria}


def rcri(high_risk_surgery: bool, ischaemic_heart_disease: bool, heart_failure: bool,
         cerebrovascular_disease: bool, insulin_treated_diabetes: bool,
         creatinine_over_2_mg_dl: bool) -> dict[str, Any]:
    """Revised Cardiac Risk Index (Lee 1999) with the derivation cohort's major cardiac
    complication rates by class."""
    items = {"high_risk_surgery": high_risk_surgery, "ischaemic_heart_disease": ischaemic_heart_disease,
             "heart_failure": heart_failure, "cerebrovascular_disease": cerebrovascular_disease,
             "insulin_treated_diabetes": insulin_treated_diabetes,
             "creatinine_over_2_mg_dl": creatinine_over_2_mg_dl}
    total = sum(_flag(v, k) for k, v in items.items())
    classes = {0: ("I", 0.4), 1: ("II", 0.9), 2: ("III", 6.6)}
    cls, rate = classes.get(total, ("IV", 11.0))
    return {"score": total, "class": cls, "major_cardiac_complication_rate_percent": rate,
            "source": "Lee et al. 1999 derivation cohort"}


def stop_bang(snoring: bool, tired: bool, observed_apnoea: bool, high_blood_pressure: bool,
              bmi_over_35: bool, age_over_50: bool, neck_over_40cm: bool, male: bool
              ) -> dict[str, Any]:
    """STOP-Bang screening score for obstructive sleep apnoea."""
    items = {"snoring": snoring, "tired": tired, "observed_apnoea": observed_apnoea,
             "high_blood_pressure": high_blood_pressure, "bmi_over_35": bmi_over_35,
             "age_over_50": age_over_50, "neck_over_40cm": neck_over_40cm, "male": male}
    total = sum(_flag(v, k) for k, v in items.items())
    band = "low (0-2)" if total <= 2 else "intermediate (3-4)" if total <= 4 else "high (5-8)"
    return {"score": total, "band": band}


# ------------------------------------------------------------- hepatology & metabolism

def fib4(age_years: float, ast_u_l: float, alt_u_l: float, platelets_10e9_per_l: float
         ) -> dict[str, Any]:
    """FIB-4 = age × AST / (platelets × √ALT) (Sterling 2006), with the usual cut-offs."""
    age = _num(age_years, "age_years", 18, 120)
    ast = _num(ast_u_l, "ast_u_l", 1, 10000)
    alt = _num(alt_u_l, "alt_u_l", 1, 10000)
    plt = _num(platelets_10e9_per_l, "platelets_10e9_per_l", 1, 2000)
    score = age * ast / (plt * math.sqrt(alt))
    band = ("low: advanced fibrosis unlikely (< 1.3)" if score < 1.3 else
            "indeterminate (1.3 to 2.67)" if score <= 2.67 else
            "high: advanced fibrosis likely (> 2.67)")
    return {"fib4": round(score, 2), "band": band,
            "note": "a lower cut-off of 2.0 is suggested above 65 years"}


def apri(ast_u_l: float, ast_upper_limit_normal_u_l: float, platelets_10e9_per_l: float
         ) -> dict[str, Any]:
    """AST to platelet ratio index = (AST / ULN × 100) / platelets (Wai 2003)."""
    ast = _num(ast_u_l, "ast_u_l", 1, 10000)
    uln = _num(ast_upper_limit_normal_u_l, "ast_upper_limit_normal_u_l", 10, 100)
    plt = _num(platelets_10e9_per_l, "platelets_10e9_per_l", 1, 2000)
    score = (ast / uln * 100.0) / plt
    band = ("significant fibrosis unlikely (< 0.5)" if score < 0.5 else
            "indeterminate (0.5 to 1.5)" if score <= 1.5 else
            "significant fibrosis likely (> 1.5); > 2.0 suggests cirrhosis")
    return {"apri": round(score, 2), "band": band}


def homa_ir(fasting_glucose_mg_dl: float, fasting_insulin_uU_ml: float) -> dict[str, Any]:
    """HOMA-IR = glucose (mg/dL) × insulin (µU/mL) / 405 (Matthews 1985)."""
    glu = _num(fasting_glucose_mg_dl, "fasting_glucose_mg_dl", 20, 600)
    ins = _num(fasting_insulin_uU_ml, "fasting_insulin_uU_ml", 0.1, 500)
    score = glu * ins / 405.0
    return {"homa_ir": round(score, 2), "homa_beta_percent": round(360.0 * ins / (glu - 63.0), 1) if glu > 63 else None,
            "note": "insulin resistance is commonly read above about 2.5; thresholds vary by population"}

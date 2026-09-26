"""Survival analysis from the standard library: Kaplan–Meier and the log-rank test.

Tested against the Freireich (1963) 6-mercaptopurine trial, the dataset every survival
textbook uses, so the numbers can be checked against any of them.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .stats import _numbers, _z_quantile

__all__ = ["kaplan_meier", "log_rank_test"]


def _events(values: Any, what: str, n: int) -> list[int]:
    if not isinstance(values, (list, tuple)) or len(values) != n:
        raise ValueError(f"{what} must be a list the same length as the times")
    out = []
    for v in values:
        if isinstance(v, bool) or v in (0, 1):
            out.append(int(v))
        else:
            raise ValueError(f"{what} must contain only 0/1 (1 = event, 0 = censored)")
    return out


def _times(values: Any, what: str) -> list[float]:
    t = _numbers(values, what)
    if any(x < 0 for x in t):
        raise ValueError(f"{what} must be non-negative")
    return t


def kaplan_meier(times: Sequence[float], status: Sequence[int], confidence: float = 0.95
                 ) -> dict[str, Any]:
    """Kaplan–Meier product-limit estimate with Greenwood standard errors.

    ``status`` marks 1 for an observed event and 0 for censoring (R's ``Surv`` convention). Subjects censored at a
    time are counted at risk at that time, the usual convention. The median is the first
    event time at which the survival estimate falls to 0.5 or below.
    """
    t = _times(times, "times")
    e = _events(status, "status", len(t))
    if not 0.5 <= confidence < 1:
        raise ValueError("confidence must be in [0.5, 1)")
    z = _z_quantile(0.5 + confidence / 2.0)
    survival, var_sum, median = 1.0, 0.0, None
    table = []
    for time in sorted({x for x, ev in zip(t, e) if ev == 1}):
        at_risk = sum(1 for x in t if x >= time)
        d = sum(1 for x, ev in zip(t, e) if x == time and ev == 1)
        censored = sum(1 for x, ev in zip(t, e) if x == time and ev == 0)
        survival *= 1.0 - d / at_risk
        if at_risk - d > 0:
            var_sum += d / (at_risk * (at_risk - d))
        se = survival * math.sqrt(var_sum)
        table.append({"time": time, "at_risk": at_risk, "events": d, "censored": censored,
                      "survival": round(survival, 6), "std_error": round(se, 6),
                      "ci_low": round(max(0.0, survival - z * se), 6),
                      "ci_high": round(min(1.0, survival + z * se), 6)})
        if median is None and survival <= 0.5:
            median = time
    return {"n": len(t), "events": sum(e), "censored": len(t) - sum(e), "table": table,
            "median_survival_time": median, "survival_at_last_event": round(survival, 6),
            "confidence": confidence,
            "ci_method": "Greenwood variance, normal approximation, clamped to [0, 1]"}


def log_rank_test(times_a: Sequence[float], status_a: Sequence[int], times_b: Sequence[float],
                  status_b: Sequence[int]) -> dict[str, Any]:
    """Two-group log-rank (Mantel–Cox) test with the hypergeometric variance, and the
    Pike estimate of the hazard ratio (O₁/E₁)/(O₂/E₂)."""
    ta, tb = _times(times_a, "times_a"), _times(times_b, "times_b")
    ea, eb = _events(status_a, "status_a", len(ta)), _events(status_b, "status_b", len(tb))
    if sum(ea) + sum(eb) == 0:
        raise ValueError("at least one event is needed")
    o1, e1, var = 0.0, 0.0, 0.0
    for time in sorted({x for x, ev in zip(ta + tb, ea + eb) if ev == 1}):
        n1 = sum(1 for x in ta if x >= time)
        n2 = sum(1 for x in tb if x >= time)
        n = n1 + n2
        d1 = sum(1 for x, ev in zip(ta, ea) if x == time and ev == 1)
        d2 = sum(1 for x, ev in zip(tb, eb) if x == time and ev == 1)
        d = d1 + d2
        if n == 0:
            continue
        o1 += d1
        e1 += d * n1 / n
        if n > 1:
            var += d * (n1 / n) * (1.0 - n1 / n) * (n - d) / (n - 1)
    o2 = float(sum(eb))
    e2 = float(sum(ea) + sum(eb)) - e1
    chi2 = (o1 - e1) ** 2 / var if var > 0 else 0.0
    hr = ((o1 / e1) / (o2 / e2)) if e1 > 0 and e2 > 0 and o2 > 0 else None
    return {"chi_square": round(chi2, 4), "df": 1,
            "p_value": round(math.erfc(math.sqrt(chi2 / 2.0)), 8),
            "observed": {"a": o1, "b": o2}, "expected": {"a": round(e1, 4), "b": round(e2, 4)},
            "hazard_ratio_a_vs_b": round(hr, 4) if hr is not None else None,
            "n": {"a": len(ta), "b": len(tb)}}

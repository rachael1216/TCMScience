"""Statistics for omics and epidemiology, from the standard library only.

The p-values come from closed forms (the error function for the normal and the 1-df
chi-square, the regularised incomplete beta for Student's t) rather than a numerics
library, so a tool runs in a PSH child with a clean environment. Each is tested against
textbook values.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["hypergeometric_test", "fisher_exact", "enrichment_analysis", "benjamini_hochberg",
           "mann_whitney_u", "welch_t_test", "log2_fold_change", "cpm", "tpm",
           "correlation", "diversity", "odds_ratio", "relative_risk", "diagnostic_metrics",
           "roc_auc", "number_needed_to_treat", "regularised_incomplete_beta",
           "meta_analysis", "chi_square_test", "linear_regression", "one_way_anova", "kruskal_wallis", "wilcoxon_signed_rank", "cohens_d", "post_test_probability", "sample_size_two_proportions", "sample_size_two_means", "incidence_rate"]


# ------------------------------------------------------------- special functions

def _ln_choose(n: int, k: int) -> float:
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-15:
            break
    return h


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), by the continued fraction of Numerical Recipes."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_two_sided(t: float, df: float) -> float:
    return regularised_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def _z_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def _numbers(values: Any, what: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{what} must be a non-empty list of numbers")
    try:
        return [float(v) for v in values]
    except (TypeError, ValueError):
        raise ValueError(f"{what} must contain only numbers") from None


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ------------------------------------------------------------------ enrichment

def hypergeometric_test(overlap: int, query_size: int, set_size: int, background: int
                        ) -> dict[str, Any]:
    """P(X ≥ overlap) drawing ``query_size`` from ``background`` with ``set_size`` marked."""
    for name, v in (("overlap", overlap), ("query_size", query_size), ("set_size", set_size),
                    ("background", background)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if query_size > background or set_size > background:
        raise ValueError("query_size and set_size cannot exceed background")
    if overlap > min(query_size, set_size):
        raise ValueError("overlap cannot exceed the smaller of query_size and set_size")
    denom = _ln_choose(background, query_size)
    p = 0.0
    for i in range(overlap, min(query_size, set_size) + 1):
        p += math.exp(_ln_choose(set_size, i) + _ln_choose(background - set_size, query_size - i)
                      - denom)
    expected = query_size * set_size / background if background else 0.0
    return {"p_value": min(1.0, p), "expected_overlap": round(expected, 4),
            "fold_enrichment": round(overlap / expected, 4) if expected else None,
            "overlap": overlap, "query_size": query_size, "set_size": set_size,
            "background": background}


def fisher_exact(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """Fisher's exact test on the 2×2 table [[a, b], [c, d]]: two-sided and one-sided."""
    for name, v in (("a", a), ("b", b), ("c", c), ("d", d)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    n = a + b + c + d
    if n == 0:
        raise ValueError("the table is empty")
    row1, col1 = a + b, a + c
    denom = _ln_choose(n, col1)

    def prob(x: int) -> float:
        return math.exp(_ln_choose(row1, x) + _ln_choose(n - row1, col1 - x) - denom)

    observed = prob(a)
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)
    two = sum(p for p in (prob(x) for x in range(lo, hi + 1)) if p <= observed * (1 + 1e-9))
    greater = sum(prob(x) for x in range(a, hi + 1))
    less = sum(prob(x) for x in range(lo, a + 1))
    odds = (a * d) / (b * c) if b * c else None
    return {"p_value": min(1.0, two), "p_greater": min(1.0, greater), "p_less": min(1.0, less),
            "odds_ratio": round(odds, 4) if odds is not None else None,
            "table": [[a, b], [c, d]]}


def enrichment_analysis(genes: Sequence[str], gene_sets: Mapping[str, Sequence[str]],
                        background: int, min_overlap: int = 1) -> dict[str, Any]:
    """Over-representation analysis of a gene list against named sets, BH-adjusted."""
    query = {str(g).upper() for g in genes if str(g).strip()}
    if not query:
        raise ValueError("genes must be a non-empty list")
    if not isinstance(gene_sets, Mapping) or not gene_sets:
        raise ValueError("gene_sets must be a non-empty mapping of name -> genes")
    if not isinstance(background, int) or background < len(query):
        raise ValueError("background must be an integer no smaller than the query")
    rows = []
    for name, members in gene_sets.items():
        member_set = {str(g).upper() for g in members}
        overlap = sorted(query & member_set)
        if len(overlap) < min_overlap:
            continue
        test = hypergeometric_test(len(overlap), len(query), min(len(member_set), background),
                                   background)
        rows.append({"set": name, "overlap": len(overlap), "set_size": len(member_set),
                     "p_value": test["p_value"], "fold_enrichment": test["fold_enrichment"],
                     "genes": overlap})
    adjusted = benjamini_hochberg([r["p_value"] for r in rows])["q_values"] if rows else []
    for row, q in zip(rows, adjusted):
        row["q_value"] = q
    rows.sort(key=lambda r: (r["p_value"], r["set"]))
    return {"query_size": len(query), "background": background, "results": rows,
            "significant_at_0_05": sum(1 for r in rows if r["q_value"] <= 0.05)}


def benjamini_hochberg(p_values: Sequence[float]) -> dict[str, Any]:
    """Benjamini–Hochberg false-discovery-rate adjustment, order preserved."""
    ps = _numbers(p_values, "p_values")
    if any(p < 0 or p > 1 for p in ps):
        raise ValueError("p-values must lie in [0, 1]")
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        q[i] = running
    return {"q_values": [round(v, 10) for v in q],
            "bonferroni": [min(1.0, p * m) for p in ps], "m": m}


# --------------------------------------------------------------- two samples

def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Mann–Whitney U with tie-corrected normal approximation (two-sided)."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    n1, n2 = len(a), len(b)
    ranks = _ranks(a + b)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    n = n1 + n2
    counts: dict[float, int] = {}
    for v in a + b:
        counts[v] = counts.get(v, 0) + 1
    tie_term = sum(t ** 3 - t for t in counts.values())
    sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))) if n > 1 else 0.0
    mean_u = n1 * n2 / 2.0
    z = (u - mean_u) / sigma if sigma else 0.0
    return {"u": u, "u1": u1, "u2": u2, "z": round(z, 6),
            "p_value": round(_z_two_sided(z), 10) if sigma else 1.0,
            "n1": n1, "n2": n2, "auc": round(u1 / (n1 * n2), 6)}


def welch_t_test(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Welch's unequal-variance t-test, two-sided, with Welch–Satterthwaite df."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    if len(a) < 2 or len(b) < 2:
        raise ValueError("each sample needs at least two values")
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / (len(a) - 1)
    vb = sum((v - mb) ** 2 for v in b) / (len(b) - 1)
    se2 = va / len(a) + vb / len(b)
    if se2 == 0:
        raise ValueError("both samples are constant; the statistic is undefined")
    t = (ma - mb) / math.sqrt(se2)
    df = se2 ** 2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    return {"t": round(t, 6), "df": round(df, 4), "p_value": round(_t_two_sided(t, df), 10),
            "mean_x": round(ma, 6), "mean_y": round(mb, 6), "difference": round(ma - mb, 6)}


# ------------------------------------------------------------- expression maths

def log2_fold_change(treated: Sequence[float], control: Sequence[float],
                     pseudocount: float = 1.0) -> dict[str, Any]:
    """Element-wise log2((treated + pc) / (control + pc))."""
    a, b = _numbers(treated, "treated"), _numbers(control, "control")
    if len(a) != len(b):
        raise ValueError("treated and control must have the same length")
    if pseudocount < 0:
        raise ValueError("pseudocount must be non-negative")
    lfc = [round(math.log2((p + pseudocount) / (q + pseudocount)), 6) for p, q in zip(a, b)]
    return {"log2_fold_change": lfc, "pseudocount": pseudocount,
            "mean": round(sum(lfc) / len(lfc), 6)}


def cpm(counts: Sequence[float], log: bool = False) -> dict[str, Any]:
    """Counts per million; ``log`` gives log2(CPM + 1)."""
    c = _numbers(counts, "counts")
    total = sum(c)
    if total <= 0:
        raise ValueError("counts must sum to a positive number")
    values = [v / total * 1e6 for v in c]
    if log:
        values = [math.log2(v + 1) for v in values]
    return {"values": [round(v, 4) for v in values], "library_size": total, "log": log}


def tpm(counts: Sequence[float], lengths_bp: Sequence[float]) -> dict[str, Any]:
    """Transcripts per million from raw counts and feature lengths."""
    c, lens = _numbers(counts, "counts"), _numbers(lengths_bp, "lengths_bp")
    if len(c) != len(lens):
        raise ValueError("counts and lengths_bp must have the same length")
    if any(v <= 0 for v in lens):
        raise ValueError("lengths must be positive")
    rpk = [v / (ln / 1000.0) for v, ln in zip(c, lens)]
    scale = sum(rpk)
    if scale <= 0:
        raise ValueError("counts must sum to a positive number")
    return {"values": [round(r / scale * 1e6, 4) for r in rpk]}


def correlation(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Pearson and Spearman correlation with a t-test p-value for Pearson's r."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    if len(a) != len(b) or len(a) < 3:
        raise ValueError("x and y need the same length of at least three")

    def pearson(u: Sequence[float], v: Sequence[float]) -> float:
        mu, mv = sum(u) / len(u), sum(v) / len(v)
        num = sum((p - mu) * (q - mv) for p, q in zip(u, v))
        den = math.sqrt(sum((p - mu) ** 2 for p in u) * sum((q - mv) ** 2 for q in v))
        if den == 0:
            raise ValueError("a constant series has no correlation")
        return num / den

    r = pearson(a, b)
    rho = pearson(_ranks(a), _ranks(b))
    n = len(a)
    if abs(r) < 1:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = _t_two_sided(t, n - 2)
    else:
        p = 0.0
    return {"pearson_r": round(r, 6), "pearson_p_value": round(p, 10),
            "spearman_rho": round(rho, 6), "n": n}


def diversity(counts: Sequence[float]) -> dict[str, Any]:
    """Shannon (natural log), Simpson's index and Pielou evenness."""
    c = [v for v in _numbers(counts, "counts") if v > 0]
    total = sum(c)
    if total <= 0:
        raise ValueError("counts must contain positive values")
    props = [v / total for v in c]
    shannon = -sum(p * math.log(p) for p in props)
    return {"shannon": round(shannon, 6), "simpson": round(1 - sum(p * p for p in props), 6),
            "richness": len(c), "evenness": round(shannon / math.log(len(c)), 6) if len(c) > 1 else None}


# ------------------------------------------------------------- epidemiology

def odds_ratio(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """Odds ratio with a 95% CI; Haldane–Anscombe 0.5 correction when a cell is zero."""
    cells = [a, b, c, d]
    if any((not isinstance(v, int)) or v < 0 for v in cells):
        raise ValueError("cells must be non-negative integers")
    corrected = any(v == 0 for v in cells)
    aa, bb, cc, dd = [v + 0.5 for v in cells] if corrected else cells
    orr = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return {"odds_ratio": round(orr, 4), "ci95": [round(math.exp(math.log(orr) - 1.96 * se), 4),
                                                   round(math.exp(math.log(orr) + 1.96 * se), 4)],
            "haldane_corrected": corrected, "table": [[a, b], [c, d]]}


def relative_risk(exposed_events: int, exposed_total: int, unexposed_events: int,
                  unexposed_total: int) -> dict[str, Any]:
    """Relative risk with a 95% CI, absolute risk difference and NNT/NNH."""
    for name, v in (("exposed_events", exposed_events), ("exposed_total", exposed_total),
                    ("unexposed_events", unexposed_events), ("unexposed_total", unexposed_total)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if exposed_events > exposed_total or unexposed_events > unexposed_total:
        raise ValueError("events cannot exceed totals")
    if exposed_total == 0 or unexposed_total == 0 or exposed_events == 0 or unexposed_events == 0:
        raise ValueError("relative risk needs events in both groups")
    r1, r0 = exposed_events / exposed_total, unexposed_events / unexposed_total
    rr = r1 / r0
    se = math.sqrt(1 / exposed_events - 1 / exposed_total + 1 / unexposed_events
                   - 1 / unexposed_total)
    ard = r1 - r0
    return {"relative_risk": round(rr, 4),
            "ci95": [round(math.exp(math.log(rr) - 1.96 * se), 4),
                     round(math.exp(math.log(rr) + 1.96 * se), 4)],
            "risk_exposed": round(r1, 6), "risk_unexposed": round(r0, 6),
            "absolute_risk_difference": round(ard, 6),
            "number_needed": round(1 / abs(ard), 2) if ard else None}


def diagnostic_metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """Sensitivity, specificity, predictive values, likelihood ratios, accuracy, F1, Youden."""
    for name, v in (("tp", tp), ("fp", fp), ("fn", fn), ("tn", tn)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if tp + fn == 0 or fp + tn == 0:
        raise ValueError("both a diseased and a non-diseased group are needed")
    sens, spec = tp / (tp + fn), tn / (fp + tn)
    ppv = tp / (tp + fp) if tp + fp else None
    npv = tn / (tn + fn) if tn + fn else None
    return {"sensitivity": round(sens, 6), "specificity": round(spec, 6),
            "ppv": round(ppv, 6) if ppv is not None else None,
            "npv": round(npv, 6) if npv is not None else None,
            "lr_positive": round(sens / (1 - spec), 4) if spec < 1 else None,
            "lr_negative": round((1 - sens) / spec, 4) if spec > 0 else None,
            "accuracy": round((tp + tn) / (tp + fp + fn + tn), 6),
            "f1": round(2 * tp / (2 * tp + fp + fn), 6) if tp + fp + fn else None,
            "youden_j": round(sens + spec - 1, 6),
            "prevalence": round((tp + fn) / (tp + fp + fn + tn), 6)}


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> dict[str, Any]:
    """Area under the ROC curve by the rank (Mann–Whitney) identity."""
    s = _numbers(scores, "scores")
    if not isinstance(labels, (list, tuple)) or len(labels) != len(s):
        raise ValueError("labels must be a list the same length as scores")
    lab = [1 if bool(v) else 0 for v in labels]
    n_pos, n_neg = sum(lab), len(lab) - sum(lab)
    if n_pos == 0 or n_neg == 0:
        raise ValueError("both classes must be present")
    ranks = _ranks(s)
    rank_sum = sum(r for r, y in zip(ranks, lab) if y == 1)
    auc = (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return {"auc": round(auc, 6), "n_positive": n_pos, "n_negative": n_neg}


def number_needed_to_treat(control_event_rate: float, experimental_event_rate: float
                           ) -> dict[str, Any]:
    """NNT (benefit) or NNH (harm) from two event rates in [0, 1]."""
    for name, v in (("control_event_rate", control_event_rate),
                    ("experimental_event_rate", experimental_event_rate)):
        if not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f"{name} must be a rate between 0 and 1")
    arr = control_event_rate - experimental_event_rate
    return {"absolute_risk_reduction": round(arr, 6),
            "relative_risk_reduction": round(arr / control_event_rate, 6) if control_event_rate else None,
            "nnt": round(1 / arr, 2) if arr > 0 else None,
            "nnh": round(-1 / arr, 2) if arr < 0 else None}


# ------------------------------------------------- more special functions

def _gamma_p(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x), series and continued fraction (NR)."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        ap, total, delta = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-16:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return 1.0 - _gamma_q_cf(a, x)


def _gamma_q_cf(a: float, x: float) -> float:
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _chi2_sf(x: float, df: float) -> float:
    """Upper tail of the chi-square distribution."""
    if x <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - _gamma_p(df / 2.0, x / 2.0)))


def _f_sf(f: float, df1: float, df2: float) -> float:
    """Upper tail of the F distribution via the regularised incomplete beta."""
    if f <= 0:
        return 1.0
    return regularised_incomplete_beta(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def _bisect(fn: Any, target: float, lo: float, hi: float) -> float:
    """Root of an increasing function by bisection: deterministic and library-free."""
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def _z_quantile(p: float) -> float:
    """z such that Φ(z) = p."""
    if not 0 < p < 1:
        raise ValueError("probability must lie strictly between 0 and 1")
    return _bisect(lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0)), p, -40.0, 40.0)


def _t_quantile(p: float, df: float) -> float:
    """t such that P(T ≤ t) = p for Student's t with ``df`` degrees of freedom."""
    if not 0 < p < 1:
        raise ValueError("probability must lie strictly between 0 and 1")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -_t_quantile(1.0 - p, df)
    return _bisect(lambda t: 1.0 - 0.5 * _t_two_sided(t, df), p, 0.0, 1e6)


def _chi2_quantile(p: float, df: float) -> float:
    if not 0 < p < 1:
        raise ValueError("probability must lie strictly between 0 and 1")
    return _bisect(lambda x: _gamma_p(df / 2.0, x / 2.0), p, 0.0, 1e7)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _var(values: Sequence[float]) -> float:
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _groups(groups: Any, minimum: int = 2) -> list[list[float]]:
    if not isinstance(groups, (list, tuple)) or len(groups) < 2:
        raise ValueError("groups must be a list of at least two lists of numbers")
    out = [_numbers(g, f"group {i + 1}") for i, g in enumerate(groups)]
    if any(len(g) < minimum for g in out):
        raise ValueError(f"every group needs at least {minimum} values")
    return out


# ------------------------------------------------------------------ inference

def meta_analysis(estimates: Sequence[float], standard_errors: Sequence[float],
                  labels: Sequence[str] | None = None, scale: str = "log") -> dict[str, Any]:
    """Inverse-variance meta-analysis: fixed effect, DerSimonian–Laird random effects,
    Cochran's Q, I² and τ². ``scale="log"`` treats the estimates as log ratios (log OR,
    RR or HR) and also reports the exponentiated pooled values."""
    y = _numbers(estimates, "estimates")
    se = _numbers(standard_errors, "standard_errors")
    if len(y) != len(se) or len(y) < 2:
        raise ValueError("at least two studies, with one standard error per estimate")
    if any(s <= 0 for s in se):
        raise ValueError("standard errors must be positive")
    if scale not in ("log", "raw"):
        raise ValueError("scale must be 'log' or 'raw'")
    names = [str(l) for l in labels] if labels else [f"study {i + 1}" for i in range(len(y))]
    if len(names) != len(y):
        raise ValueError("labels must match estimates")
    k = len(y)
    w = [1.0 / (s * s) for s in se]
    sw = sum(w)
    fixed = sum(wi * yi for wi, yi in zip(w, y)) / sw
    se_fixed = math.sqrt(1.0 / sw)
    q = sum(wi * (yi - fixed) ** 2 for wi, yi in zip(w, y))
    df = k - 1
    c = sw - sum(wi * wi for wi in w) / sw
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    wr = [1.0 / (s * s + tau2) for s in se]
    swr = sum(wr)
    random = sum(wi * yi for wi, yi in zip(wr, y)) / swr
    se_random = math.sqrt(1.0 / swr)
    i2 = max(0.0, (q - df) / q) if q > 0 else 0.0
    z = 1.959964

    def summary(est: float, s: float) -> dict[str, Any]:
        zval = est / s
        out = {"estimate": round(est, 6), "std_error": round(s, 6),
               "ci_low": round(est - z * s, 6), "ci_high": round(est + z * s, 6),
               "z": round(zval, 4), "p_value": round(_z_two_sided(zval), 8)}
        if scale == "log":
            out["ratio"] = round(math.exp(est), 6)
            out["ratio_ci_low"] = round(math.exp(est - z * s), 6)
            out["ratio_ci_high"] = round(math.exp(est + z * s), 6)
        return out

    return {"k": k, "fixed_effect": summary(fixed, se_fixed),
            "random_effects": summary(random, se_random),
            "heterogeneity": {"Q": round(q, 4), "df": df, "p_value": round(_chi2_sf(q, df), 6),
                              "I_squared": round(i2, 4), "tau_squared": round(tau2, 6)},
            "weights_percent": {"fixed": {n: round(100 * wi / sw, 2) for n, wi in zip(names, w)},
                                "random": {n: round(100 * wi / swr, 2) for n, wi in zip(names, wr)}},
            "method": "inverse variance; DerSimonian–Laird τ²"}


def chi_square_test(table: Sequence[Sequence[float]], yates: bool = False) -> dict[str, Any]:
    """Pearson's chi-square test of independence on an r×c table, with Cramér's V and a
    count of expected cells below 5 (when Fisher's exact test is the better choice)."""
    if not isinstance(table, (list, tuple)) or len(table) < 2:
        raise ValueError("table must have at least two rows")
    rows = [_numbers(r, f"row {i + 1}") for i, r in enumerate(table)]
    c = len(rows[0])
    if c < 2 or any(len(r) != c for r in rows):
        raise ValueError("every row must have the same number (≥ 2) of columns")
    if any(v < 0 for r in rows for v in r):
        raise ValueError("counts must be non-negative")
    r = len(rows)
    row_sums = [sum(row) for row in rows]
    col_sums = [sum(rows[i][j] for i in range(r)) for j in range(c)]
    n = sum(row_sums)
    if n == 0 or any(s == 0 for s in row_sums) or any(s == 0 for s in col_sums):
        raise ValueError("every row and column needs at least one observation")
    correction = 0.5 if (yates and r == 2 and c == 2) else 0.0
    chi2, below5 = 0.0, 0
    expected = []
    for i in range(r):
        exp_row = []
        for j in range(c):
            e = row_sums[i] * col_sums[j] / n
            exp_row.append(round(e, 4))
            if e < 5:
                below5 += 1
            chi2 += (max(abs(rows[i][j] - e) - correction, 0.0)) ** 2 / e
        expected.append(exp_row)
    df = (r - 1) * (c - 1)
    return {"chi_square": round(chi2, 4), "df": df, "p_value": round(_chi2_sf(chi2, df), 8),
            "expected": expected, "expected_cells_below_5": below5,
            "cramers_v": round(math.sqrt(chi2 / (n * min(r - 1, c - 1))), 4),
            "yates_correction": correction > 0, "n": n}


def linear_regression(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Ordinary least squares y = a + b·x with r², residual standard error, the slope's
    standard error, t statistic, p-value and 95 % confidence interval."""
    xs, ys = _numbers(x, "x"), _numbers(y, "y")
    n = len(xs)
    if len(ys) != n or n < 3:
        raise ValueError("x and y must have the same length, at least 3")
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((v - mx) ** 2 for v in xs)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    syy = sum((v - my) ** 2 for v in ys)
    if sxx == 0:
        raise ValueError("x has no variance")
    slope = sxy / sxx
    intercept = my - slope * mx
    sse = sum((b - (intercept + slope * a)) ** 2 for a, b in zip(xs, ys))
    r2 = 1.0 - sse / syy if syy > 0 else 1.0
    df = n - 2
    resid_se = math.sqrt(sse / df) if df > 0 else 0.0
    se_slope = resid_se / math.sqrt(sxx)
    t = slope / se_slope if se_slope > 0 else float("inf")
    p = _t_two_sided(t, df) if se_slope > 0 else 0.0
    tq = _t_quantile(0.975, df)
    return {"slope": round(slope, 6), "intercept": round(intercept, 6),
            "r_squared": round(r2, 6), "r": round(math.copysign(math.sqrt(max(r2, 0.0)), slope), 6),
            "residual_std_error": round(resid_se, 6), "slope_std_error": round(se_slope, 6),
            "t": round(t, 4) if math.isfinite(t) else None, "df": df, "p_value": round(p, 8),
            "slope_ci_95": [round(slope - tq * se_slope, 6), round(slope + tq * se_slope, 6)],
            "n": n}


def one_way_anova(groups: Sequence[Sequence[float]]) -> dict[str, Any]:
    """One-way ANOVA: F, degrees of freedom, p-value and η² (between / total sum of squares)."""
    gs = _groups(groups)
    everything = [v for g in gs for v in g]
    grand = _mean(everything)
    ss_between = sum(len(g) * (_mean(g) - grand) ** 2 for g in gs)
    ss_within = sum((v - _mean(g)) ** 2 for g in gs for v in g)
    df_between, df_within = len(gs) - 1, len(everything) - len(gs)
    ms_between, ms_within = ss_between / df_between, ss_within / df_within
    f = ms_between / ms_within if ms_within > 0 else float("inf")
    return {"F": round(f, 4) if math.isfinite(f) else None, "df_between": df_between,
            "df_within": df_within, "p_value": round(_f_sf(f, df_between, df_within), 8) if math.isfinite(f) else 0.0,
            "ss_between": round(ss_between, 6), "ss_within": round(ss_within, 6),
            "eta_squared": round(ss_between / (ss_between + ss_within), 6) if ss_between + ss_within > 0 else None,
            "group_means": [round(_mean(g), 6) for g in gs], "group_sizes": [len(g) for g in gs]}


def kruskal_wallis(groups: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Kruskal–Wallis H test with the tie correction; p from the chi-square approximation."""
    gs = _groups(groups, minimum=1)
    everything = [v for g in gs for v in g]
    n = len(everything)
    if n < 3:
        raise ValueError("at least three observations are needed")
    ranks = _ranks(everything)
    h, offset = 0.0, 0
    mean_ranks = []
    for g in gs:
        r = ranks[offset:offset + len(g)]
        offset += len(g)
        mean_ranks.append(round(_mean(r), 4))
        h += sum(r) ** 2 / len(g)
    h = 12.0 / (n * (n + 1)) * h - 3 * (n + 1)
    counts: dict[float, int] = {}
    for v in everything:
        counts[v] = counts.get(v, 0) + 1
    ties = sum(t ** 3 - t for t in counts.values())
    correction = 1.0 - ties / (n ** 3 - n)
    if correction > 0:
        h /= correction
    df = len(gs) - 1
    return {"H": round(h, 4), "df": df, "p_value": round(_chi2_sf(h, df), 8),
            "tie_correction": round(correction, 6), "mean_ranks": mean_ranks,
            "group_sizes": [len(g) for g in gs]}


def wilcoxon_signed_rank(x: Sequence[float], y: Sequence[float] | None = None) -> dict[str, Any]:
    """Wilcoxon signed-rank test, paired (x − y) or one-sample against zero; zero
    differences are dropped and the normal approximation with continuity correction and
    the tie term is used for the p-value."""
    xs = _numbers(x, "x")
    if y is not None:
        ys = _numbers(y, "y")
        if len(ys) != len(xs):
            raise ValueError("x and y must be paired, with the same length")
        diffs = [a - b for a, b in zip(xs, ys)]
    else:
        diffs = xs
    nonzero = [round(d, 9) for d in diffs if round(d, 9) != 0]
    n = len(nonzero)
    if n < 1:
        raise ValueError("all differences are zero")
    ranks = _ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, nonzero) if d < 0)
    mean = n * (n + 1) / 4.0
    counts: dict[float, int] = {}
    for d in nonzero:
        counts[abs(d)] = counts.get(abs(d), 0) + 1
    var = n * (n + 1) * (2 * n + 1) / 24.0 - sum(t ** 3 - t for t in counts.values()) / 48.0
    z = (abs(w_plus - mean) - 0.5) / math.sqrt(var) if var > 0 else 0.0
    z = max(z, 0.0)
    return {"W_plus": w_plus, "W_minus": w_minus, "W": min(w_plus, w_minus), "n_nonzero": n,
            "dropped_zero_differences": len(diffs) - n, "z": round(z, 4),
            "p_value": round(math.erfc(z / math.sqrt(2.0)), 8),
            "median_difference": round(sorted(diffs)[len(diffs) // 2], 6),
            "note": "normal approximation; exact tables are preferable below n = 10"}


def cohens_d(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Cohen's d with the pooled standard deviation, and Hedges' g small-sample correction."""
    xs, ys = _numbers(x, "x"), _numbers(y, "y")
    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("each group needs at least two values")
    n1, n2 = len(xs), len(ys)
    pooled = math.sqrt(((n1 - 1) * _var(xs) + (n2 - 1) * _var(ys)) / (n1 + n2 - 2))
    if pooled == 0:
        raise ValueError("both groups have zero variance")
    d = (_mean(xs) - _mean(ys)) / pooled
    g = d * (1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0))
    magnitude = abs(d)
    band = ("negligible" if magnitude < 0.2 else "small" if magnitude < 0.5 else
            "medium" if magnitude < 0.8 else "large")
    return {"cohens_d": round(d, 6), "hedges_g": round(g, 6), "pooled_sd": round(pooled, 6),
            "mean_difference": round(_mean(xs) - _mean(ys), 6), "band": band,
            "band_source": "Cohen 1988 conventions"}


def post_test_probability(pretest_probability: float, sensitivity: float | None = None,
                          specificity: float | None = None,
                          likelihood_ratio: float | None = None) -> dict[str, Any]:
    """Bayesian post-test probability by likelihood ratios: from sensitivity and
    specificity (both LR+ and LR− are applied) or from a single likelihood ratio."""
    if isinstance(pretest_probability, bool) or not isinstance(pretest_probability, (int, float)) \
            or not 0 < pretest_probability < 1:
        raise ValueError("pretest_probability must lie strictly between 0 and 1")
    pre_odds = pretest_probability / (1 - pretest_probability)

    def post(lr: float) -> float:
        odds = pre_odds * lr
        return odds / (1 + odds)

    if likelihood_ratio is not None:
        if isinstance(likelihood_ratio, bool) or not isinstance(likelihood_ratio, (int, float)) \
                or likelihood_ratio <= 0:
            raise ValueError("likelihood_ratio must be positive")
        return {"pretest_probability": pretest_probability, "pretest_odds": round(pre_odds, 6),
                "likelihood_ratio": likelihood_ratio,
                "post_test_probability": round(post(likelihood_ratio), 6)}
    for name, v in (("sensitivity", sensitivity), ("specificity", specificity)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ValueError(f"{name} must lie in (0, 1] when no likelihood ratio is given")
    lr_pos = sensitivity / (1 - specificity) if specificity < 1 else None
    lr_neg = (1 - sensitivity) / specificity
    return {"pretest_probability": pretest_probability, "pretest_odds": round(pre_odds, 6),
            "lr_positive": round(lr_pos, 4) if lr_pos is not None else None,
            "lr_negative": round(lr_neg, 4),
            "post_test_probability_if_positive": round(post(lr_pos), 6) if lr_pos is not None else 1.0,
            "post_test_probability_if_negative": round(post(lr_neg), 6)}


def sample_size_two_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8,
                                two_sided: bool = True, ratio: float = 1.0) -> dict[str, Any]:
    """Sample size per group to detect p1 versus p2 (pooled-variance normal approximation,
    Fleiss without continuity correction). ``ratio`` is n2/n1."""
    for name, v in (("p1", p1), ("p2", p2)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 1:
            raise ValueError(f"{name} must lie strictly between 0 and 1")
    if p1 == p2:
        raise ValueError("p1 and p2 must differ")
    if not 0 < alpha < 1 or not 0 < power < 1 or ratio <= 0:
        raise ValueError("alpha and power must lie in (0, 1) and ratio must be positive")
    z_a = _z_quantile(1 - alpha / 2 if two_sided else 1 - alpha)
    z_b = _z_quantile(power)
    pbar = (p1 + ratio * p2) / (1 + ratio)
    num = (z_a * math.sqrt((1 + 1 / ratio) * pbar * (1 - pbar))
           + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2) / ratio)) ** 2
    n1 = num / (p1 - p2) ** 2
    return {"n_group_1": math.ceil(n1), "n_group_2": math.ceil(ratio * n1),
            "total": math.ceil(n1) + math.ceil(ratio * n1), "alpha": alpha, "power": power,
            "two_sided": two_sided, "z_alpha": round(z_a, 4), "z_beta": round(z_b, 4)}


def sample_size_two_means(difference: float, sd: float, alpha: float = 0.05, power: float = 0.8,
                          two_sided: bool = True) -> dict[str, Any]:
    """Sample size per group for a difference in means: n = 2(z_α + z_β)²σ²/δ²."""
    if isinstance(difference, bool) or not isinstance(difference, (int, float)) or difference == 0:
        raise ValueError("difference must be a non-zero number")
    if isinstance(sd, bool) or not isinstance(sd, (int, float)) or sd <= 0:
        raise ValueError("sd must be positive")
    if not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("alpha and power must lie in (0, 1)")
    z_a = _z_quantile(1 - alpha / 2 if two_sided else 1 - alpha)
    z_b = _z_quantile(power)
    n = 2 * (z_a + z_b) ** 2 * sd * sd / (difference * difference)
    return {"n_per_group": math.ceil(n), "total": 2 * math.ceil(n),
            "standardised_difference": round(abs(difference) / sd, 4), "alpha": alpha,
            "power": power, "two_sided": two_sided}


def incidence_rate(event_count: int, person_time: float, per: float = 1000.0,
                   confidence: float = 0.95) -> dict[str, Any]:
    """Incidence rate with the exact (Garwood) Poisson confidence interval."""
    events = event_count
    if isinstance(events, bool) or not isinstance(events, int) or events < 0:
        raise ValueError("event_count must be a non-negative integer")
    if isinstance(person_time, bool) or not isinstance(person_time, (int, float)) or person_time <= 0:
        raise ValueError("person_time must be positive")
    if not 0.5 <= confidence < 1 or per <= 0:
        raise ValueError("confidence must lie in [0.5, 1) and per must be positive")
    alpha = 1 - confidence
    lower = _chi2_quantile(alpha / 2, 2 * events) / 2.0 if events > 0 else 0.0
    upper = _chi2_quantile(1 - alpha / 2, 2 * events + 2) / 2.0
    scale = per / person_time
    return {"events": events, "person_time": person_time, "per": per,
            "rate": round(events * scale, 6), "ci_low": round(lower * scale, 6),
            "ci_high": round(upper * scale, 6), "confidence": confidence,
            "ci_method": "exact Poisson (Garwood)"}

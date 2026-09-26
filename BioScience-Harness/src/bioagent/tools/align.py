"""Pairwise alignment: Needleman–Wunsch (global) and Smith–Waterman (local), linear gaps."""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["global_alignment", "local_alignment", "protein_alignment"]


def _score_fn(match: float, mismatch: float, matrix: Any):
    if isinstance(matrix, str):
        from .matrices import matrix_by_name
        matrix = matrix_by_name(matrix)
    if matrix:
        def score(a: str, b: str) -> float:
            try:
                return float(matrix[a][b])
            except KeyError:
                try:
                    return float(matrix[b][a])
                except KeyError:
                    raise ValueError(f"substitution matrix has no entry for ({a}, {b})") from None
        return score
    return lambda a, b: match if a == b else mismatch


def _prepare(a: Any, b: Any) -> tuple[str, str]:
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("both sequences must be strings")
    x, y = "".join(a.split()).upper(), "".join(b.split()).upper()
    if not x or not y:
        raise ValueError("sequences must not be empty")
    if len(x) * len(y) > 4_000_000:
        raise ValueError("sequences too long for in-memory alignment (product of lengths > 4e6)")
    return x, y


def _summarise(ax: str, ay: str, score: float) -> dict[str, Any]:
    matches = sum(1 for p, q in zip(ax, ay) if p == q and p != "-")
    gaps = ax.count("-") + ay.count("-")
    return {"score": score, "aligned_a": ax, "aligned_b": ay, "length": len(ax),
            "matches": matches, "identity": round(matches / len(ax), 4) if ax else 0.0,
            "gaps": gaps}


def global_alignment(a: str, b: str, match: float = 2.0, mismatch: float = -1.0,
                     gap: float = -2.0, matrix: Mapping[str, Mapping[str, float]] | str | None = None
                     ) -> dict[str, Any]:
    """Needleman–Wunsch with a linear gap penalty; ``matrix`` may name BLOSUM62."""
    x, y = _prepare(a, b)
    score = _score_fn(match, mismatch, matrix)
    n, m = len(x), len(y)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[0] * (m + 1) for _ in range(n + 1)]          # 0 diag, 1 up, 2 left
    for i in range(1, n + 1):
        H[i][0], T[i][0] = i * gap, 1
    for j in range(1, m + 1):
        H[0][j], T[0][j] = j * gap, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score(x[i - 1], y[j - 1])
            up = H[i - 1][j] + gap
            left = H[i][j - 1] + gap
            best = max(diag, up, left)
            H[i][j] = best
            T[i][j] = 0 if best == diag else (1 if best == up else 2)
    ax, ay, i, j = [], [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and T[i][j] == 0:
            ax.append(x[i - 1]); ay.append(y[j - 1]); i -= 1; j -= 1
        elif i > 0 and (j == 0 or T[i][j] == 1):
            ax.append(x[i - 1]); ay.append("-"); i -= 1
        else:
            ax.append("-"); ay.append(y[j - 1]); j -= 1
    out = _summarise("".join(reversed(ax)), "".join(reversed(ay)), H[n][m])
    out["mode"] = "global"
    return out


def local_alignment(a: str, b: str, match: float = 2.0, mismatch: float = -1.0,
                    gap: float = -2.0, matrix: Mapping[str, Mapping[str, float]] | str | None = None
                    ) -> dict[str, Any]:
    """Smith–Waterman with a linear gap penalty; reports the best local segment."""
    x, y = _prepare(a, b)
    score = _score_fn(match, mismatch, matrix)
    n, m = len(x), len(y)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[3] * (m + 1) for _ in range(n + 1)]          # 3 = stop
    best, best_pos = 0.0, (0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score(x[i - 1], y[j - 1])
            up = H[i - 1][j] + gap
            left = H[i][j - 1] + gap
            val = max(0.0, diag, up, left)
            H[i][j] = val
            T[i][j] = 3 if val == 0 else (0 if val == diag else (1 if val == up else 2))
            if val > best:
                best, best_pos = val, (i, j)
    ax, ay = [], []
    i, j = best_pos
    end_a, end_b = i, j
    while i > 0 and j > 0 and T[i][j] != 3:
        if T[i][j] == 0:
            ax.append(x[i - 1]); ay.append(y[j - 1]); i -= 1; j -= 1
        elif T[i][j] == 1:
            ax.append(x[i - 1]); ay.append("-"); i -= 1
        else:
            ax.append("-"); ay.append(y[j - 1]); j -= 1
    out = _summarise("".join(reversed(ax)), "".join(reversed(ay)), best)
    out.update({"mode": "local", "start_a": i + 1, "end_a": end_a, "start_b": j + 1,
                "end_b": end_b})
    return out


def protein_alignment(a: str, b: str, mode: str = "global", gap: float = -4.0) -> dict[str, Any]:
    """Protein alignment scored with BLOSUM62 and a linear gap penalty."""
    if mode == "global":
        out = global_alignment(a, b, gap=gap, matrix="BLOSUM62")
    elif mode == "local":
        out = local_alignment(a, b, gap=gap, matrix="BLOSUM62")
    else:
        raise ValueError("mode must be 'global' or 'local'")
    out["matrix"] = "BLOSUM62"
    return out

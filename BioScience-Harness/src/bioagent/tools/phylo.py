"""Phylogenetics from the standard library: distances, neighbor joining, UPGMA, Newick."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["distance_matrix", "neighbor_joining", "upgma", "parse_newick", "tree_distances"]

_TRANSITIONS = {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}


def _aligned(sequences: Any) -> tuple[list[str], list[str]]:
    if isinstance(sequences, Mapping):
        names, values = [str(k) for k in sequences], list(sequences.values())
    elif isinstance(sequences, (list, tuple)):
        names, values = [f"seq{i + 1}" for i in range(len(sequences))], list(sequences)
    else:
        raise ValueError("sequences must be a mapping name→sequence or a list of sequences")
    if len(values) < 2:
        raise ValueError("at least two sequences are needed")
    seqs = []
    for s in values:
        if not isinstance(s, str) or not s.strip():
            raise ValueError("every sequence must be a non-empty string")
        seqs.append("".join(s.split()).upper().replace("U", "T"))
    if any(len(s) != len(seqs[0]) for s in seqs):
        raise ValueError("sequences must be aligned to the same length")
    return names, seqs


def distance_matrix(sequences: Mapping[str, str] | Sequence[str], model: str = "p"
                    ) -> dict[str, Any]:
    """Pairwise distances between aligned nucleotide sequences: ``p`` (proportion of
    differing sites), ``jc69`` (Jukes–Cantor) or ``k2p`` (Kimura two-parameter). Sites
    with a gap or ambiguity code in either sequence are skipped; a pair too diverged for
    the model is reported as saturated rather than given a number."""
    names, seqs = _aligned(sequences)
    if model not in ("p", "jc69", "k2p"):
        raise ValueError("model must be 'p', 'jc69' or 'k2p'")
    n = len(seqs)
    matrix = [[0.0] * n for _ in range(n)]
    compared = [[0] * n for _ in range(n)]
    saturated = []
    for i in range(n):
        for j in range(i + 1, n):
            sites = diffs = ts = 0
            for a, b in zip(seqs[i], seqs[j]):
                if a in "ACGT" and b in "ACGT":
                    sites += 1
                    if a != b:
                        diffs += 1
                        if (a, b) in _TRANSITIONS:
                            ts += 1
            if sites == 0:
                raise ValueError(f"{names[i]} and {names[j]} share no comparable sites")
            p = diffs / sites
            d: float | None
            if model == "p":
                d = p
            elif model == "jc69":
                d = -0.75 * math.log(1 - 4 * p / 3) if p < 0.75 else None
            else:
                pt, q = ts / sites, (diffs - ts) / sites
                x, y = 1 - 2 * pt - q, 1 - 2 * q
                d = -0.5 * math.log(x) - 0.25 * math.log(y) if x > 0 and y > 0 else None
            if d is None:
                saturated.append([names[i], names[j]])
                d = float("nan")
            matrix[i][j] = matrix[j][i] = round(d, 6) if not math.isnan(d) else None
            compared[i][j] = compared[j][i] = sites
    return {"names": names, "model": model, "matrix": matrix, "sites_compared": compared,
            "saturated_pairs": saturated}


def _square(names: Any, matrix: Any) -> tuple[list[str], list[list[float]]]:
    if not isinstance(names, (list, tuple)) or len(names) < 2:
        raise ValueError("names must list at least two taxa")
    labels = [str(x) for x in names]
    if len(set(labels)) != len(labels):
        raise ValueError("taxon names must be unique")
    n = len(labels)
    if not isinstance(matrix, (list, tuple)) or len(matrix) != n:
        raise ValueError("matrix must be square with one row per name")
    out = []
    for i, row in enumerate(matrix):
        if not isinstance(row, (list, tuple)) or len(row) != n:
            raise ValueError("matrix must be square with one row per name")
        vals = []
        for j, v in enumerate(row):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or math.isnan(v):
                raise ValueError(f"matrix[{i}][{j}] must be a non-negative number")
            vals.append(float(v))
        out.append(vals)
    for i in range(n):
        for j in range(n):
            if abs(out[i][j] - out[j][i]) > 1e-9:
                raise ValueError("matrix must be symmetric")
    return labels, out


def _newick(node: int, nodes: dict[int, dict[str, Any]]) -> str:
    entry = nodes[node]
    if not entry["children"]:
        return _quote(entry["name"])
    parts = [f"{_newick(child, nodes)}:{length:.6f}".rstrip("0").rstrip(".")
             for child, length in entry["children"]]
    return "(" + ",".join(parts) + ")"


def _quote(name: str) -> str:
    if any(c in name for c in " ,:;()[]'"):
        return "'" + name.replace("'", "''") + "'"
    return name


def neighbor_joining(names: Sequence[str], matrix: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Neighbor joining (Saitou & Nei 1987; Q criterion of Studier & Keppler 1988). The
    tree is unrooted; to write it as Newick the last edge is split evenly, which keeps
    every leaf-to-leaf path length. Negative branch lengths are clamped to zero and counted."""
    labels, dist = _square(names, matrix)
    n = len(labels)
    nodes: dict[int, dict[str, Any]] = {i: {"name": labels[i], "children": []} for i in range(n)}
    d = {i: {j: dist[i][j] for j in range(n)} for i in range(n)}
    active = list(range(n))
    next_id = n
    clamped = 0
    while len(active) > 2:
        m = len(active)
        r = {i: sum(d[i][j] for j in active if j != i) for i in active}
        best, best_q = None, None
        for x in range(m):
            for y in range(x + 1, m):
                i, j = active[x], active[y]
                q = (m - 2) * d[i][j] - r[i] - r[j]
                if best_q is None or q < best_q - 1e-12:
                    best, best_q = (i, j), q
        i, j = best
        li = 0.5 * d[i][j] + (r[i] - r[j]) / (2.0 * (m - 2))
        lj = d[i][j] - li
        for value in (li, lj):
            if value < 0:
                clamped += 1
        li, lj = max(li, 0.0), max(lj, 0.0)
        new = next_id
        next_id += 1
        nodes[new] = {"name": "", "children": [(i, li), (j, lj)]}
        d[new] = {}
        for k in active:
            if k in (i, j):
                continue
            d[new][k] = d[k][new] = 0.5 * (d[i][k] + d[j][k] - d[i][j])
        active = [k for k in active if k not in (i, j)] + [new]
    i, j = active
    root = next_id
    half = max(d[i][j], 0.0) / 2.0
    nodes[root] = {"name": "", "children": [(i, half), (j, half)]}
    newick = _newick(root, nodes) + ";"
    return {"newick": newick, "names": labels, "method": "neighbor joining",
            "negative_branch_lengths_clamped": clamped,
            "note": "unrooted; the final edge is split evenly so the Newick is binary"}


def upgma(names: Sequence[str], matrix: Sequence[Sequence[float]]) -> dict[str, Any]:
    """UPGMA (average linkage), which assumes a molecular clock: every leaf is at the same
    distance from the root, and each node's height is half the distance it joined at."""
    labels, dist = _square(names, matrix)
    n = len(labels)
    nodes: dict[int, dict[str, Any]] = {i: {"name": labels[i], "children": []} for i in range(n)}
    height = {i: 0.0 for i in range(n)}
    size = {i: 1 for i in range(n)}
    d = {i: {j: dist[i][j] for j in range(n)} for i in range(n)}
    active = list(range(n))
    next_id = n
    while len(active) > 1:
        best, best_d = None, None
        for x in range(len(active)):
            for y in range(x + 1, len(active)):
                i, j = active[x], active[y]
                if best_d is None or d[i][j] < best_d - 1e-12:
                    best, best_d = (i, j), d[i][j]
        i, j = best
        h = best_d / 2.0
        new = next_id
        next_id += 1
        nodes[new] = {"name": "", "children": [(i, h - height[i]), (j, h - height[j])]}
        height[new] = h
        size[new] = size[i] + size[j]
        d[new] = {}
        for k in active:
            if k in (i, j):
                continue
            d[new][k] = d[k][new] = (size[i] * d[i][k] + size[j] * d[j][k]) / (size[i] + size[j])
        active = [k for k in active if k not in (i, j)] + [new]
    root = active[0]
    return {"newick": _newick(root, nodes) + ";", "names": labels, "method": "UPGMA",
            "root_height": round(height[root], 6)}


# ------------------------------------------------------------------- Newick

class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip_ws(self) -> None:
        while self.peek() and self.peek().isspace():
            self.pos += 1

    def node(self) -> dict[str, Any]:
        self.skip_ws()
        children: list[dict[str, Any]] = []
        if self.peek() == "(":
            self.pos += 1
            while True:
                children.append(self.node())
                self.skip_ws()
                if self.peek() == ",":
                    self.pos += 1
                    continue
                if self.peek() == ")":
                    self.pos += 1
                    break
                raise ValueError(f"expected ',' or ')' at position {self.pos}")
        name = self.label()
        length = None
        self.skip_ws()
        if self.peek() == ":":
            self.pos += 1
            start = self.pos
            while self.peek() and (self.peek().isdigit() or self.peek() in ".-+eE"):
                self.pos += 1
            try:
                length = float(self.text[start:self.pos])
            except ValueError:
                raise ValueError(f"bad branch length at position {start}") from None
        return {"name": name, "length": length, "children": children}

    def label(self) -> str:
        self.skip_ws()
        if self.peek() == "'":
            self.pos += 1
            out = []
            while True:
                c = self.peek()
                if not c:
                    raise ValueError("unterminated quoted label")
                self.pos += 1
                if c == "'":
                    if self.peek() == "'":
                        self.pos += 1
                        out.append("'")
                        continue
                    break
                out.append(c)
            return "".join(out)
        start = self.pos
        while self.peek() and self.peek() not in ",:;()[]":
            self.pos += 1
        return self.text[start:self.pos].strip()


def _parse(text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("newick must be a non-empty string")
    parser = _Parser(text.strip())
    tree = parser.node()
    parser.skip_ws()
    if parser.peek() == ";":
        parser.pos += 1
    parser.skip_ws()
    if parser.pos != len(parser.text):
        raise ValueError(f"unexpected text after the tree at position {parser.pos}")
    return tree


def parse_newick(newick: str) -> dict[str, Any]:
    """Parse a Newick tree: leaves, internal nodes, branch-length totals, depths and the
    root-to-leaf distances. Unlabelled internal nodes are fine; lengths are optional."""
    tree = _parse(newick)
    leaves: list[str] = []
    root_dist: dict[str, float] = {}
    counts = {"internal": 0, "with_length": 0, "total_length": 0.0, "max_depth": 0}

    def walk(node: dict[str, Any], depth: int, dist: float) -> None:
        length = node["length"] or 0.0
        if node["length"] is not None:
            counts["with_length"] += 1
            counts["total_length"] += length
        here = dist + length
        if node["children"]:
            counts["internal"] += 1
            for child in node["children"]:
                walk(child, depth + 1, here)
        else:
            name = node["name"] or f"leaf{len(leaves) + 1}"
            leaves.append(name)
            root_dist[name] = round(here, 6)
            counts["max_depth"] = max(counts["max_depth"], depth)

    walk(tree, 0, 0.0)
    return {"leaves": leaves, "n_leaves": len(leaves), "n_internal": counts["internal"],
            "total_branch_length": round(counts["total_length"], 6),
            "branches_with_lengths": counts["with_length"], "max_depth": counts["max_depth"],
            "root_to_leaf": root_dist, "is_binary": _is_binary(tree)}


def _is_binary(node: dict[str, Any]) -> bool:
    if not node["children"]:
        return True
    return len(node["children"]) == 2 and all(_is_binary(c) for c in node["children"])


def tree_distances(newick: str) -> dict[str, Any]:
    """Leaf-to-leaf path lengths (patristic distances) from a Newick tree with lengths."""
    tree = _parse(newick)
    paths: dict[str, list[tuple[int, float]]] = {}
    counter = [0]

    def walk(node: dict[str, Any], trail: list[tuple[int, float]]) -> None:
        counter[0] += 1
        me = counter[0]
        here = trail + [(me, node["length"] or 0.0)]
        if node["children"]:
            for child in node["children"]:
                walk(child, here)
        else:
            paths[node["name"] or f"leaf{len(paths) + 1}"] = here

    walk(tree, [])
    names = list(paths)
    matrix = []
    for a in names:
        row = []
        for b in names:
            if a == b:
                row.append(0.0)
                continue
            shared = 0
            for (x, _), (y, _) in zip(paths[a], paths[b]):
                if x != y:
                    break
                shared += 1
            row.append(round(sum(l for _, l in paths[a][shared:]) + sum(l for _, l in paths[b][shared:]), 6))
        matrix.append(row)
    return {"names": names, "matrix": matrix}

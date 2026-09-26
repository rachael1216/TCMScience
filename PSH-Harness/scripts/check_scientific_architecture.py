"""Small, explicit dependency ratchet for newly separated scientific contracts.

This checks static imports (including nested/type-checking imports), not arbitrary
Python execution or the whole historical package graph. No external dependencies.
"""

import ast
from pathlib import Path


RULES = {
    "psh.scientist.models": {"__future__", "dataclasses", "hashlib", "json"},
    "psh.scientist.ports": {"typing", "psh.contracts", "psh.labels", "psh.scientist.models"},
    "psh.workflow.statistics": {"dataclasses", "typing", "psh.scientist.models"},
}


def check_source(module: str, source: str) -> list[str]:
    allowed = RULES[module]
    problems = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{module}:{exc.lineno}: invalid Python syntax"]
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = module.split(".")[:-1]
                if node.level > len(package):
                    problems.append(f"{module}:{node.lineno}: relative import escapes package")
                    continue
                base = ".".join(package[:len(package) - node.level + 1])
                target = base + ("." + node.module if node.module else "")
            else:
                target = node.module or ""
            names = [target] if node.module else [target + "." + a.name for a in node.names]
            if any(a.name == "*" for a in node.names):
                problems.append(f"{module}:{node.lineno}: wildcard import is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"__import__", "eval", "exec"}:
                problems.append(f"{module}:{node.lineno}: dynamic execution/import is not allowed")
        for name in names:
            if name not in allowed:
                problems.append(f"{module}:{node.lineno}: forbidden dependency {name}")
    return sorted(problems)


def check_tree(source_root: Path) -> list[str]:
    problems = []
    for module in RULES:
        path = source_root.joinpath(*module.split(".")).with_suffix(".py")
        if not path.is_file():
            problems.append(f"{module}: managed module missing")
            continue
        problems.extend(check_source(module, path.read_text(encoding="utf-8")))
    return sorted(problems)


if __name__ == "__main__":
    problems = check_tree(Path(__file__).resolve().parents[1] / "src")
    print("\n".join(problems) if problems else "Scientific contract architecture checks passed")
    raise SystemExit(bool(problems))

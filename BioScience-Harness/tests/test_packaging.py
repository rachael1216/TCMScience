"""Release-hygiene tests: what actually ships must be usable.

Three defects reached the v2.1 release and none of them were logic bugs — they
were packaging bugs, invisible to a test suite that only ever ran from a source
checkout:

* the tarball carried ~80 macOS AppleDouble sidecars (`._name`), and pytest
  collects `._test_*.py` as modules, so unpacking the release and running the
  suite died during collection;
* the wheel contained no capability catalogue at all, because the data was never
  declared as package data — `pip install` produced a registry with nothing to
  index;
* `demo_run.py` shipped with an import spliced into the middle of a string
  literal, so it raised SyntaxError on import.

Building a wheel is slow, so that check is marked `integration`; the tree checks
are cheap and always run.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "scripts"))
from make_release import (REQUIRED_IN_WHEEL, find_junk,  # noqa: E402
                          find_unparsable, verify_artifact)


def test_no_appledouble_or_os_metadata_files_in_the_tree() -> None:
    junk = find_junk(REPO)
    assert junk == [], (
        f"{len(junk)} AppleDouble/OS metadata file(s) in the tree, e.g. "
        f"{[str(p.relative_to(REPO)) for p in junk[:5]]}. "
        "Run: python scripts/make_release.py")


def test_every_shipped_python_file_parses() -> None:
    bad = find_unparsable(REPO)
    assert bad == [], f"unparsable python file(s): {bad}"


def test_the_catalogue_is_inside_the_package_not_only_the_repo() -> None:
    """`catalogue_path()` must resolve without a source checkout."""
    from bioagent.config import catalogue_path
    from bioagent.data import CATALOGUE_CSV

    assert CATALOGUE_CSV.exists(), "the packaged catalogue is missing"
    resolved = catalogue_path()
    assert resolved.exists()
    package_root = Path(__import__("bioagent").__file__).resolve().parent
    assert package_root in resolved.parents, (
        f"catalogue_path() resolved to {resolved}, outside the installed package; "
        "an installed wheel would find nothing there")


def test_the_packaged_catalogue_actually_loads() -> None:
    from bioagent import CapabilityRegistry
    from bioagent.config import catalogue_path

    registry = CapabilityRegistry.from_csv(catalogue_path())
    assert len(registry) > 1000, f"catalogue loaded only {len(registry)} rows"


def test_package_data_declares_every_runtime_data_file() -> None:
    """A new file under bioagent/data/ must be matched by pyproject's globs."""
    import fnmatch

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"bioagent.data"' in pyproject, "package-data entry for bioagent.data is missing"
    globs = ["*.csv", "*.json", "*.parquet"]
    for path in (REPO / "src" / "bioagent" / "data").iterdir():
        if path.name in ("__init__.py", "__pycache__"):
            continue
        assert any(fnmatch.fnmatch(path.name, g) for g in globs), (
            f"{path.name} is in bioagent/data/ but no package-data glob matches it; "
            "it would be dropped from the wheel")


def test_every_source_file_is_tracked_by_git() -> None:
    """A .gitignore pattern must never silently drop a package from the commit.

    An unanchored `workspace/` rule also matched `src/bioagent/workspace/`, so
    that package was excluded from a commit while the working tree still had the
    files: the local suite passed and CI failed on ModuleNotFoundError. Only a
    check against what git actually tracks can see this.
    """
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(["git", "ls-files", "src", "tests", "scripts"],
                             cwd=REPO, capture_output=True, text=True)
    if tracked.returncode != 0:
        pytest.skip(f"git unavailable: {tracked.stderr.strip()}")
    known = {line.strip() for line in tracked.stdout.splitlines() if line.strip()}
    on_disk = {
        str(path.relative_to(REPO))
        for base in ("src", "tests", "scripts")
        for path in (REPO / base).rglob("*.py")
        if "__pycache__" not in path.parts and not path.name.startswith("._")
    }
    missing = sorted(on_disk - known)
    assert missing == [], (
        f"{len(missing)} source file(s) exist but are not tracked by git — a "
        f".gitignore pattern is probably matching them: {missing[:8]}")


def test_every_package_directory_is_importable_from_the_installed_package() -> None:
    """Every `src/bioagent/**/__init__.py` must import from the install."""
    import importlib

    pkg_root = REPO / "src" / "bioagent"
    for init in sorted(pkg_root.rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        dotted = ".".join(("bioagent", *init.relative_to(pkg_root).parent.parts))
        importlib.import_module(dotted)


def test_the_declared_version_is_stated_once() -> None:
    """pyproject and __init__ drifted (0.2.2 vs 0.2.1).

    The package version reaches provenance records and release artifacts, so two
    answers means two different claims about which code produced a result.
    """
    import re

    import bioagent

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert declared, "pyproject.toml declares no version"
    assert bioagent.__version__ == declared.group(1), (
        f"__init__.py says {bioagent.__version__}, pyproject.toml says {declared.group(1)}")


def test_required_packages_covers_every_shipped_package() -> None:
    """The declared public-API list must not fall behind the tree.

    The list is what lets the release gate notice a *missing* package; if a new
    package is added and not declared, its disappearance would again go unseen.
    """
    from make_release import REQUIRED_PACKAGES

    pkg_root = REPO / "src" / "bioagent"
    on_disk = {
        ".".join(("bioagent", *init.relative_to(pkg_root).parent.parts))
        for init in pkg_root.rglob("__init__.py")
        if "__pycache__" not in init.parts
    }
    undeclared = sorted(on_disk - set(REQUIRED_PACKAGES))
    assert undeclared == [], (
        f"packages not declared in make_release.REQUIRED_PACKAGES: {undeclared}")


@pytest.mark.integration
def test_the_built_wheel_contains_the_catalogue_and_no_sidecars(tmp_path) -> None:
    """The end-to-end check: build a real wheel and look inside it."""
    pytest.importorskip("build", reason="pip install build")
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path),
                    str(REPO)], check=True, capture_output=True)
    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "no wheel was produced"
    problems = verify_artifact(wheels[0])
    assert problems == [], f"wheel is not releasable: {problems}"
    names = zipfile.ZipFile(wheels[0]).namelist()
    for required in REQUIRED_IN_WHEEL:
        assert required in names

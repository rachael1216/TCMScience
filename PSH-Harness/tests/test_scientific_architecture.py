import importlib.util
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys

import pytest

from psh.labels import DataLabel, Sensitivity
from psh.scientist import Hypothesis, Observation, Deviation, Protocol
from psh.scientist import models, records
from psh.workflow import ProtocolBinding, ScientificCompiler, StatisticalDesign
from test_scientific_workflow import contract, program, policy
from test_scientist_records import protocol


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("science_architecture", ROOT / "scripts/check_scientific_architecture.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_managed_dependency_graph():
    assert checker.check_tree(ROOT / "src") == []


@pytest.mark.parametrize("source", [
    "import sqlite3", "from ..kernel import TrustedKernel",
    "from psh.scientist.records import ScientificLedger",
    "def f():\n    from psh.runtime import Runner",
    "if False:\n    import psh.workflow.compiler",
    "from . import records", "from .models import *",
    "__import__('psh.kernel')", "exec('import psh.kernel')",
    "from ....kernel import TrustedKernel", "this is invalid Python!",
])
def test_forbidden_imports_are_caught(source):
    assert checker.check_source("psh.scientist.models", source)


def test_allowed_absolute_relative_and_type_imports():
    assert checker.check_source("psh.scientist.ports", "from .models import Protocol") == []
    assert checker.check_source("psh.workflow.statistics", "from ..scientist.models import Protocol") == []
    assert checker.check_source("psh.scientist.models", "from dataclasses import dataclass") == []


def test_missing_managed_module_fails_closed(tmp_path):
    assert len(checker.check_tree(tmp_path)) == len(checker.RULES)


def test_existing_model_imports_preserve_identity_and_pickle():
    for name in ("Hypothesis", "Protocol", "Observation", "Deviation"):
        assert getattr(records, name) is getattr(models, name) is globals()[name]
    assert pickle.loads(pickle.dumps(protocol())) == protocol()
    assert protocol().fingerprint == records._hash(json.loads(records._json(records.asdict(protocol()))))


def test_schema_import_does_not_load_scientific_persistence_adapter():
    # psh's legacy root still imports kernel/runtime: only this new seam is lazy.
    code = """
import sys
from psh.scientist import Protocol
from psh.workflow import StatisticalDesign
assert 'psh.scientist.records' not in sys.modules
import psh.scientist as scientist
assert 'ScientificLedger' in dir(scientist)
from psh.scientist import ScientificLedger
assert 'psh.scientist.records' in sys.modules
assert ScientificLedger is scientist.ScientificLedger
try:
    scientist.nonexistent
except AttributeError:
    pass
else:
    raise AssertionError('unknown attribute was accepted')
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_compiler_consumes_structural_port_without_sqlite():
    class Resolver:
        def __init__(self):
            self.calls = []

        def resolve_protocol(self, node_id, envelope):
            self.calls.append((node_id, envelope))
            return protocol(), DataLabel(Sensitivity.INTERNAL)

    resolver = Resolver()
    p = program(contracts={"a": contract(statistics=StatisticalDesign(protocol()),
        protocol_binding=ProtocolBinding("synthetic-record", protocol().fingerprint))})
    result = ScientificCompiler(scientific_ledger=resolver).compile(p, policy().envelope())
    assert result.sensitivities["a"] == Sensitivity.INTERNAL
    assert resolver.calls[0][0] == "synthetic-record"

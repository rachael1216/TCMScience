"""``bioagent doctor``: readiness is reported before a run discovers it."""

from __future__ import annotations

import json

from bioagent.cli import main
from bioagent.doctor import diagnose, render


def test_diagnose_reports_every_section_with_a_verdict(tmp_path):
    report = diagnose(data_lake=tmp_path / "no-lake")
    for section in ("backends", "datasets", "connectors", "native_tools", "psh", "tcm",
                    "problems", "verdict"):
        assert section in report, section
    assert report["verdict"] in ("ready", "degraded", "blocked")
    assert report["backends"]["python"]["available"]
    assert report["datasets"]["exists"] is False and report["datasets"]["fetchable"] >= 20
    assert report["connectors"]["sources"] >= 50
    assert report["native_tools"]["count"] >= 147
    assert report["tcm"]["herbs"] >= 20
    problems = {p["problem"] for p in report["problems"]}
    assert any("no data lake" in p for p in problems)
    assert all(p["remedy"] for p in report["problems"]), "every problem names a remedy"
    json.dumps(report, default=str)


def test_a_present_dataset_counts_as_present(tmp_path):
    lake = tmp_path / "lake"
    lake.mkdir()
    from bioagent.acquisition import BulkDatasetProvider, acquisition_for

    manifest = next(iter(BulkDatasetProvider().discover()))
    spec = acquisition_for(manifest)
    (lake / spec.filename).write_bytes(b"x")
    report = diagnose(data_lake=lake)
    assert report["datasets"]["exists"] and report["datasets"]["present"] >= 1


def test_the_cli_prints_a_readable_report_and_json(tmp_path, capsys):
    code = main(["--dest", str(tmp_path / "no-lake"), "doctor"])
    out = capsys.readouterr().out
    assert code == 0 and "verdict:" in out and "tools" in out and "remedy:" in out
    code = main(["--dest", str(tmp_path / "no-lake"), "doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["verdict"] in ("ready", "degraded")
    assert render(report).startswith("bioagent ")


def test_the_smoke_option_runs_every_native_tool(tmp_path):
    report = diagnose(data_lake=tmp_path / "no-lake", smoke=True)
    smoke = report["native_tools"]["smoke"]
    assert smoke["ran"] >= 147 and smoke["failed"] == []

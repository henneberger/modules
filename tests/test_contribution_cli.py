"""Exercise the CLI through real task preparation and locked evaluation."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from test_environments import environment_source as environment_source

from module_families.cli import main
from module_families.contributions import prepare_contribution
from module_families.environments import sync_environment, verify_environment


@pytest.mark.parametrize("expected,code", [("[0,1,2]", 0), ("[]", 2)])
def test_cli_writes_evidence_and_returns_acceptance_status(environment_source, tmp_path, capsys, expected, code):
    lock, _ = verify_environment(environment_source)
    interface = next(spec for spec in lock["assembly"]["interfaces"] if spec["id"] == "test.call")
    source = tmp_path / "task.toml"
    source.write_text('schema_version=1\n[task]\nid="range"\nsummary="Enumerate positions"\nrequires={id="test.call",version="1"}\n[[cases]]\nid="three"\nexport="run"\nargs=[3]\nexpected=' + expected + '\n')
    repo = Mock(spec=["interface"])
    repo.interface.return_value = interface
    prepare_contribution(source, repo, tmp_path / "task")
    sync_environment(environment_source, tmp_path / "venv")
    task = str(tmp_path / "task/task.json")
    evidence = str(tmp_path / "evidence.json")
    assert main(["evaluate-contribution", task, str(environment_source), "--target", str(tmp_path / "venv"), "--out", evidence]) == code
    report = json.loads((tmp_path / "evidence.json").read_text())
    assert report["status"] == ("passed" if code == 0 else "failed")
    assert main(["verify-evidence", task, str(environment_source), evidence]) == code
    capsys.readouterr()

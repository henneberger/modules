"""Real CLI flow: prepared contract, subprocess worker, offline admission."""

from __future__ import annotations

import json

import pytest
from test_workers import driver
from test_workers import setup as setup
from test_workers import wheelhouse as wheelhouse

from module_families.cli import main


def common(setup, work="cli-work"):
    root, base, staging, _, _ = setup
    return [
        "--queue",
        str(root / "queue.sqlite"),
        "--registry",
        str(base.root),
        "--staging",
        str(staging.root),
        "--work-dir",
        str(root / work),
    ]


def configured_driver(setup):
    root = setup[0]
    path = root / "driver.toml"
    path.write_text("[driver]\ncommand=" + json.dumps(driver(root)) + "\n")
    return path


def test_cli_complete_offline_contribution(setup, wheelhouse, monkeypatch, capsys):
    root, base, _, queue, _ = setup
    # Exact enqueue replay is idempotent; the fixture already queued the contract.
    assert (
        main(
            [
                "enqueue-task",
                str(root / "prepared/task.json"),
                "--id",
                "increment",
                "--queue",
                str(root / "queue.sqlite"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["id"] == "increment"
    assert (
        main(
            [
                "tasks",
                "--queue",
                str(root / "queue.sqlite"),
                "--state",
                "pending",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert (
        main(
            [
                "agent-worker",
                *common(setup),
                "--worker",
                "alice",
                "--driver",
                str(configured_driver(setup)),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "submitted"
    assert not base.candidates("test.call", "1")
    monkeypatch.setenv("MF_EVALUATOR_SECRET", "test-evaluator-secret-" + "x" * 32)
    evidence_path = root / "cli-evidence.sqlite"
    assert (
        main(
            [
                "evaluate-submissions",
                *common(setup, "cli-evaluator"),
                "--evidence-store",
                str(evidence_path),
                "--evaluator",
                "cli-test",
                "--find-links",
                str(wheelhouse),
                "--no-index",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "accepted"
    assert result["attestation"]["payload"]["evaluator"] == "cli-test"
    assert evidence_path.is_file()
    assert queue.get("increment")["state"] == "accepted"
    assert len(base.candidates("test.call", "1")) == 1
    receipt = json.loads(
        next((root / "cli-evaluator").glob("*/receipt.json")).read_text()
    )
    assert (
        main(
            [
                "attest-evidence",
                str(root / "prepared/task.json"),
                receipt["environment"],
                receipt["evidence"],
                "--evidence-store",
                str(evidence_path),
                "--evaluator",
                "cli-test",
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["sha256"] == result["attestation"]["sha256"]
    )
    assert (
        main(["tasks", "--queue", str(root / "queue.sqlite"), "--state", "accepted"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)[0]["id"] == "increment"


@pytest.mark.parametrize("secret", [None, "too-short"])
def test_cli_evaluator_requires_valid_secret(setup, monkeypatch, capsys, secret):
    if secret is None:
        monkeypatch.delenv("MF_EVALUATOR_SECRET", raising=False)
    else:
        monkeypatch.setenv("MF_EVALUATOR_SECRET", secret)
    root = setup[0]
    assert (
        main(
            [
                "evaluate-submissions",
                *common(setup),
                "--evidence-store",
                str(root / "evidence.sqlite"),
                "--evaluator",
                "test",
            ]
        )
        == 2
    )
    assert "secret" in json.loads(capsys.readouterr().err)["message"]
    assert setup[3].get("increment")["state"] == "pending"


@pytest.mark.parametrize("alias", ["direct", "relative", "symlink"])
def test_cli_rejects_same_accepted_and_staging(setup, capsys, alias):
    root, base, _, _, _ = setup
    staging = base.root
    if alias == "relative":
        staging = base.root / ".." / base.root.name
    elif alias == "symlink":
        staging = root / "base-alias"
        staging.symlink_to(base.root, target_is_directory=True)
    args = common(setup)
    args[args.index("--staging") + 1] = str(staging)
    assert (
        main(
            [
                "agent-worker",
                *args,
                "--worker",
                "alice",
                "--driver",
                str(configured_driver(setup)),
            ]
        )
        == 2
    )
    assert "distinct" in json.loads(capsys.readouterr().err)["message"]
    assert setup[3].get("increment")["state"] == "pending"


def test_cli_driver_contract_is_operator_configuration(setup, capsys):
    root = setup[0]
    path = root / "invalid-driver.toml"
    path.write_text('[driver]\ncommand=[]\nextra="never execute"\n')
    assert (
        main(
            ["agent-worker", *common(setup), "--worker", "alice", "--driver", str(path)]
        )
        == 2
    )
    assert "driver TOML" in json.loads(capsys.readouterr().err)["message"]
    assert setup[3].get("increment")["state"] == "pending"


def test_cli_bounded_listing_errors(setup, capsys):
    assert (
        main(["tasks", "--queue", str(setup[0] / "queue.sqlite"), "--limit", "1001"])
        == 2
    )
    assert "pagination" in json.loads(capsys.readouterr().err)["message"]


def test_cli_drops_custom_token_name_even_model_auth_allowlist(
    setup, monkeypatch, capsys
):
    import sys

    root = setup[0]
    monkeypatch.setenv("OPENAI_API_KEY", "this-is-a-repository-admin-token")
    command = [
        sys.executable,
        "-c",
        "import os,json,pathlib;pathlib.Path('observed.json').write_text(json.dumps(dict(os.environ)))",
    ]
    config = root / "observe.toml"
    config.write_text("[driver]\ncommand=" + json.dumps(command) + "\n")
    assert (
        main(
            [
                "agent-worker",
                *common(setup),
                "--worker",
                "alice",
                "--driver",
                str(config),
                "--token-env",
                "OPENAI_API_KEY",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    environment = json.loads((root / result["workspace"] / "observed.json").read_text())
    assert "OPENAI_API_KEY" not in environment

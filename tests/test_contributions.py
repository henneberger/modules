from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from module_families import contributions as c
from module_families.interfaces import validate_interface
from module_families.registry import canonical_bytes

SPEC = validate_interface(
    {
        "id": "kb.search",
        "version": "1",
        "callables": {
            "search": {
                "parameters": [
                    {"name": "query", "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                ],
            }
        },
    }
)
SOURCE = """schema_version = 1
[task]
id = "search-citations"
summary = "Find a document and preserve its citation"
requires = {id="kb.search",version="1"}
capabilities = ["citations"]
[policy]
allowed_effects = ["storage"]
[[cases]]
id = "find-document"
export = "search"
args = ["retention"]
expected = [{document="policy-1",page=2}]
"""


def save(path, value, rehash=False):
    if rehash:
        value["sha256"] = hashlib.sha256(
            canonical_bytes({k: v for k, v in value.items() if k != "sha256"})
        ).hexdigest()
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def task(tmp_path):
    source = tmp_path / "contribution.toml"
    source.write_text(SOURCE)
    repository = Mock(spec=["interface"])
    repository.interface.return_value = SPEC
    bundle = c.prepare_contribution(source, repository, tmp_path / "prepared")
    repository.interface.assert_called_once_with("kb.search", "1")
    return tmp_path / "prepared/task.json", bundle


@pytest.fixture
def runtime(monkeypatch):
    card = {
        "family": "kb",
        "id": "search",
        "version": "1",
        "sha256": "a" * 64,
        "provides": {"id": "kb.search", "version": "1"},
        "requires": {},
        "effects": ["storage"],
        "capabilities": ["citations"],
    }
    lock = {
        "sha256": "environment",
        "assembly": {
            "sha256": "program",
            "expression": {"use": "search"},
            "bindings": {"search": {"member": card}},
            "effects": ["storage"],
            "interfaces": [SPEC],
        },
    }
    monkeypatch.setattr(c, "verify_environment", Mock(return_value=(lock, None)))
    execute = Mock(
        return_value={
            "assembly": "program",
            "export": "search",
            "result": [{"document": "policy-1", "page": 2}],
        }
    )
    monkeypatch.setattr(c, "run_environment", execute)
    return lock, execute


def test_portable_task_without_provider_access(task, tmp_path):
    path, bundle = task
    assert json.loads(path.read_text()) == bundle
    assert bundle["interface"] == SPEC
    assert (
        bundle["sha256"]
        == hashlib.sha256(
            canonical_bytes({k: v for k, v in bundle.items() if k != "sha256"})
        ).hexdigest()
    )
    other = tmp_path / "second.toml"
    other.write_text(SOURCE)
    repo = Mock(spec=["interface"], interface=Mock(return_value=SPEC))
    assert c.prepare_contribution(other, repo, tmp_path / "second") == bundle


@pytest.mark.parametrize(
    "replacement",
    [
        ("schema_version = 1", "schema_version = true"),
        ("schema_version = 1", "schema_version = 1\nunknown = 1"),
        ('summary = "Find a document and preserve its citation"', 'summary = ""'),
        ('capabilities = ["citations"]', 'capabilities = ["citations", "citations"]'),
        ('allowed_effects = ["storage"]', 'allowed_effects = ["storage"]\nunknown = 1'),
        ('args = ["retention"]', 'args = "retention"'),
        ('args = ["retention"]', "args = [1979-05-27]"),
        ('expected = [{document="policy-1",page=2}]', "expected = nan"),
        ('expected = [{document="policy-1",page=2}]', "unknown = 1\nexpected = []"),
    ],
)
def test_strict_authoring(tmp_path, replacement):
    path = tmp_path / "bad.toml"
    path.write_text(SOURCE.replace(*replacement))
    with pytest.raises(c.ContributionError):
        c.read_contribution(path)


@pytest.mark.parametrize("change", ["missing", "duplicate", "arity", "export"])
def test_case_contract_validation(tmp_path, change):
    text = SOURCE
    if change == "missing":
        text = text[: text.index("[[cases]]")]
    elif change == "duplicate":
        text += text[text.index("[[cases]]") :]
    elif change == "arity":
        text = text.replace('args = ["retention"]', "args = []")
    else:
        text = text.replace('export = "search"', 'export = "missing"')
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(c.ContributionError):
        c.prepare_contribution(
            path, Mock(interface=Mock(return_value=SPEC)), tmp_path / "out"
        )


def test_pass_and_offline_verification(task, runtime, tmp_path):
    path, bundle = task
    report = c.evaluate_contribution(path, "lock", "venv", timeout=12)
    assert report["status"] == "passed"
    assert report["task_sha256"] == bundle["sha256"]
    runtime[1].assert_called_once_with(
        "lock", "venv", export="search", args=["retention"], timeout=12
    )
    report_path = save(tmp_path / "evidence.json", report)
    runtime[1].reset_mock()
    assert c.verify_evidence(path, "lock", report_path) == report
    runtime[1].assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["interface", "interface-body", "capability", "effect"]
)
def test_selection_must_satisfy_task(task, runtime, mutation):
    lock, execute = runtime
    card = lock["assembly"]["bindings"]["search"]["member"]
    if mutation == "interface":
        card["provides"] = {"id": "unrelated", "version": "1"}
    elif mutation == "interface-body":
        lock["assembly"]["interfaces"] = []
    elif mutation == "capability":
        card["capabilities"] = []
    else:
        card["effects"] = ["network"]
    with pytest.raises(c.ContributionError):
        c.evaluate_contribution(task[0], "lock", "venv")
    execute.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["wrong-result", "exception", "timeout", "provenance", "boolean-is-not-integer"],
)
def test_execution_failure_is_failed_evidence(task, runtime, tmp_path, failure):
    execute = runtime[1]
    if failure == "wrong-result":
        execute.return_value["result"] = []
    elif failure == "exception":
        execute.side_effect = ValueError("adapter exploded")
    elif failure == "timeout":
        execute.side_effect = subprocess.TimeoutExpired("worker", 0.1)
    elif failure == "provenance":
        execute.return_value["assembly"] = "unrelated-program"
    else:
        execute.return_value["result"] = [{"document": "policy-1", "page": True}]
    report = c.evaluate_contribution(task[0], "lock", "venv")
    assert report["status"] == "failed"
    with pytest.raises(c.ContributionError):
        c.verify_evidence(task[0], "lock", save(tmp_path / "failed.json", report))


@pytest.mark.parametrize(
    "field",
    ["task_sha256", "environment_sha256", "program_sha256", "cases", "trust", "status"],
)
def test_rehashed_tampering_rejected(task, runtime, tmp_path, field):
    report = c.evaluate_contribution(task[0], "lock", "venv")
    report[field] = [] if field == "cases" else "changed"
    with pytest.raises(c.ContributionError):
        c.verify_evidence(task[0], "lock", save(tmp_path / "forged.json", report, True))


def test_rehashed_failed_case_cannot_be_made_passed(task, runtime, tmp_path):
    report = c.evaluate_contribution(task[0], "lock", "venv")
    report["cases"][0]["actual"] = []
    with pytest.raises(c.ContributionError, match="unaccepted"):
        c.verify_evidence(task[0], "lock", save(tmp_path / "forged.json", report, True))


def test_unhashed_changes_rejected(task, runtime, tmp_path):
    report = c.evaluate_contribution(task[0], "lock", "venv")
    report["timeout_seconds"] = 50
    with pytest.raises(c.ContributionError, match="hash"):
        c.verify_evidence(task[0], "lock", save(tmp_path / "changed.json", report))
    bundle = copy.deepcopy(task[1])
    bundle["document"]["cases"][0]["expected"] = []
    save(task[0], bundle)
    with pytest.raises(c.ContributionError, match="hash"):
        c.evaluate_contribution(task[0], "lock", "venv")


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), "30"])
def test_invalid_timeout(task, runtime, timeout):
    with pytest.raises(c.ContributionError, match="timeout"):
        c.evaluate_contribution(task[0], "lock", "venv", timeout=timeout)


def test_cases_execute_separately_even_after_failure(task, runtime, tmp_path):
    bundle = copy.deepcopy(task[1])
    bundle["document"]["cases"].append(
        {**bundle["document"]["cases"][0], "id": "second"}
    )
    save(task[0], bundle, True)
    runtime[1].side_effect = [
        ValueError("first failed"),
        {
            "assembly": "program",
            "export": "search",
            "result": [{"document": "policy-1", "page": 2}],
        },
    ]
    report = c.evaluate_contribution(task[0], "lock", "venv")
    assert [case["status"] for case in report["cases"]] == ["failed", "passed"]
    assert runtime[1].call_count == 2


def test_real_locked_worker_evaluation(tmp_path):
    # Reuse the existing offline wheel fixture's construction, with no network.
    from test_environments import environment_source

    from module_families.environments import sync_environment, verify_environment

    factory = Mock()
    factory.mktemp.return_value = tmp_path / "real-environment"
    factory.mktemp.return_value.mkdir()
    lock_path = environment_source.__wrapped__(factory)
    lock, _ = verify_environment(lock_path)
    interface = next(
        item for item in lock["assembly"]["interfaces"] if item["id"] == "test.call"
    )
    source = tmp_path / "real.toml"
    source.write_text("""schema_version = 1
[task]
id = "count"
summary = "Return all integer positions"
requires = {id="test.call",version="1"}
[[cases]]
id = "three"
export = "run"
args = [3]
expected = [0,1,2]
""")
    prepared = tmp_path / "real-task"
    c.prepare_contribution(
        source, Mock(interface=Mock(return_value=interface)), prepared
    )
    target = tmp_path / "venv"
    sync_environment(lock_path, target)
    report = c.evaluate_contribution(prepared / "task.json", lock_path, target)
    assert report["status"] == "passed", report
    assert (
        c.verify_evidence(
            prepared / "task.json",
            lock_path,
            save(tmp_path / "real-evidence.json", report),
        )
        == report
    )
    timed_out = c.evaluate_contribution(
        prepared / "task.json", lock_path, target, timeout=0.000001
    )
    assert timed_out["status"] == "failed"
    assert "TimeoutExpired" in timed_out["cases"][0]["error"]


@pytest.mark.parametrize("suffix", [".json", ".txt", ""])
def test_authoring_requires_toml_extension(tmp_path, suffix):
    path = tmp_path / ("contribution" + suffix)
    path.write_text(SOURCE)
    with pytest.raises(c.ContributionError, match=r"\.toml"):
        c.read_contribution(path)

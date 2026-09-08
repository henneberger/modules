"""Evidence gates apply to resolved executable environments, not just modules."""

from __future__ import annotations

import copy
import json
import shutil

import pytest
from test_environments import environment_source as environment_source
from test_environments import seal
from test_evidence import TASK, TRUST, attestation
from test_synthesis import goal

from module_families import environments as env
from module_families import evidence as e
from module_families.assemblies import lock_assembly, verify_assembly
from module_families.registry import Registry
from module_families.synthesis import synthesize
from module_families.wheels import build_wheel


@pytest.fixture
def evidence_environment(environment_source, tmp_path):
    source, _ = env.verify_environment(environment_source)
    repository = Registry(environment_source.parent.parent / "repository")
    request = goal()
    candidate = lock_assembly(synthesize(request, repository), repository)
    store = e.EvidenceStore(tmp_path / "evidence.sqlite")
    record = attestation(candidate, environment=e.environment_fingerprint(source))
    store.add(record, TRUST)
    request["evidence"] = {"tasks": [TASK], "evaluators": ["ci"]}
    result = synthesize(request, repository, evidence_store=store, trust_keys=TRUST)
    checked = lock_assembly(result, repository, trust_keys=TRUST)
    directory = tmp_path / "locked"
    shutil.copytree(environment_source.parent, directory)
    path = directory / "environment.lock.json"
    source["assembly"] = checked
    seal(path, source)
    return path, repository


def _pin_evaluated_runtime(monkeypatch, path):
    lock = json.loads(path.read_text())
    original = path.parent / "wheels" / lock["runtime"]["filename"]

    def runtime(directory):
        destination = directory / original.name
        shutil.copyfile(original, destination)
        return destination

    monkeypatch.setattr(env, "_runtime_wheel", runtime)


def _replace_wheel(path, lock, distribution):
    original = next(w for w in lock["wheels"] if w["distribution"] == distribution)
    directory = path.parent / "wheels"
    artifact = build_wheel(
        directory,
        distribution=distribution,
        version=original["version"],
        files={
            distribution.replace("-", "_")
            + "/__init__.py": "# different valid implementation\n"
        },
        requires_dist=original["requires_dist"],
    )
    replacement = env._wheel(directory / artifact["filename"])
    lock["wheels"] = [
        replacement if w["distribution"] == distribution else w for w in lock["wheels"]
    ]
    if distribution == "module-families":
        lock["runtime"] = replacement


def test_exact_environment_relocks_and_verifies_with_evidence(
    evidence_environment, tmp_path, monkeypatch
):
    path, repository = evidence_environment
    lock, _ = env.verify_environment(path)
    assert verify_assembly(lock["assembly"], trust_keys=TRUST)["evidence"][
        "authenticated"
    ]
    _pin_evaluated_runtime(monkeypatch, path)
    built = env.lock_environment(
        lock["assembly"],
        repository,
        tmp_path / "relocked",
        find_links=[str(path.parent / "wheels")],
        no_index=True,
    )
    actual, _ = env.verify_environment(built["lock"])
    assert e.environment_fingerprint(actual) == e.environment_fingerprint(lock)
    assert actual["assembly"]["evidence"] == lock["assembly"]["evidence"]


@pytest.mark.parametrize("distribution", ["module-families", "packaging"])
def test_rehashed_valid_replacement_wheel_needs_new_evidence(
    evidence_environment, distribution
):
    path, _ = evidence_environment
    lock = json.loads(path.read_text())
    _replace_wheel(path, lock, distribution)
    seal(path, lock)
    # Wheel RECORD, dependency closure and environment hashes are valid. The
    # evaluator has simply never observed this executable implementation.
    with pytest.raises(
        env.EnvironmentError, match="matching trusted evaluation evidence"
    ):
        env.verify_environment(path)


def test_changed_current_interpreter_cannot_reuse_evidence(
    evidence_environment, monkeypatch
):
    path, _ = evidence_environment
    lock = json.loads(path.read_text())
    changed = copy.deepcopy(lock["interpreter"])
    changed["binary_sha256"] = "f" * 64
    lock["interpreter"] = changed
    monkeypatch.setattr(env, "interpreter", lambda python: changed)
    seal(path, lock)
    with pytest.raises(
        env.EnvironmentError, match="matching trusted evaluation evidence"
    ):
        env.verify_environment(path)


@pytest.mark.parametrize("changed", ["runtime", "interpreter", "dependency"])
def test_resolving_unevaluated_environment_fails_before_writing_lock(
    evidence_environment, tmp_path, monkeypatch, changed
):
    path, repository = evidence_environment
    lock = json.loads(path.read_text())
    _pin_evaluated_runtime(monkeypatch, path)
    available = path.parent / "wheels"
    if changed == "runtime":

        def runtime(directory):
            original = lock["runtime"]
            artifact = build_wheel(
                directory,
                distribution="module-families",
                version=original["version"],
                files={"module_families/__init__.py": "# modified runtime\n"},
                requires_dist=original["requires_dist"],
            )
            return directory / artifact["filename"]

        monkeypatch.setattr(env, "_runtime_wheel", runtime)
    elif changed == "interpreter":
        fingerprint = copy.deepcopy(lock["interpreter"])
        fingerprint["binary_sha256"] = "f" * 64
        monkeypatch.setattr(env, "interpreter", lambda python: fingerprint)
    else:
        # Keep runtime copy stable while replacing an available third-party wheel.
        _replace_wheel(path, lock, "packaging")
    destination = tmp_path / "rejected"
    with pytest.raises(
        env.EnvironmentError, match="matching trusted evaluation evidence"
    ):
        env.lock_environment(
            lock["assembly"],
            repository,
            destination,
            find_links=[str(available)],
            no_index=True,
        )
    assert not (destination / "environment.lock.json").exists()
    assert not (destination / "wheels").exists()


def test_each_task_needs_evidence_in_the_actual_environment(evidence_environment):
    path, _ = evidence_environment
    lock = json.loads(path.read_text())
    assembly = lock["assembly"]
    second_task = "f" * 64
    assembly["synthesis"]["request"]["evidence"]["tasks"].append(second_task)
    assembly["evidence"]["policy"]["tasks"] = [TASK, second_task]
    assembly["evidence"]["observations"].append(
        attestation(assembly, task=second_task, environment="9" * 64)
    )
    assembly["sha256"] = e._digest({k: v for k, v in assembly.items() if k != "sha256"})
    seal(path, lock)
    assert verify_assembly(assembly, trust_keys=TRUST)["evidence"]["authenticated"]
    with pytest.raises(
        env.EnvironmentError, match="matching trusted evaluation evidence"
    ):
        env.verify_environment(path)


def test_candidate_process_does_not_inherit_operator_credentials(monkeypatch):
    import os
    import sys

    monkeypatch.setenv("MF_EVALUATOR_SECRET", "private-evaluator-seed")
    monkeypatch.setenv("CUSTOM_SIGNER_ENV", "custom-private-seed")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-cloud-credential")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/operator-agent.sock")
    monkeypatch.setenv("PYTHONPATH", "/operator/imports")
    monkeypatch.setenv("PATH", "/operator/untrusted-bin")
    process = env._candidate_process(
        [
            sys.executable,
            "-I",
            "-c",
            "import json,os; print(json.dumps({'environment':dict(os.environ),'cwd':os.getcwd()}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(process.stdout)
    assert not {
        "MF_EVALUATOR_SECRET",
        "CUSTOM_SIGNER_ENV",
        "AWS_SECRET_ACCESS_KEY",
        "SSH_AUTH_SOCK",
        "PYTHONPATH",
    } & set(data["environment"])
    assert data["environment"]["PATH"] == os.defpath
    assert os.path.realpath(data["cwd"]) == os.path.realpath(data["environment"]["HOME"])
    assert data["environment"]["HOME"] != os.environ["HOME"]


def test_installed_startup_hooks_do_not_inherit_evaluator_secret(
    environment_source, tmp_path, monkeypatch
):
    import sysconfig
    from pathlib import Path

    monkeypatch.setenv("MF_EVALUATOR_SECRET", "must-not-reach-pth-startup")
    target = tmp_path / "startup-venv"
    env.sync_environment(environment_source, target)
    # .pth code executes before -c or -m, including interpreter verification.
    purelib = sysconfig.get_path(
        "purelib", vars={"base": str(target), "platbase": str(target)}
    )
    probe = tmp_path / "startup-result.json"
    hook = Path(purelib) / "evidence_probe.pth"
    hook.write_text(
        "import os,pathlib,json; pathlib.Path("
        + repr(str(probe))
        + ").write_text(json.dumps(dict(os.environ)))\n"
    )
    env.interpreter(str(target / "bin/python"))
    observed = json.loads(probe.read_text())
    assert "MF_EVALUATOR_SECRET" not in observed

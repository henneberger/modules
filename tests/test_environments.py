from __future__ import annotations

import hashlib
import importlib.metadata
import itertools
import json
import shutil
import zipfile
from pathlib import Path

import packaging
import pytest
from test_assemblies import CALL, interface, provider, publish, request

from module_families.assemblies import (
    AssemblyError,
    lock_assembly,
    read_assembly,
    resolve_assembly,
)
from module_families.environments import (
    EnvironmentError,
    _validate_dependencies,
    _wheel,
    interpreter,
    lock_environment,
    run_environment,
    sync_environment,
    verify_environment,
)
from module_families.registry import Registry, canonical_bytes
from module_families.synthesis import SynthesisError, read_goal
from module_families.wheels import build_wheel
from module_families.worker import _result


def seal(path, document):
    document["sha256"] = hashlib.sha256(
        canonical_bytes(
            {key: value for key, value in document.items() if key != "sha256"}
        )
    ).hexdigest()
    path.write_text(json.dumps(document))


@pytest.fixture(scope="module")
def environment_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("offline-environment")
    repository = Registry(root / "repository")
    publish(
        root, repository, "interfaces", interfaces=[interface(CALL, callables=["run"])]
    )
    publish(
        root,
        repository,
        "alpha",
        [provider()],
        "def run(value):\n    print('candidate stdout')\n    return iter(range(value))\n",
    )
    assembly = lock_assembly(resolve_assembly(request(), repository), repository)
    links = root / "available-wheels"
    links.mkdir()
    available = (
        Path(__file__).resolve().parents[1]
        / ".mf/upstream-wheels/packaging-26.3-py3-none-any.whl"
    )
    if available.is_file():
        shutil.copyfile(available, links / available.name)
    else:
        # Tests remain offline when the demo's downloaded wheel is absent.
        package = Path(packaging.__file__).parent
        files = {
            "packaging/" + path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        distribution = importlib.metadata.distribution("packaging")
        licenses = {
            Path(str(path)).name: distribution.locate_file(path).read_bytes()
            for path in distribution.files or []
            if "/licenses/" in str(path)
        }
        build_wheel(
            links,
            distribution="packaging",
            version=distribution.version,
            files=files,
            license_files=licenses,
        )
    result = lock_environment(
        assembly, repository, root / "locked", find_links=[str(links)], no_index=True
    )
    return Path(result["lock"])


@pytest.fixture
def locked_environment(tmp_path, environment_source):
    directory = tmp_path / "locked"
    shutil.copytree(environment_source.parent, directory)
    return directory / environment_source.name


def test_actual_offline_lock_sync_and_execute(
    environment_source, tmp_path, monkeypatch
):
    # A bogus index setting cannot affect offline replay.
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:1/unavailable")
    lock, wheelhouse = verify_environment(environment_source)
    assert {item["distribution"] for item in lock["wheels"]} >= {
        "packaging",
        "module-families",
    }
    assert len(lock["wheels"]) == 4
    assert wheelhouse.is_dir()
    synced = sync_environment(environment_source, tmp_path / "venv")
    assert synced["offline"] is True
    result = run_environment(
        environment_source, synced["target"], export="run", args=[3]
    )
    assert result == {
        "assembly": lock["assembly"]["sha256"],
        "export": "run",
        "result": [0, 1, 2],
    }
    with pytest.raises(EnvironmentError, match="already exists"):
        sync_environment(environment_source, synced["target"])


def test_tampered_wheel_is_rejected_before_creating_environment(
    locked_environment, tmp_path
):
    lock = json.loads(locked_environment.read_text())
    record = next(
        item for item in lock["wheels"] if item["distribution"] == "packaging"
    )
    wheel = locked_environment.parent / "wheels" / record["filename"]
    wheel.write_bytes(wheel.read_bytes() + b"changed wheel")
    with pytest.raises(EnvironmentError, match="integrity"):
        sync_environment(locked_environment, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_resealed_lock_cannot_omit_a_required_dependency(locked_environment, tmp_path):
    lock = json.loads(locked_environment.read_text())
    lock["wheels"] = [
        item for item in lock["wheels"] if item["distribution"] != "packaging"
    ]
    seal(locked_environment, lock)
    with pytest.raises(EnvironmentError, match="dependency is missing"):
        sync_environment(locked_environment, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_valid_wheel_with_incompatible_dependency_version_is_rejected(
    locked_environment,
):
    lock = json.loads(locked_environment.read_text())
    wheels = locked_environment.parent / "wheels"
    replacement = build_wheel(
        wheels,
        distribution="packaging",
        version="1.0.0",
        files={"packaging/__init__.py": "__version__ = '1.0.0'\n"},
    )
    record = _wheel(wheels / replacement["filename"])
    lock["wheels"] = [
        record if item["distribution"] == "packaging" else item
        for item in lock["wheels"]
    ]
    seal(locked_environment, lock)
    with pytest.raises(EnvironmentError, match="dependency version mismatch"):
        verify_environment(locked_environment)


def test_record_hash_is_checked_even_if_outer_wheel_hash_is_resealed(
    locked_environment,
):
    lock = json.loads(locked_environment.read_text())
    record = next(
        item for item in lock["wheels"] if item["distribution"] == "packaging"
    )
    wheel = locked_environment.parent / "wheels" / record["filename"]
    with zipfile.ZipFile(wheel) as archive:
        entries = {item.filename: archive.read(item) for item in archive.infolist()}
    source = next(name for name in entries if name.endswith(".py"))
    entries[source] += b"\n# modified\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    record["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    seal(locked_environment, lock)
    with pytest.raises(EnvironmentError, match="RECORD hash mismatch"):
        verify_environment(locked_environment)


def test_interpreter_fingerprint_is_committed(locked_environment):
    lock = json.loads(locked_environment.read_text())
    lock["interpreter"]["binary_sha256"] = "0" * 64
    seal(locked_environment, lock)
    with pytest.raises(EnvironmentError, match="interpreter differs"):
        verify_environment(locked_environment)


def test_dependency_markers_extras_and_requires_python_are_checked():
    fingerprint = interpreter()

    def record(name, requires):
        return {
            "distribution": name,
            "version": "1.0.0",
            "requires_dist": requires,
            "requires_python": ">=3.11",
        }

    records = {
        "root": record("root", ["child[feature]>=1"]),
        "child": record("child", ['missing>=1; extra == "feature"']),
    }
    with pytest.raises(EnvironmentError, match="missing"):
        _validate_dependencies(records, fingerprint)
    records["root"]["requires_dist"] = ["child>=1"]
    _validate_dependencies(records, fingerprint)
    records["root"]["requires_dist"] += ['missing; python_version < "0"']
    _validate_dependencies(records, fingerprint)
    records["child"]["requires_python"] = ">=999"
    with pytest.raises(EnvironmentError, match="incompatible Python"):
        _validate_dependencies(records, fingerprint)


def test_worker_supports_async_values_and_bounds_iterator_results():
    async def value():
        return 42

    async def values():
        for number in range(3):
            yield number

    assert _result(value()) == 42
    assert _result(values()) == [0, 1, 2]
    with pytest.raises(ValueError, match="10,000"):
        _result(itertools.count())


@pytest.mark.parametrize(
    "reader,error", [(read_goal, SynthesisError), (read_assembly, AssemblyError)]
)
def test_authored_goal_and_assembly_files_are_toml_only(tmp_path, reader, error):
    path = tmp_path / "authored.json"
    path.write_text("{}")
    with pytest.raises(error, match="TOML"):
        reader(path)


def test_invalid_worker_arguments_fail_before_a_process_starts(tmp_path):
    with pytest.raises(EnvironmentError, match="args must be a list"):
        run_environment(
            tmp_path / "missing", tmp_path / "missing-env", export="run", args={}
        )

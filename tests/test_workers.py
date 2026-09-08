from __future__ import annotations

import importlib.metadata
import json
import sys
import threading
from pathlib import Path

import packaging
import pytest
from test_assemblies import CALL, interface, publish

from module_families.contributions import prepare_contribution
from module_families.coordination import (
    CoordinationClient,
    CoordinationError,
    CoordinationQueue,
    coordination_server,
)
from module_families.evidence import EvidenceStore
from module_families.registry import Registry, RegistryError
from module_families.wheels import build_wheel
from module_families.workers import (
    OverlayRepository,
    WorkerError,
    evaluate_submission,
    run_worker_once,
)


@pytest.fixture
def setup(tmp_path):
    base, staging = Registry(tmp_path / "base"), Registry(tmp_path / "staging")
    publish(
        tmp_path, base, "interfaces", interfaces=[interface(CALL, callables=["run"])]
    )
    source = tmp_path / "task.toml"
    source.write_text("""schema_version=1
[task]
id="increment"
summary="Increment a value"
requires={id="test.call",version="1"}
capabilities=[]
[policy]
allowed_effects=[]
[[cases]]
id="seven"
export="run"
args=[7]
expected=8
""")
    payload = prepare_contribution(source, base, tmp_path / "prepared")
    queue = CoordinationQueue(tmp_path / "queue.sqlite")
    task = queue.enqueue(payload, task_id="increment")
    return tmp_path, base, staging, queue, task


@pytest.fixture(scope="module")
def wheelhouse(tmp_path_factory):
    root = tmp_path_factory.mktemp("worker-wheels")
    package = Path(packaging.__file__).parent
    distribution = importlib.metadata.distribution("packaging")
    build_wheel(
        root,
        distribution="packaging",
        version=distribution.version,
        files={
            "packaging/" + path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        },
        license_files={
            Path(str(path)).name: distribution.locate_file(path).read_bytes()
            for path in distribution.files or []
            if "/licenses/" in str(path)
        },
    )
    return root


def driver(root, correct=True):
    manifest = """schema_version=1
[family]
name="worker-example"
version="1.0.0"
description="Independent worker contribution"
[publisher]
name="agent"
[source]
root="src"
package="increment"
[[members]]
id="run"
kind="operation"
symbol="increment:run"
summary="Increment"
version="1.0.0"
provides={id="test.call",version="1"}
requires={}
capabilities=[]
effects=[]
"""
    script = root / "driver.py"
    script.write_text(
        'import pathlib,json\nroot=pathlib.Path.cwd()\n(root/"src/increment").mkdir(parents=True)\n'
        + f'(root/"src/increment/__init__.py").write_text({"def run(value): return value + " + str(1 if correct else 2)!r})\n'
        + f'(root/"family.toml").write_text({manifest!r})\n'
        + '(root/"proposal.json").write_text(json.dumps({"kind":"family","manifest":"family.toml"}))\n'
    )
    return [sys.executable, str(script)]


def run(setup, command=None, **kwargs):
    root, base, staging, queue, _ = setup
    return run_worker_once(
        queue,
        base,
        staging,
        worker="alice",
        driver=command or driver(root),
        work_root=root / "workers",
        **kwargs,
    )


def evaluate(setup, wheelhouse, **kwargs):
    root, base, staging, queue, task = setup
    return evaluate_submission(
        queue,
        task["id"],
        base,
        staging,
        work_root=root / "evaluations",
        evidence_store=EvidenceStore(root / "evidence.sqlite"),
        evaluator_id="test-evaluator",
        secret=b"x" * 32,
        find_links=[str(wheelhouse)],
        no_index=True,
        **kwargs,
    )


def test_real_driver_evaluation_admission_and_replay(setup, wheelhouse):
    root, base, staging, queue, task = setup
    result = run(setup)
    assert result["status"] == "submitted"
    assert not base.candidates("test.call", "1")
    accepted = evaluate(setup, wheelhouse)
    assert accepted["status"] == "accepted"
    assert accepted["attestation"]["payload"]["status"] == "passed"
    assert queue.get(task["id"])["state"] == "accepted"
    assert len(base.candidates("test.call", "1")) == 1
    assert evaluate(setup, wheelhouse) == accepted


def test_failed_case_never_publishes(setup, wheelhouse):
    run(setup, driver(setup[0], correct=False))
    rejected = evaluate(setup, wheelhouse)
    assert rejected["status"] == "rejected"
    assert setup[3].get("increment")["state"] == "pending"
    assert not setup[1].candidates("test.call", "1")


def test_publication_receipt_recovers_ack_failure(setup, wheelhouse, monkeypatch):
    run(setup)
    queue = setup[3]
    accept = queue.accept

    def unavailable(**kwargs):
        raise OSError("queue disconnected after publication")

    monkeypatch.setattr(queue, "accept", unavailable)
    with pytest.raises(OSError):
        evaluate(setup, wheelhouse)
    assert queue.get("increment")["state"] == "submitted"
    assert len(setup[1].candidates("test.call", "1")) == 1
    monkeypatch.setattr(queue, "accept", accept)
    # Receipt prevents a second execution of candidate code.
    import module_families.workers as workers

    monkeypatch.setattr(
        workers,
        "evaluate_contribution",
        lambda *a, **k: pytest.fail("replayed candidate"),
    )
    assert evaluate(setup, wheelhouse)["status"] == "accepted"


def test_driver_timeout_and_retry(setup, wheelhouse):
    result = run(
        setup, [sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05
    )
    assert result["status"] == "submitted"
    assert "timeout" in setup[3].get("increment")["submission"]["error"]
    assert evaluate(setup, wheelhouse)["state"] == "pending"
    assert run(setup)["status"] == "submitted"


def test_manifest_escape_rejected(setup):
    code = 'import json;open("proposal.json","w").write(json.dumps({"kind":"family","manifest":"../escape.toml"}))'
    run(setup, [sys.executable, "-c", code])
    assert "inside worker workspace" in setup[3].get("increment")["submission"]["error"]


def test_stale_fenced_submission_not_hidden(setup, monkeypatch):
    queue = setup[3]

    def stale(**kwargs):
        raise CoordinationError("stale lease")

    monkeypatch.setattr(queue, "submit", stale)
    with pytest.raises(CoordinationError, match="stale"):
        run(setup)


def test_worker_over_real_http_queue(setup):
    root, base, staging, queue, _ = setup
    secret = "worker-token-0123456789"
    server = coordination_server(queue, {secret: {"role": "worker", "worker": "alice"}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote = CoordinationClient(f"http://127.0.0.1:{server.server_port}", secret)
        assert (
            run_worker_once(
                remote,
                base,
                staging,
                worker="alice",
                driver=driver(root),
                work_root=root / "remote-worker",
            )["status"]
            == "submitted"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_overlay_does_not_mask_corrupt_metadata(setup, monkeypatch):
    _, base, staging, _, _ = setup

    def broken(*args):
        raise RegistryError("malformed immutable interface")

    monkeypatch.setattr(staging, "interface", broken)
    with pytest.raises(RegistryError, match="malformed"):
        OverlayRepository(staging, base).interface("test.call", "1")


def test_invalid_driver_and_idle(setup):
    root, base, staging, queue, _ = setup
    with pytest.raises(WorkerError):
        run_worker_once(
            queue, base, staging, worker="alice", driver=[], work_root=root / "bad"
        )
    queue.claim("other")
    assert run(setup)["status"] == "idle"


def test_open_constructor_selects_exact_root_with_same_interface_dependency(
    setup, wheelhouse
):
    from test_assemblies import provider

    root, base, staging, queue, _ = setup
    publish(
        root,
        base,
        "identity",
        [provider(package="identity")],
        "def run(value): return value\n",
    )
    publish(
        root,
        staging,
        "composed",
        [
            provider(
                package="composed",
                kind="functor",
                requires={"input": CALL},
                effects=["dependency-effects"],
            )
        ],
        "def run(input):\n    return {'run': lambda value: input.run(value) + 1}\n",
    )
    lease = queue.claim("alice")
    pinned = staging.lock("composed", "tests.run", version="1.0.0")
    index = json.loads((root / "composed/dist/index.json").read_text())
    queue.submit(
        lease["id"],
        lease["worker"],
        lease["fence"],
        {
            "candidate": {
                "family": "composed",
                "member": pinned["member"]["id"],
                "version": "1.0.0",
                "sha256": pinned["member"]["sha256"],
            },
            "family": index["family"],
        },
    )
    assert evaluate(setup, wheelhouse)["status"] == "accepted"
    assert len(base.candidates("test.call", "1")) == 2


def test_staged_dependency_cannot_be_admitted_with_root(setup, wheelhouse):
    from test_assemblies import provider

    root, base, staging, queue, _ = setup
    publish(
        root,
        staging,
        "unaccepted",
        [provider(package="unaccepted")],
        "def run(value): return value\n",
    )
    publish(
        root,
        staging,
        "composed",
        [
            provider(
                package="composed",
                kind="functor",
                requires={"input": CALL},
                effects=["dependency-effects"],
            )
        ],
        "def run(input): return {'run':lambda value:input.run(value)+1}\n",
    )
    pinned = staging.lock("composed", "tests.run", version="1.0.0")
    index = json.loads((root / "composed/dist/index.json").read_text())
    lease = queue.claim("alice")
    queue.submit(
        lease["id"],
        lease["worker"],
        lease["fence"],
        {
            "candidate": {
                "family": "composed",
                "member": pinned["member"]["id"],
                "version": "1.0.0",
                "sha256": pinned["member"]["sha256"],
            },
            "family": index["family"],
        },
    )
    with pytest.raises(WorkerError, match="dependency synthesis"):
        evaluate(setup, wheelhouse)
    assert not base.candidates("test.call", "1")
    assert queue.get("increment")["state"] == "submitted"


def test_receipt_signature_rechecked_before_publication(setup, wheelhouse, monkeypatch):
    import module_families.workers as workers

    run(setup)
    accept = workers.accept_contribution

    def unavailable(*args):
        raise OSError("repository unavailable")

    monkeypatch.setattr(workers, "accept_contribution", unavailable)
    with pytest.raises(OSError):
        evaluate(setup, wheelhouse)
    receipt_path = next((setup[0] / "evaluations").glob("*/receipt.json"))
    receipt = json.loads(receipt_path.read_text())
    receipt["attestation"]["signature"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(workers, "accept_contribution", accept)
    with pytest.raises(ValueError, match="attestation|signature"):
        evaluate(setup, wheelhouse)
    assert not setup[1].candidates("test.call", "1")
    assert setup[3].get("increment")["state"] == "submitted"


def test_driver_environment_excludes_evaluator_credentials(setup, monkeypatch):
    monkeypatch.setenv("PRIVATE_EVALUATOR_KEY", "do-not-send-to-driver")
    monkeypatch.setenv("MF_EVALUATOR_SECRET", "do-not-send-either")
    monkeypatch.setenv("MF_QUEUE_TOKEN", "administrator-token")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-model-key")
    monkeypatch.setenv("CODEX_HOME", "/tmp/operator-codex")
    code = "import json,os,pathlib;pathlib.Path('observed.json').write_text(json.dumps(dict(os.environ)))"
    result = run(setup, [sys.executable, "-c", code])
    environment = json.loads((Path(result["workspace"]) / "observed.json").read_text())
    assert (
        not {"PRIVATE_EVALUATOR_KEY", "MF_EVALUATOR_SECRET", "MF_QUEUE_TOKEN"}
        & environment.keys()
    )
    assert environment["OPENAI_API_KEY"] == "operator-model-key"
    assert environment["CODEX_HOME"] == "/tmp/operator-codex"


def test_driver_environment_mapping_validated_before_claim(setup):
    with pytest.raises(WorkerError, match="driver_env"):
        run(setup, driver_env={"invalid": 1})
    assert setup[3].get("increment")["state"] == "pending"

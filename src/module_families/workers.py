"""Agent-driver execution and evaluator-controlled contribution admission.

Driver commands are operator configuration, never executable instructions taken
from queued task payloads. Workers submit candidates; evaluators run the tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from .acceptance import accept_contribution
from .assemblies import lock_assembly
from .compiler import build_family
from .contributions import evaluate_contribution
from .environments import lock_environment, sync_environment
from .module_build import build_module
from .registry import RegistryError, canonical_bytes
from .synthesis import synthesize
from .typed_program import build_program


class WorkerError(ValueError):
    pass


class OverlayRepository:
    """Read-only selected candidate layer over the accepted repository.

    Layers may be local or HTTP repositories. No full repository/source snapshot
    is copied into a worker or evaluator workspace.
    """

    def __init__(self, *repositories):
        self.repositories = repositories

    def _merged(self, method, *args, **kwargs):
        rows = {}
        for repository in self.repositories:
            values = getattr(repository, method)(*args, **kwargs)
            for card in values:
                identity = (card["family"], card["id"], card["version"])
                if identity in rows and rows[identity] != card:
                    raise WorkerError(
                        "conflicting immutable candidate in repository layers"
                    )
                rows[identity] = card
        return list(rows.values())

    def versions(self, family, member):
        return self._merged("versions", family, member)

    def candidates(self, id, version, *, limit=100, offset=0, **kwargs):
        # Deterministic layer traversal; each query only visits the requested
        # interface domain. Duplicate identities retain one immutable record.
        rows = {}
        for repository in self.repositories:
            position = 0
            while position < offset + limit:
                page = repository.candidates(
                    id,
                    version,
                    limit=min(100, offset + limit - position),
                    offset=position,
                    **kwargs,
                )
                for card in page:
                    identity = (card["family"], card["id"], card["version"])
                    if identity in rows and rows[identity] != card:
                        raise WorkerError(
                            "conflicting immutable candidate in repository layers"
                        )
                    rows[identity] = card
                position += len(page)
                if not page:
                    break
        from packaging.version import Version

        ordered = sorted(
            rows.values(), key=lambda c: (c["family"], c["id"], c["version"])
        )
        ordered.sort(key=lambda card: Version(card["version"]), reverse=True)
        return ordered[offset : offset + limit]

    def interface(self, id, version):
        found = None
        for repository in self.repositories:
            try:
                value = repository.interface(id, version)
            except RegistryError as error:
                message = str(error)
                if (
                    message != f"Unknown interface: {id}@{version}"
                    and message
                    != f"Repository HTTP 400: Unknown interface: {id}@{version}"
                ):
                    raise
                continue
            if found is not None and value != found:
                raise WorkerError(
                    "conflicting immutable interface in repository layers"
                )
            found = value
        if found is None:
            raise RegistryError("interface is absent from repository layers")
        return found

    def lock(self, family, member, *, version=None, allowed_effects=None):
        matching = self.versions(family, member)
        if version is not None:
            matching = [card for card in matching if card["version"] == version]
        if not matching:
            raise RegistryError("member absent from repository layers")
        if version is None:
            from packaging.version import Version

            version = max(matching, key=lambda card: Version(card["version"]))[
                "version"
            ]
        for repository in self.repositories:
            if any(
                card["version"] == version
                for card in repository.versions(family, member)
            ):
                return repository.lock(
                    family, member, version=version, allowed_effects=allowed_effects
                )
        raise RegistryError("member absent from repository layers")

    def _verify_lock(self, lock):
        expected = self.lock(
            lock["family"],
            lock["member"]["id"],
            version=lock["version"],
            allowed_effects=lock.get("allowed_effects"),
        )
        if canonical_bytes(expected) != canonical_bytes(lock):
            raise RegistryError("lock differs from immutable repository member")
        for repository in self.repositories:
            if any(
                card["version"] == lock["version"]
                for card in repository.versions(lock["family"], lock["member"]["id"])
            ):
                return repository._verify_lock(lock)
        raise RegistryError("member absent from repository layers")

    def _blob(self, digest):
        for repository in self.repositories:
            try:
                path = repository._blob(digest)
            except RegistryError as error:
                if str(error).startswith("Repository HTTP 404:"):
                    continue
                raise
            if not path.is_file():
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RegistryError("repository layer contains corrupt artifact")
            return path
        raise RegistryError("verified artifact unavailable in repository layers")


class CandidateRepository(OverlayRepository):
    """One staged root, with independently selected dependencies from accepted code."""

    def __init__(self, staging, base, root):
        super().__init__(staging, base)
        self.base = base
        self.root = root

    def candidates(self, id, version, *, limit=100, offset=0, **kwargs):
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        rows = []
        while len(rows) < offset + limit:
            requested = min(1000, offset + limit - len(rows))
            page = self.base.candidates(
                id, version, limit=requested, offset=len(rows), **kwargs
            )
            rows.extend(page)
            if len(page) < requested:
                break
        root = self.root
        if (
            root.get("provides", {}).get("id") == id
            and root.get("provides", {}).get("version") == version
            and kwargs.get("family", root["family"]) == root["family"]
            and SpecifierSet(kwargs.get("version_spec") or "").contains(
                root["version"], prereleases=True
            )
        ):
            rows.append(root)
        unique = {}
        for card in rows:
            key = (card["family"], card["id"], card["version"])
            if key in unique and canonical_bytes(unique[key]) != canonical_bytes(card):
                raise WorkerError("conflicting immutable candidate")
            unique[key] = card
        ordered = sorted(
            unique.values(),
            key=lambda card: (card["family"], card["id"], card["version"]),
        )
        ordered.sort(key=lambda card: Version(card["version"]), reverse=True)
        return ordered[offset : offset + limit]


def _task(payload):
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "module-families-contribution-task-1"
    ):
        raise WorkerError("queue payload must be a prepared contribution task")
    digest = hashlib.sha256(
        canonical_bytes({k: v for k, v in payload.items() if k != "sha256"})
    ).hexdigest()
    if digest != payload.get("sha256"):
        raise WorkerError("queued task hash mismatch")
    return payload


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def driver_environment(*, exclude=()):
    """Explicit operator/model-auth environment; evaluator credentials stay private."""
    allowed = {
        "HOME",
        "PATH",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SystemRoot",
        "CODEX_HOME",
        "OPENAI_API_KEY",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed and key not in exclude
    }


def run_worker_once(
    queue,
    base,
    staging,
    *,
    worker,
    driver,
    work_root,
    lease_seconds=60,
    timeout=300,
    driver_env=None,
):
    """Execute an operator-trusted driver; task payloads never choose commands.

    Drivers and generated Python need an operator-provided sandbox when they
    are untrusted. A virtual environment is dependency isolation, not a sandbox.
    """
    if (
        not isinstance(driver, list)
        or not driver
        or any(not isinstance(arg, str) or not arg for arg in driver)
    ):
        raise WorkerError("driver must be a nonempty argument vector")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
        raise WorkerError("timeout must be positive and at most one day")
    driver_env = driver_environment() if driver_env is None else driver_env
    if not isinstance(driver_env, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or "=" in key
        or "\0" in key
        or "\0" in value
        for key, value in driver_env.items()
    ):
        raise WorkerError("driver_env must map environment names to string values")
    driver_env = dict(driver_env)
    lease = queue.claim(worker=worker, lease_seconds=lease_seconds)
    if lease is None:
        return {"status": "idle"}
    task_id = lease["id"]
    work = Path(work_root).resolve()
    work.mkdir(parents=True, exist_ok=True)
    root = Path(
        tempfile.mkdtemp(
            prefix=hashlib.sha256(task_id.encode()).hexdigest()[:16] + "-", dir=work
        )
    )
    stopped, lost = threading.Event(), []

    def heartbeat():
        while not stopped.wait(max(0.01, lease_seconds / 3)):
            try:
                queue.renew(
                    task_id=task_id,
                    worker=worker,
                    fence=lease["fence"],
                    lease_seconds=lease_seconds,
                )
            except Exception as error:
                lost.append(str(error))
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        payload = _task(lease["payload"])
        (root / "task.json").write_bytes(canonical_bytes(payload))
        (root / "feedback.json").write_bytes(
            canonical_bytes(
                {
                    "attempt": lease["attempts"],
                    "previous_error": lease.get("previous_error"),
                    "previous_submission": lease.get("previous_submission"),
                }
            )
        )
        from .worker_context import contribution_context

        (root / "context.json").write_bytes(
            canonical_bytes(contribution_context(payload, base))
        )
        replacements = {
            "{task}": str(root / "task.json"),
            "{proposal}": str(root / "proposal.json"),
            "{workspace}": str(root),
            "{feedback}": str(root / "feedback.json"),
            "{context}": str(root / "context.json"),
        }
        command = []
        for arg in driver:
            for marker, value in replacements.items():
                arg = arg.replace(marker, value)
            command.append(arg)
        with (root / "driver.log").open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=driver_env,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise WorkerError("agent driver exceeded timeout") from None
        if returncode:
            raise WorkerError("agent driver failed; inspect local driver.log")
        proposal_path = root / "proposal.json"
        if proposal_path.stat().st_size > 100_000:
            raise WorkerError("proposal exceeds size limit")
        proposal = json.loads(proposal_path.read_text())
        if (
            not isinstance(proposal, dict)
            or set(proposal) != {"kind", "manifest"}
            or not isinstance(proposal["manifest"], str)
        ):
            raise WorkerError("proposal must declare kind and manifest")
        manifest = (root / proposal["manifest"]).resolve()
        if not manifest.is_relative_to(root) or manifest.suffix != ".toml":
            raise WorkerError(
                "proposal manifest must be a TOML file inside worker workspace"
            )
        repository = OverlayRepository(staging, base)
        if proposal["kind"] == "family":
            build_family(manifest, root / "build")
        elif proposal["kind"] == "graph":
            build_module(manifest, repository, root / "build")
        elif proposal["kind"] == "program":
            build_program(manifest, repository, root / "build")
        else:
            raise WorkerError("unknown proposal kind")
        index = json.loads((root / "build/index.json").read_text())
        if len(index["members"]) != 1:
            raise WorkerError(
                "a contribution submission must have exactly one root member"
            )
        if lost:
            raise WorkerError("lease lost while agent worked")
        staging.publish(root / "build/index.json")
        card = index["members"][0]
        submission = {
            "candidate": {
                "family": index["family"]["name"],
                "member": card["id"],
                "version": card["version"],
                "sha256": card["sha256"],
            },
            "family": index["family"],
        }
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        submission = {"error": f"{type(error).__name__}: {error}"}
    finally:
        stopped.set()
        thread.join(timeout=5)
    submitted = queue.submit(
        task_id=task_id, worker=worker, fence=lease["fence"], submission=submission
    )
    return {
        "status": "submitted",
        "task_id": task_id,
        "submission_sha256": submitted["submission_sha256"],
        "workspace": str(root),
    }


def evaluate_submission(
    queue,
    task_id,
    base,
    staging,
    *,
    work_root,
    evidence_store,
    evaluator_id,
    secret,
    find_links=(),
    no_index=False,
    timeout=30,
):
    """Evaluate, attest, publish exact bytes, then acknowledge the queue.

    A durable receipt makes retries after publication safe. Publication and queue
    acceptance are separate transactions: interrupted commits remain submitted,
    never falsely rejected. Use one evaluator workspace per coordination service.
    """
    import fcntl

    from .contributions import verify_evidence
    from .evidence import ingest_evidence, verify_attestation

    initial = queue.get(task_id=task_id)
    if initial["state"] == "accepted":
        return {"status": "accepted", "task_id": task_id, **initial["result"]}
    if initial["state"] != "submitted":
        raise WorkerError("task has no submitted candidate")
    submission_hash = initial["submission_sha256"]
    root = (
        Path(work_root).resolve()
        / hashlib.sha256((task_id + submission_hash).encode()).hexdigest()
    )
    root.mkdir(parents=True, exist_ok=True)
    with (root / "evaluator.lock").open("a") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        task = queue.get(task_id=task_id)
        if task["state"] == "accepted" and task["submission_sha256"] == submission_hash:
            return {"status": "accepted", "task_id": task_id, **task["result"]}
        if task["state"] != "submitted" or task["submission_sha256"] != submission_hash:
            raise WorkerError("submission changed before evaluation")
        payload = _task(task["payload"])
        task_path = root / "task.json"
        task_path.write_bytes(canonical_bytes(payload))
        receipt_path = root / "receipt.json"
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
            if receipt["submission_sha256"] != submission_hash:
                raise WorkerError("evaluator receipt belongs to different submission")
        else:
            submission = task["submission"]
            if "error" in submission:
                rejected = queue.reject(
                    task_id=task_id,
                    submission_sha256=submission_hash,
                    reason=submission["error"],
                    retry=True,
                )
                return {
                    "status": "rejected",
                    "task_id": task_id,
                    "error": submission["error"],
                    "state": rejected["state"],
                }
            candidate = submission["candidate"]
            pinned = staging.lock(
                candidate["family"], candidate["member"], version=candidate["version"]
            )
            if pinned["member"]["sha256"] != candidate["sha256"]:
                raise WorkerError(
                    "staged candidate bytes differ from fenced submission"
                )
            repository = CandidateRepository(staging, base, pinned["member"])
            contract = payload["document"]["task"]
            resolution = synthesize(
                {
                    "schema_version": 1,
                    "goal": {
                        "name": contract["id"],
                        "requires": contract["requires"],
                        "capabilities": contract.get("capabilities", []),
                        "root": {
                            "family": candidate["family"],
                            "id": candidate["member"],
                            "version": candidate["version"],
                            "sha256": candidate["sha256"],
                        },
                    },
                    "policy": payload["document"]["policy"],
                },
                repository,
            )
            if resolution["status"] != "unique":
                raise WorkerError(
                    "candidate dependency synthesis is " + resolution["status"]
                )
            assembly = lock_assembly(resolution, repository)
            # Every evaluation attempt has an isolated build environment. A crash
            # before a receipt simply repeats evaluation, without reusing partial files.
            attempt = Path(tempfile.mkdtemp(prefix="evaluation-", dir=root))
            environment = lock_environment(
                assembly,
                repository,
                attempt / "environment",
                find_links=list(find_links),
                no_index=no_index,
            )
            sync_environment(environment["lock"], attempt / "venv")
            evidence = evaluate_contribution(
                task_path, environment["lock"], attempt / "venv", timeout=timeout
            )
            evidence_path = attempt / "evidence.json"
            evidence_path.write_bytes(canonical_bytes(evidence))
            if evidence["status"] != "passed":
                rejected = queue.reject(
                    task_id=task_id,
                    submission_sha256=submission_hash,
                    reason="candidate failed acceptance cases: "
                    + json.dumps(evidence.get("cases", []), sort_keys=True)[:8000],
                    retry=True,
                )
                return {
                    "status": "rejected",
                    "task_id": task_id,
                    "state": rejected["state"],
                    "evidence": str(evidence_path),
                }
            attestation = ingest_evidence(
                task_path,
                environment["lock"],
                evidence_path,
                evidence_store,
                evaluator_id,
                secret,
            )
            publication = {
                "schema_version": 1,
                "family": submission["family"],
                "publisher": pinned["member"]["publisher"],
                "members": [pinned["member"]],
                "artifacts": pinned["artifacts"],
            }
            out = attempt / "publication"
            out.mkdir()
            for artifact in publication["artifacts"]:
                (out / artifact["filename"]).write_bytes(
                    repository._blob(artifact["sha256"]).read_bytes()
                )
            (out / "index.json").write_bytes(canonical_bytes(publication))
            receipt = {
                "submission_sha256": submission_hash,
                "attestation": attestation,
                "environment": str(environment["lock"]),
                "evidence": str(evidence_path),
                "index": str(out / "index.json"),
            }
            _atomic_json(receipt_path, receipt)
        attested = verify_attestation(receipt["attestation"], {evaluator_id: secret})
        verified = verify_evidence(
            task_path, receipt["environment"], receipt["evidence"]
        )
        for key in ("task_sha256", "environment_sha256", "program_sha256"):
            if attested[key] != verified[key]:
                raise WorkerError(
                    "evaluator receipt attests a different evaluated context"
                )
        if attested["evidence_sha256"] != verified["sha256"]:
            raise WorkerError("evaluator receipt attests different evidence")
        current = queue.get(task_id=task_id)
        if (
            current["state"] != "submitted"
            or current["submission_sha256"] != submission_hash
        ):
            raise WorkerError("submission changed before publication")
        accepted = accept_contribution(
            task_path,
            receipt["environment"],
            receipt["evidence"],
            receipt["index"],
            base,
        )
        result = {
            "acceptance": accepted,
            "attestation": receipt["attestation"],
            "environment": receipt["environment"],
        }
        queue.accept(task_id=task_id, submission_sha256=submission_hash, result=result)
        return {"status": "accepted", "task_id": task_id, **result}

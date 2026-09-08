"""CLI wiring for durable task workers and trusted evaluator operations."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

COMMANDS = {
    "queue-serve",
    "enqueue-task",
    "tasks",
    "agent-worker",
    "evaluate-submissions",
    "attest-evidence",
    "campaign",
}


def trust_keys(environment="MF_EVIDENCE_KEYS"):
    from .signing import PublicEvaluatorKey

    value = json.loads(os.environ.get(environment, "{}"))
    if not isinstance(value, dict):
        raise ValueError("evidence keys must map evaluator IDs to keys")
    result = {}
    for name, key in value.items():
        if not isinstance(name, str):
            raise ValueError("evaluator IDs must be strings")
        if isinstance(key, str):
            result[name] = key.encode()
        elif isinstance(key, dict) and set(key) == {"ed25519"}:
            try:
                result[name] = PublicEvaluatorKey(bytes.fromhex(key["ed25519"]))
            except (ValueError, TypeError) as error:
                raise ValueError("invalid evaluator public key") from error
        else:
            raise ValueError("key must be a secret string or {ed25519: public hex}")
    return result


def _repository_identity(location):
    if location.startswith(("http://", "https://")):
        parsed = urlsplit(location)
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/"),
                parsed.query,
                "",
            )
        )
    return str(Path(location).expanduser().resolve())


def _evaluator_secret(environment):
    secret = os.environ.get(environment)
    if not secret:
        raise ValueError("configured evaluator secret is missing")
    if secret.startswith("ed25519:"):
        from .signing import PrivateEvaluatorKey

        try:
            return PrivateEvaluatorKey(bytes.fromhex(secret[len("ed25519:") :]))
        except ValueError as error:
            raise ValueError("invalid evaluator private key") from error
    if len(secret.encode()) < 32:
        raise ValueError("configured evaluator secret must contain at least 32 bytes")
    return secret.encode()


def register(sub):
    serve = sub.add_parser(
        "queue-serve", help="Serve a durable scoped agent task queue"
    )
    serve.add_argument("--database", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8043)
    serve.add_argument("--tokens-env", default="MF_WORKERS")
    enqueue = sub.add_parser(
        "enqueue-task", help="Queue a prepared executable contribution contract"
    )
    enqueue.add_argument("task")
    enqueue.add_argument("--id")
    enqueue.add_argument("--prerequisite", action="append", default=[])
    enqueue.add_argument("--max-attempts", type=int, default=3)
    listing = sub.add_parser("tasks", help="Inspect bounded task queue state")
    listing.add_argument("--state")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--offset", type=int, default=0)
    worker = sub.add_parser(
        "agent-worker",
        help="Run a configured agent driver on leased contribution tasks",
    )
    worker.add_argument("--worker", required=True)
    worker.add_argument(
        "--driver",
        required=True,
        help="Operator TOML: [driver] command=[argv with {task}, {proposal}, {workspace}]",
    )
    worker.add_argument("--lease-seconds", type=float, default=60)
    worker.add_argument("--timeout", type=float, default=300)
    worker.add_argument("--max-tasks", type=int, default=1)
    evaluate = sub.add_parser(
        "evaluate-submissions",
        help="Evaluate submitted candidates, attest results, and accept passing work",
    )
    evaluate.add_argument("--limit", type=int, default=10)
    evaluate.add_argument("--timeout", type=float, default=30)
    evaluate.add_argument("--find-links", action="append", default=[])
    evaluate.add_argument("--no-index", action="store_true")
    campaign = sub.add_parser(
        "campaign", help="Run a bounded explicit contribution campaign"
    )
    campaign.add_argument("manifest")
    campaign.add_argument("--find-links", action="append", default=[])
    campaign.add_argument("--no-index", action="store_true")
    attest = sub.add_parser(
        "attest-evidence",
        help="Attest a caller-trusted evaluation report and its exact context",
    )
    attest.add_argument("task")
    attest.add_argument("environment")
    attest.add_argument("evidence")
    for command in (enqueue, listing, worker, evaluate, campaign):
        command.add_argument(
            "--queue", required=True, help="Local SQLite path or HTTP queue URL"
        )
        command.add_argument("--queue-token-env", default="MF_QUEUE_TOKEN")
    for command in (worker, evaluate, campaign):
        command.add_argument("--registry", required=True, help="Accepted repository")
        command.add_argument(
            "--staging",
            required=True,
            help="Candidate repository, distinct from accepted repository",
        )
        command.add_argument("--work-dir", required=True)
        command.add_argument("--token-env", default="MF_TOKEN")
        command.add_argument("--staging-token-env", default="MF_STAGING_TOKEN")
    for command in (evaluate, attest, campaign):
        command.add_argument("--evidence-store", required=True)
        command.add_argument("--evaluator", required=True)
        command.add_argument("--key-env", default="MF_EVALUATOR_SECRET")


def run(args, emit):
    from .coordination import CoordinationClient, CoordinationQueue, coordination_server

    if args.command == "queue-serve":
        server = coordination_server(
            CoordinationQueue(args.database),
            tokens=json.loads(os.environ.get(args.tokens_env, "{}")),
            host=args.host,
            port=args.port,
        )
        emit(
            {
                "listening": f"http://{server.server_address[0]}:{server.server_address[1]}"
            }
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "attest-evidence":
        from .evidence import EvidenceStore, ingest_evidence

        secret = _evaluator_secret(args.key_env)
        emit(
            ingest_evidence(
                args.task,
                args.environment,
                args.evidence,
                EvidenceStore(args.evidence_store),
                args.evaluator,
                secret,
            )
        )
        return 0
    queue = (
        CoordinationClient(args.queue, os.environ.get(args.queue_token_env, ""))
        if args.queue.startswith(("http://", "https://"))
        else CoordinationQueue(args.queue)
    )
    if args.command == "enqueue-task":
        from .contributions import _task

        payload = _task(args.task)
        emit(
            queue.enqueue(
                payload=payload,
                task_id=args.id,
                prerequisites=args.prerequisite,
                max_attempts=args.max_attempts,
            )
        )
        return 0
    if args.command == "tasks":
        emit(queue.list_tasks(state=args.state, limit=args.limit, offset=args.offset))
        return 0
    from .repository import open_repository
    from .workers import driver_environment, evaluate_submission, run_worker_once

    if _repository_identity(args.registry) == _repository_identity(args.staging):
        raise ValueError("accepted and staging repositories must be distinct")
    base = open_repository(
        args.registry,
        token=os.environ.get(args.token_env),
        cache=Path(args.work_dir) / "base-cache",
    )
    staging = open_repository(
        args.staging,
        token=os.environ.get(args.staging_token_env),
        cache=Path(args.work_dir) / "staging-cache",
    )
    driver_env = driver_environment(
        exclude={
            getattr(args, "key_env", "MF_EVALUATOR_SECRET"),
            args.token_env,
            args.staging_token_env,
            args.queue_token_env,
        }
    )
    if args.command == "campaign":
        from .campaigns import run_campaign
        from .evidence import EvidenceStore

        result = run_campaign(
            args.manifest,
            queue,
            base,
            staging,
            work_root=args.work_dir,
            evidence_store=EvidenceStore(args.evidence_store),
            evaluator_id=args.evaluator,
            secret=_evaluator_secret(args.key_env),
            trust_keys=trust_keys(),
            find_links=args.find_links,
            no_index=args.no_index,
            driver_env=driver_env,
        )
        emit(result)
        return 0 if result["status"] == "complete" else 2
    if args.command == "agent-worker":
        document = tomllib.loads(Path(args.driver).read_text())
        if set(document) != {"driver"} or set(document["driver"]) != {"command"}:
            raise ValueError("driver TOML must contain only [driver] command")
        if args.max_tasks < 1:
            raise ValueError("max-tasks must be positive")
        for _ in range(args.max_tasks):
            result = run_worker_once(
                queue,
                base,
                staging,
                worker=args.worker,
                driver=document["driver"]["command"],
                work_root=args.work_dir,
                lease_seconds=args.lease_seconds,
                timeout=args.timeout,
                driver_env=driver_env,
            )
            emit(result)
            if result["status"] == "idle":
                break
        return 0
    from .evidence import EvidenceStore

    secret = _evaluator_secret(args.key_env)
    records = queue.list_tasks(state="submitted", limit=args.limit, offset=0)
    for record in records:
        emit(
            evaluate_submission(
                queue,
                record["id"],
                base,
                staging,
                work_root=args.work_dir,
                evidence_store=EvidenceStore(args.evidence_store),
                evaluator_id=args.evaluator,
                secret=secret,
                find_links=args.find_links,
                no_index=args.no_index,
                timeout=args.timeout,
            )
        )
    return 0

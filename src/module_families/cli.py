"""Small, JSON-first discovery, publishing, and execution interface."""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import sys
import tomllib
from pathlib import Path

from . import __version__
from .catalog import Catalog


def _json_default(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (Path, enum.Enum)):
        return str(value) if isinstance(value, Path) else value.value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    raise TypeError(
        f"{type(value).__name__} is not JSON serializable; use the Python API"
    )


def _emit(value, destination: str | None = None):
    text = (
        json.dumps(
            value, indent=2, ensure_ascii=False, allow_nan=False, default=_json_default
        )
        + "\n"
    )
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(json.dumps({"written": str(path)}))
    else:
        print(text, end="")


def _effects(args):
    return [] if args.pure else args.allow_effect


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="mf",
        description="Build reusable Python module families from TOML. Command responses are JSON.",
    )
    root.add_argument(
        "--version", action="version", version=f"module-families {__version__}"
    )
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init", help="Draft a generic family from any supported Python source project"
    )
    init.add_argument("project")
    init.add_argument("--family", required=True)
    init.add_argument("--publisher", required=True)
    init.add_argument("--package", required=True)
    init.add_argument("--family-version")
    init.add_argument("--out", default="family.toml")
    scaffold = sub.add_parser(
        "scaffold", help="Write one TOML contribution for an existing source definition"
    )
    scaffold.add_argument("manifest")
    scaffold.add_argument("--member", required=True)
    scaffold.add_argument("--out")
    migrate = sub.add_parser(
        "migrate-mari",
        help="Generate family metadata from a copied Mari source tree without importing it",
    )
    migrate.add_argument("project", nargs="?", default="projects/mari-kit")
    migrate.add_argument("--out", default="families/mari/family.toml")
    catalog = sub.add_parser("catalog", help="Search an unpublished family manifest")
    catalog.add_argument("manifest")
    catalog.add_argument("query", nargs="?", default="")
    catalog.add_argument(
        "--kind",
        choices=[
            "algorithm",
            "operation",
            "type",
            "adapter",
            "contract",
            "module-factory",
            "functor",
            "module",
        ],
    )
    catalog.add_argument("--contract")
    catalog.add_argument("--limit", type=int, default=10)
    catalog.add_argument("--offset", type=int, default=0)
    inspect = sub.add_parser("inspect", help="Read full member context from a manifest")
    inspect.add_argument("manifest")
    inspect.add_argument("member")
    validate = sub.add_parser(
        "validate", help="Validate a family manifest without executing its source"
    )
    validate.add_argument("manifest")
    build = sub.add_parser(
        "build", help="Build selected members and their cells as ordinary wheels"
    )
    build.add_argument("manifest")
    build.add_argument(
        "--member",
        action="append",
        help="Repeat for multiple members; omission builds the whole family",
    )
    build.add_argument("--out", help="Defaults to dist/<family>")
    build.add_argument("--cache")
    build_plan = sub.add_parser(
        "plan-build",
        help="Explain selected source and dependency closure without building or importing",
    )
    build_plan.add_argument("manifest")
    build_plan.add_argument("--member", action="append")
    build_plan.add_argument("--out")
    build_plan.add_argument("--cache")
    plan = sub.add_parser(
        "plan", help="Check a finite module expression and its supplied candidate cards"
    )
    plan.add_argument(
        "request", help="TOML or JSON containing expression and candidates"
    )
    plan.add_argument("--out")
    publish = sub.add_parser(
        "publish",
        help="Publish a build index and wheels into a local immutable registry",
    )
    publish.add_argument("index")
    search = sub.add_parser(
        "search", help="Search published metadata without importing code"
    )
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--family")
    search.add_argument("--contract")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--offset", type=int, default=0)
    describe = sub.add_parser("describe", help="Read a published member card")
    describe.add_argument("family")
    describe.add_argument("member")
    describe.add_argument("--member-version", dest="member_version")
    lock = sub.add_parser(
        "lock", help="Lock a selected member's internal artifact closure"
    )
    lock.add_argument("family")
    lock.add_argument("member")
    lock.add_argument("--member-version", dest="member_version")
    lock.add_argument("--out")
    materialize = sub.add_parser(
        "materialize",
        help="Verify and install locked generated files without executing them",
    )
    materialize.add_argument("lock")
    materialize.add_argument("--target", required=True)
    run = sub.add_parser(
        "run", help="Execute the locked Python export with explicit JSON arguments"
    )
    run.add_argument("lock")
    run.add_argument("--target", default=".mf/site")
    run.add_argument("--args", default="[]", help="JSON positional argument list")
    run.add_argument("--kwargs", default="{}", help="JSON keyword argument object")
    simple = sub.add_parser(
        "export-simple", help="Export a static Python Simple Repository index"
    )
    simple.add_argument("--out", default=".mf/simple")
    families = sub.add_parser("families", help="List families in the repository")
    families.add_argument("--limit", type=int, default=100)
    families.add_argument("--offset", type=int, default=0)
    versions = sub.add_parser(
        "versions", help="List independently published versions of a member"
    )
    versions.add_argument("family")
    versions.add_argument("member")
    interfaces = sub.add_parser(
        "interfaces", help="List published reusable interface libraries"
    )
    interfaces.add_argument("--limit", type=int, default=100)
    interfaces.add_argument("--offset", type=int, default=0)
    interface = sub.add_parser("interface", help="Read an exact published interface")
    interface.add_argument("id")
    interface.add_argument("version")
    resolve = sub.add_parser(
        "resolve", help="Resolve a TOML module expression against repository providers"
    )
    resolve.add_argument("assembly")
    resolve.add_argument("--out")
    resolve.add_argument("--max-solutions", type=int, default=16)
    resolve.add_argument("--max-states", type=int, default=10000)
    module_resolve = sub.add_parser("resolve-module", help="Check a TOML open module graph and its repository choices")
    module_build = sub.add_parser("build-module", help="Compile a TOML open module graph to a publishable Python wheel")
    for command in (module_resolve, module_build):
        command.add_argument("module")
        command.add_argument("--out", required=command is module_build)
        command.add_argument("--max-candidates", type=int, default=100)
        command.add_argument("--max-states", type=int, default=10000)
        command.add_argument("--max-solutions", type=int, default=16)
    module_build.add_argument("--choice", type=int)
    program_check = sub.add_parser("check-program", help="Typecheck .mfl composition against interfaces without concrete providers")
    program_build = sub.add_parser("build-program", help="Compile a resource-checked .mfl program into a published Python constructor")
    for command in (program_check, program_build):
        command.add_argument("program", help="TOML manifest referencing .mfl source")
        command.add_argument("--out", required=command is program_build)
    synthesize = sub.add_parser(
        "synthesize",
        help="Construct programs recursively from an interface and capability goal",
    )
    synthesize.add_argument("goal")
    synthesize.add_argument("--out")
    synthesize.add_argument("--max-depth", type=int, default=5)
    synthesize.add_argument("--max-states", type=int, default=10000)
    synthesize.add_argument("--max-solutions", type=int, default=16)
    synthesize.add_argument("--evidence-store")
    synthesize.add_argument("--trust-keys-env", default="MF_EVIDENCE_KEYS")
    contribution_plan = sub.add_parser("plan-contributions", help="Turn missing providers into bounded contribution handoffs")
    contribution_plan.add_argument("goal")
    contribution_plan.add_argument("--out", required=True)
    contribution_plan.add_argument("--max-depth", type=int, default=5)
    contribution_plan.add_argument("--max-states", type=int, default=10000)
    contribution_plan.add_argument("--max-candidates", type=int, default=100)
    contribution_plan.add_argument("--max-solutions", type=int, default=16)
    contribution_prepare = sub.add_parser("prepare-contribution", help="Package a TOML acceptance contract with its published interface")
    contribution_prepare.add_argument("contract")
    contribution_prepare.add_argument("--out", required=True)
    contribution_evaluate = sub.add_parser("evaluate-contribution", help="Evaluate a locked candidate against an executable task")
    contribution_evaluate.add_argument("task")
    contribution_evaluate.add_argument("environment")
    contribution_evaluate.add_argument("--target", required=True)
    contribution_evaluate.add_argument("--timeout", type=float, default=30)
    contribution_evaluate.add_argument("--out", required=True)
    contribution_verify = sub.add_parser("verify-evidence", help="Verify contribution evidence and its exact artifact provenance")
    contribution_verify.add_argument("task")
    contribution_verify.add_argument("environment")
    contribution_verify.add_argument("evidence")
    contribution_accept = sub.add_parser("accept-contribution", help="Publish exactly the contribution covered by passing trusted evaluation evidence")
    contribution_accept.add_argument("task")
    contribution_accept.add_argument("environment")
    contribution_accept.add_argument("evidence")
    contribution_accept.add_argument("index")
    contribution_accept.add_argument("--out")
    assembly_lock = sub.add_parser(
        "lock-assembly",
        help="Pin a selected synthesized or resolved program and its interfaces",
    )
    assembly_lock.add_argument("resolution")
    assembly_lock.add_argument("--choice", type=int)
    assembly_lock.add_argument("--out", required=True)
    assembly_lock.add_argument("--trust-keys-env", default="MF_EVIDENCE_KEYS")
    execute = sub.add_parser(
        "execute",
        help="Verify and instantiate an assembly, then call an exported operation",
    )
    execute.add_argument("lock")
    execute.add_argument("--target", default=".mf/assemblies")
    execute.add_argument("--export", default="run")
    execute.add_argument("--args", default="[]")
    execute.add_argument("--kwargs", default="{}")
    lock_env = sub.add_parser(
        "lock-env", help="Resolve and hash a complete wheel environment for a program"
    )
    lock_env.add_argument("lock")
    lock_env.add_argument(
        "--out", required=True, help="Directory for the environment lock and wheels"
    )
    lock_env.add_argument("--find-links", action="append", default=[])
    lock_env.add_argument("--no-index", action="store_true")
    sync = sub.add_parser("sync", help="Install a locked program environment offline")
    sync.add_argument("lock")
    sync.add_argument("--target", required=True)
    exec_env = sub.add_parser(
        "exec", help="Run a program through its locked private environment"
    )
    exec_env.add_argument("lock")
    exec_env.add_argument("--target", required=True)
    exec_env.add_argument("--export", default="run")
    exec_env.add_argument("--args", default="[]")
    exec_env.add_argument("--kwargs", default="{}")
    adapt = sub.add_parser(
        "adapt",
        help="Apply a reviewed TOML source projection without rewriting algorithms",
    )
    adapt.add_argument("manifest")
    adapt.add_argument("--out", required=True)
    serve = sub.add_parser(
        "serve", help="Run the authenticated module repository service"
    )
    serve.add_argument("--registry", default=".mf/repository")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8042)
    serve.add_argument(
        "--tokens-env",
        default="MF_PUBLISHERS",
        help="Environment variable with token-to-publisher JSON rules",
    )
    for cmd in (
        publish,
        search,
        describe,
        lock,
        materialize,
        run,
        simple,
        families,
        versions,
        interfaces,
        interface,
        resolve,
        module_resolve,
        module_build,
        program_check,
        program_build,
        synthesize,
        contribution_plan,
        contribution_prepare,
        contribution_accept,
        assembly_lock,
        execute,
        lock_env,
    ):
        cmd.add_argument("--registry", default=".mf/repository")
        cmd.add_argument("--token-env", default="MF_TOKEN")
        cmd.add_argument("--cache", default=".mf/remote-cache")
    for cmd in (catalog, search, lock):
        group = cmd.add_mutually_exclusive_group()
        group.add_argument(
            "--pure",
            action="store_true",
            help="Require a declaration of no effects; this does not enforce purity",
        )
        group.add_argument(
            "--allow-effect",
            action="append",
            help="Allowed declared effect; repeat as needed",
        )
    from .automation_cli import register

    register(sub)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        from .automation_cli import COMMANDS, run, trust_keys

        if args.command in COMMANDS:
            return run(args, _emit)
        if args.command == "init":
            from .authoring import draft_family

            _emit(
                draft_family(
                    args.project,
                    family=args.family,
                    publisher=args.publisher,
                    package=args.package,
                    destination=args.out,
                    version=args.family_version,
                )
            )
        elif args.command == "migrate-mari":
            from .migration import generate_mari

            _emit(generate_mari(args.project, args.out))
        elif args.command == "scaffold":
            from .authoring import scaffold_member

            _emit(scaffold_member(args.manifest, args.member, args.out))
        elif args.command == "plan-build":
            from .compiler import plan_build

            result = plan_build(
                Path(args.manifest),
                member_ids=args.member,
                cache_dir=Path(args.cache) if args.cache else None,
            )
            _emit(result, args.out)
            if result["status"] == "blocked":
                return 2
        elif args.command == "plan":
            from .planning import plan

            path = Path(args.request)
            request = (
                tomllib.loads(path.read_text())
                if path.suffix == ".toml"
                else json.loads(path.read_text())
            )
            _emit(plan(**request), args.out)
        elif args.command == "catalog":
            _emit(
                Catalog.load(args.manifest).search(
                    args.query,
                    kind=args.kind,
                    contract=args.contract,
                    allowed_effects=_effects(args),
                    limit=args.limit,
                    offset=args.offset,
                )
            )
        elif args.command == "inspect":
            _emit(Catalog.load(args.manifest).inspect(args.member))
        elif args.command == "validate":
            document = Catalog.load(args.manifest).document
            _emit(
                {
                    "valid": True,
                    "family": document["name"],
                    "version": document["version"],
                    "members": len(document["members"]),
                }
            )
        elif args.command == "build":
            from .compiler import build_family

            catalog = Catalog.load(args.manifest)
            out = Path(args.out or f"dist/{catalog.document['name']}")
            stats = {}
            index = build_family(
                Path(args.manifest),
                out,
                member_ids=args.member,
                cache_dir=Path(args.cache) if args.cache else None,
                stats=stats,
            )
            _emit(
                {
                    "index": str(out / "index.json"),
                    "members": len(index["members"]),
                    "artifacts": len(index["artifacts"]),
                    "interfaces": len(index.get("interfaces", [])),
                    "cache": stats,
                }
            )
        elif args.command == "adapt":
            from .adaptation import adapt

            _emit(adapt(Path(args.manifest), Path(args.out)))
        elif args.command == "evaluate-contribution":
            from .contributions import evaluate_contribution

            result = evaluate_contribution(args.task, args.environment, args.target, timeout=args.timeout)
            _emit(result, args.out)
            if result["status"] != "passed":
                return 2
        elif args.command == "verify-evidence":
            from .contributions import verify_evidence

            _emit(verify_evidence(args.task, args.environment, args.evidence))
        elif args.command == "sync":
            from .environments import sync_environment

            _emit(sync_environment(args.lock, args.target))
        elif args.command == "exec":
            from .environments import run_environment

            _emit(
                run_environment(
                    args.lock,
                    args.target,
                    export=args.export,
                    args=json.loads(args.args),
                    kwargs=json.loads(args.kwargs),
                )
            )
        elif args.command == "serve":
            from .registry import Registry
            from .repository import make_server

            rules = json.loads(os.environ.get(args.tokens_env, "{}"))
            server = make_server(
                Registry(Path(args.registry)),
                host=args.host,
                port=args.port,
                tokens=rules,
            )
            _emit(
                {
                    "listening": f"http://{server.server_address[0]}:{server.server_address[1]}",
                    "publishers": len(rules),
                }
            )
            sys.stdout.flush()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        else:
            from .repository import open_repository

            registry = open_repository(
                args.registry,
                token=os.environ.get(args.token_env),
                cache=Path(args.cache),
            )
            if args.command == "prepare-contribution":
                from .contributions import prepare_contribution

                _emit(prepare_contribution(args.contract, registry, args.out))
            elif args.command == "plan-contributions":
                from .handoffs import plan_contributions

                _emit(plan_contributions(args.goal, registry, args.out,
                    max_depth=args.max_depth, max_states=args.max_states,
                    max_candidates=args.max_candidates, max_solutions=args.max_solutions))
            elif args.command == "accept-contribution":
                from .acceptance import accept_contribution

                _emit(accept_contribution(args.task, args.environment, args.evidence, args.index, registry), args.out)
            elif args.command == "publish":
                _emit(registry.publish(Path(args.index)))
            elif args.command == "families":
                _emit(registry.families(limit=args.limit, offset=args.offset))
            elif args.command == "versions":
                _emit(registry.versions(args.family, args.member))
            elif args.command == "interfaces":
                _emit(registry.interfaces(limit=args.limit, offset=args.offset))
            elif args.command == "interface":
                _emit(registry.interface(args.id, args.version))
            elif args.command in {"check-program", "build-program"}:
                from .typed_program import build_program, check_program

                if args.command == "build-program":
                    _emit(build_program(args.program, registry, args.out))
                else:
                    _emit(check_program(args.program, registry), args.out)
            elif args.command in {"resolve-module", "build-module"}:
                from .module_build import build_module, resolve_module

                bounds = {"max_candidates": args.max_candidates, "max_states": args.max_states, "max_solutions": args.max_solutions}
                if args.command == "build-module":
                    _emit(build_module(args.module, registry, args.out, choice=args.choice, **bounds))
                else:
                    result = resolve_module(args.module, registry, **bounds)
                    _emit(result, args.out)
                    if result["status"] == "unsatisfied":
                        return 2
            elif args.command == "resolve":
                from .assemblies import resolve_assembly

                _emit(
                    resolve_assembly(
                        args.assembly,
                        registry,
                        max_states=args.max_states,
                        max_solutions=args.max_solutions,
                    ),
                    args.out,
                )
            elif args.command == "synthesize":
                from .evidence import EvidenceStore
                from .synthesis import synthesize

                request = tomllib.loads(Path(args.goal).read_text())
                _emit(
                    synthesize(
                        request,
                        registry,
                        max_depth=args.max_depth,
                        max_states=args.max_states,
                        max_solutions=args.max_solutions,
                        evidence_store=EvidenceStore(args.evidence_store) if args.evidence_store else None,
                        trust_keys=trust_keys(args.trust_keys_env),
                    ),
                    args.out,
                )
            elif args.command == "lock-assembly":
                from .assemblies import lock_assembly

                _emit(
                    lock_assembly(
                        json.loads(Path(args.resolution).read_text()),
                        registry,
                        choice=args.choice,
                        trust_keys=trust_keys(args.trust_keys_env),
                    ),
                    args.out,
                )
            elif args.command == "execute":
                from .assemblies import instantiate

                instance = instantiate(
                    json.loads(Path(args.lock).read_text()), registry, args.target
                )
                result = instance.module[args.export](
                    *json.loads(args.args), **json.loads(args.kwargs)
                )
                if hasattr(result, "__next__"):
                    from itertools import islice

                    result = list(islice(result, 10001))
                    if len(result) > 10000:
                        raise ValueError(
                            "result exceeds 10,000 items; use the Python API"
                        )
                _emit({"assembly": instance.identity, "result": result})
            elif args.command == "lock-env":
                from .environments import lock_environment

                _emit(
                    lock_environment(
                        json.loads(Path(args.lock).read_text()),
                        registry,
                        args.out,
                        find_links=args.find_links,
                        no_index=args.no_index,
                    )
                )
            elif args.command == "search":
                _emit(
                    registry.search(
                        args.query,
                        family=args.family,
                        contract=args.contract,
                        allowed_effects=_effects(args),
                        limit=args.limit,
                        offset=args.offset,
                    )
                )
            elif args.command == "describe":
                _emit(
                    registry.inspect(
                        args.family, args.member, version=args.member_version
                    )
                )
            elif args.command == "lock":
                _emit(
                    registry.lock(
                        args.family,
                        args.member,
                        version=args.member_version,
                        allowed_effects=_effects(args),
                    ),
                    args.out,
                )
            elif args.command == "materialize":
                _emit(
                    registry.materialize(
                        json.loads(Path(args.lock).read_text()), Path(args.target)
                    )
                )
            elif args.command == "export-simple":
                if not hasattr(registry, "export_simple"):
                    raise ValueError(
                        "export-simple is a local repository administration command"
                    )
                _emit(registry.export_simple(Path(args.out)))
            elif args.command == "run":
                from .runtime import load

                positional, keywords = json.loads(args.args), json.loads(args.kwargs)
                if not isinstance(positional, list) or not isinstance(keywords, dict):
                    raise ValueError(
                        "--args must be a JSON list and --kwargs a JSON object"
                    )
                value = load(
                    registry, json.loads(Path(args.lock).read_text()), Path(args.target)
                )
                if not callable(value):
                    raise ValueError("selected export is not callable")
                _emit(value(*positional, **keywords))
        return 0
    except (ValueError, KeyError, OSError, TypeError, ImportError) as exc:
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

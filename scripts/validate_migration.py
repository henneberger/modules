"""Run copied Mari semantic tests against verified, extracted cell wheels.

This test-only namespace bridge adapts historical import paths. It is not the
public runtime, and never executes a source file from the copied package.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path

from module_families.compiler import compile_project
from module_families.registry import _wheel_files


class LegacyModule(types.ModuleType):
    """Adapt the one upstream test that patches an original module global."""

    def __setattr__(self, name, value):
        adapter = self.__dict__.get("_patch_adapter")
        if adapter and name == "polar_scores":
            adapter.__globals__["polar_scores"] = value
        super().__setattr__(name, value)


class LegacyFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, project, package_root: Path, installed: Path):
        self.project, self.installed = project, installed
        self.modules = {}
        for path in package_root.rglob("*.py"):
            relative = path.relative_to(package_root.parent).with_suffix("")
            parts = relative.parts
            name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            self.modules[name] = path.name == "__init__.py"

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "mark_kit" and not fullname.startswith("mark_kit."):
            return None
        if fullname not in self.modules:
            raise ModuleNotFoundError(
                f"Unknown migrated module; source fallback is forbidden: {fullname}"
            )
        return importlib.util.spec_from_loader(
            fullname, self, is_package=self.modules[fullname]
        )

    def create_module(self, spec):
        return LegacyModule(spec.name)

    def exec_module(self, module):
        prefix = module.__name__ + ":"
        for symbol, record in self.project.symbols.items():
            if not symbol.startswith(prefix):
                continue
            cell = self.project.cells[record["cell"]]
            if not (
                self.installed / (cell.import_module.replace(".", "/") + ".py")
            ).is_file():
                # Unreferenced private helpers/constant-only roots are not
                # included in a build of all public function/class members.
                continue
            cell_module = importlib.import_module(cell.import_module)
            # A lookup of every bridged value proves that its implementation
            # was obtained from the verified installation, not a source path.
            if not Path(cell_module.__file__).is_relative_to(self.installed):
                raise AssertionError(
                    f"Cell escaped verified installation: {cell.import_module}"
                )
            module.__dict__[symbol[len(prefix) :]] = getattr(
                cell_module, record["export"]
            )
        module.__file__ = "<migrated namespace " + module.__name__ + ">"
        module.__all__ = sorted(
            name for name in module.__dict__ if not name.startswith("_")
        )
        if self.modules[module.__name__]:
            module.__path__ = []
        if module.__name__ == "mark_kit.retrieval.index":
            module.__dict__["_patch_adapter"] = module.__dict__["search_index"]


class ResultSummary:
    def __init__(self):
        self.counts = {}

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        self.counts = {
            key: len(value) for key, value in terminalreporter.stats.items() if key
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("families/mari/family.toml")
    )
    parser.add_argument("--build", type=Path, default=Path("dist/mari"))
    parser.add_argument(
        "--report", type=Path, default=Path("docs/migration-validation.json")
    )
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    manifest_path = (repository / args.manifest).resolve()
    from module_families.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    source_root = (manifest_path.parent / manifest["source"]["root"]).resolve()
    source_project = source_root.parent
    build = (repository / args.build).resolve()
    index = json.loads((build / "index.json").read_text())
    project = compile_project(
        source_root,
        manifest["source"]["package"],
        manifest["name"],
        manifest.get("external_dependencies", {}),
        manifest.get("dynamic_dependencies", {}),
    )
    if index.get("build", {}).get("source_digest") != project.source_digest:
        raise ValueError("Build source digest does not match the copied source tree")
    if any(name == "mark_kit" or name.startswith("mark_kit.") for name in sys.modules):
        raise RuntimeError(
            "Start a fresh interpreter: original mark_kit is already imported"
        )
    # An editable installation can add the original package source via .pth.
    sys.path[:] = [
        entry for entry in sys.path if not (Path(entry or ".") / "mark_kit").is_dir()
    ]
    sys.path.insert(0, str(source_project))  # tests/examples/docs only; no src
    summary = ResultSummary()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="module-families-migration-") as directory:
        installed = Path(directory)
        for artifact in index["artifacts"]:
            wheel = build / artifact["filename"]
            if hashlib.sha256(wheel.read_bytes()).hexdigest() != artifact["sha256"]:
                raise ValueError(f"Artifact SHA-256 mismatch: {wheel.name}")
            for name, data in _wheel_files(wheel, artifact).items():
                target = installed / name
                if target.exists() and target.read_bytes() != data:
                    raise ValueError(f"Conflicting wheel payload: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        sys.path.insert(0, str(installed))
        finder = LegacyFinder(project, source_root / "mark_kit", installed)
        sys.meta_path.insert(0, finder)
        import pytest

        # Historical package layout/installed-distribution assertions describe
        # the old monolith; every semantic test is retained, including its
        # approximate-scoring authorization regression via the adapter above.
        with contextlib.chdir(source_project):
            exit_code = pytest.main(
                [
                    "-q",
                    "-c",
                    str(repository / "pyproject.toml"),
                    str(source_project / "tests"),
                    "--ignore="
                    + str(source_project / "tests" / "test_architecture.py"),
                ],
                plugins=[summary],
            )
        originals = []
        for name, module in sys.modules.items():
            if name == "mark_kit" or name.startswith("mark_kit."):
                if not isinstance(module, LegacyModule):
                    originals.append(name)
        if originals:
            raise AssertionError(f"Original package execution detected: {originals}")
    report = {
        "schema_version": 1,
        "experiment": "copied Mari semantic suite against extracted wheel cells",
        "python": sys.version.split()[0],
        "source_digest": project.source_digest,
        "members": len(index["members"]),
        "artifacts": len(index["artifacts"]),
        "seconds": round(time.monotonic() - started, 3),
        "pytest_exit_code": int(exit_code),
        "results": summary.counts,
        "original_package_modules_executed": originals,
        "excluded_files": ["tests/test_architecture.py"],
        "adaptations": [
            "Original import paths are synthetic namespaces delegating to verified wheel cells.",
            "The polar_scores monkeypatch forwards to search_index's compiled global binding.",
        ],
    }
    report_path = repository / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())

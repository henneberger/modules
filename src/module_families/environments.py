"""Resolve complete wheel environments and replay them offline in private venvs."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from . import __version__
from .assemblies import verify_assembly
from .registry import RegistryError, _no_symlinks, _path, _root_path, canonical_bytes
from .wheels import build_wheel


class EnvironmentError(ValueError):
    """Dependency resolution, environment integrity, or offline replay failed."""


def _digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def interpreter(python: str = sys.executable) -> dict:
    code = """import hashlib, json, os, platform, sys, sysconfig
from pathlib import Path
version = sys.implementation.version
implementation_version = '.'.join(map(str, (version.major, version.minor, version.micro)))
if version.releaselevel != 'final':
    implementation_version += version.releaselevel[0] + str(version.serial)
markers = {
    'implementation_name': sys.implementation.name,
    'implementation_version': implementation_version,
    'os_name': os.name,
    'platform_machine': platform.machine(),
    'platform_release': platform.release(),
    'platform_system': platform.system(),
    'platform_version': platform.version(),
    'python_full_version': platform.python_version(),
    'platform_python_implementation': platform.python_implementation(),
    'python_version': '.'.join(platform.python_version_tuple()[:2]),
    'sys_platform': sys.platform,
}
print(json.dumps({'version':sys.version.split()[0], 'implementation':sys.implementation.name,
    'cache_tag':sys.implementation.cache_tag, 'platform':sysconfig.get_platform(),
    'binary_sha256':hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
    'markers': markers}))
"""
    process = subprocess.run(
        [str(python), "-I", "-c", code], capture_output=True, text=True, check=True
    )
    return json.loads(process.stdout)


def _wheel(path: Path) -> dict:
    try:
        return _read_wheel(path)
    except EnvironmentError:
        raise
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as error:
        raise EnvironmentError(f"invalid wheel {path.name}: {error}") from error


def _read_wheel(path: Path) -> dict:
    name, version, _, _ = parse_wheel_filename(path.name)
    with zipfile.ZipFile(path) as archive:
        files = {}
        for entry in archive.infolist():
            filename = _path(entry.filename.rstrip("/"))
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise EnvironmentError("wheel contains a symlink or special file")
            if entry.is_dir():
                continue
            if filename in files or entry.file_size > 1024 * 1024 * 1024:
                raise EnvironmentError("invalid wheel entries")
            files[filename] = archive.read(entry)
        records = [key for key in files if key.endswith(".dist-info/RECORD")]
        if len(records) != 1:
            raise EnvironmentError("wheel must have one RECORD")
        prefix = records[0].rsplit("/", 1)[0]
        metadata = BytesParser().parsebytes(files[prefix + "/METADATA"])
        if canonicalize_name(metadata["Name"]) != name or metadata["Version"] != str(
            version
        ):
            raise EnvironmentError("wheel metadata identity mismatch")
        seen = set()
        for filename, digest, size in csv.reader(
            io.StringIO(files[records[0]].decode())
        ):
            _path(filename)
            if filename in seen or filename not in files:
                raise EnvironmentError("invalid wheel RECORD path")
            seen.add(filename)
            if filename == records[0]:
                if digest or size:
                    raise EnvironmentError("RECORD cannot hash itself")
                continue
            algorithm, separator, expected = digest.partition("=")
            if not separator or algorithm not in {"sha256", "sha384", "sha512"}:
                raise EnvironmentError("wheel files require SHA-2 hashes")
            actual = (
                base64.urlsafe_b64encode(
                    hashlib.new(algorithm, files[filename]).digest()
                )
                .decode()
                .rstrip("=")
            )
            if actual != expected or str(len(files[filename])) != size:
                raise EnvironmentError("wheel RECORD hash mismatch")
        if seen != set(files):
            raise EnvironmentError("wheel has unrecorded files")
    return {
        "distribution": str(name),
        "version": str(version),
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "requires_dist": sorted(metadata.get_all("Requires-Dist", [])),
        "requires_python": metadata.get("Requires-Python"),
    }


def _validate_dependencies(records: dict[str, dict], fingerprint: dict) -> None:
    """Check the complete selected dependency graph, including active extras."""
    extras = {name: set() for name in records}
    for record in records.values():
        if record["requires_python"] and not SpecifierSet(
            record["requires_python"]
        ).contains(Version(fingerprint["version"]), prereleases=True):
            raise EnvironmentError(
                f"locked wheel requires incompatible Python: {record['distribution']}"
            )
    changed = True
    while changed:
        changed = False
        for name, record in records.items():
            for declaration in record["requires_dist"]:
                requirement = Requirement(declaration)
                if requirement.marker is not None and not any(
                    requirement.marker.evaluate(
                        {**fingerprint["markers"], "extra": extra}
                    )
                    for extra in {"", *extras[name]}
                ):
                    continue
                dependency = canonicalize_name(requirement.name)
                target = records.get(dependency)
                if target is None:
                    raise EnvironmentError(
                        f"locked dependency is missing: {name} requires {requirement}"
                    )
                if not requirement.specifier.contains(
                    Version(target["version"]), prereleases=True
                ):
                    raise EnvironmentError(
                        f"locked dependency version mismatch: {name} requires {requirement}, selected {target['version']}"
                    )
                additional = set(requirement.extras) - extras[dependency]
                if additional:
                    extras[dependency].update(additional)
                    changed = True


def _runtime_wheel(directory: Path) -> Path:
    package = Path(__file__).parent
    files = {
        "module_families/" + p.name: p.read_bytes()
        for p in package.iterdir()
        if p.suffix == ".py" or p.name == "py.typed"
    }
    license_path = package.parent.parent / "LICENSE"
    if not license_path.is_file():
        distribution = importlib.metadata.distribution("module-families")
        candidates = [
            distribution.locate_file(path)
            for path in distribution.files or []
            if str(path).endswith("/licenses/LICENSE")
        ]
        if not candidates:
            raise EnvironmentError("runtime distribution license is missing")
        license_path = candidates[0]
    artifact = build_wheel(
        directory,
        distribution="module-families",
        version=__version__,
        files=files,
        requires_dist=["packaging>=24.0"],
        license_files={"LICENSE": license_path.read_bytes()},
        description="Module Families locked execution runtime",
    )
    return directory / artifact["filename"]


def lock_environment(
    assembly: dict,
    repository,
    directory: str | Path,
    *,
    python: str = sys.executable,
    find_links: list[str] | None = None,
    no_index: bool = False,
) -> dict:
    """Let pip resolve wheel dependencies, then retain exact hashes for replay.

    This explicit operation may download packages. It does not execute candidate
    modules or source builds: only prebuilt wheels are accepted.
    """
    verify_assembly(assembly, repository)
    root = _root_path(Path(directory))
    _no_symlinks(root)
    if root.exists() and any(root.iterdir()):
        raise EnvironmentError(f"environment lock destination must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    fingerprint = interpreter(python)
    with tempfile.TemporaryDirectory(prefix="mf-environment-") as temporary:
        staging = Path(temporary)
        internal, downloaded = staging / "internal", staging / "downloaded"
        internal.mkdir()
        downloaded.mkdir()
        artifacts = {}
        members = []
        for binding in assembly["bindings"].values():
            for artifact in binding["artifacts"]:
                name = canonicalize_name(artifact["distribution"])
                if name in artifacts and artifacts[name] != artifact:
                    raise EnvironmentError(f"conflicting generated dependency: {name}")
                artifacts[name] = artifact
            members.append(str(internal / binding["member"]["wheel"]))
        for artifact in artifacts.values():
            blob = repository._blob(artifact["sha256"])
            if hashlib.sha256(blob.read_bytes()).hexdigest() != artifact["sha256"]:
                raise EnvironmentError("repository artifact hash mismatch")
            shutil.copyfile(blob, internal / artifact["filename"])
        runtime = _runtime_wheel(internal)
        command = [
            str(python),
            "-I",
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--dest",
            str(downloaded),
            "--find-links",
            str(internal),
        ]
        for link in find_links or []:
            command.extend(["--find-links", str(link)])
        if no_index:
            command.append("--no-index")
        process = subprocess.run(
            [*command, str(runtime), *sorted(set(members))],
            capture_output=True,
            text=True,
        )
        if process.returncode:
            # Avoid echoing registry URLs or credentials from pip configuration.
            raise EnvironmentError(
                "pip could not resolve a compatible wheel-only environment; review requirements and available wheels"
            )
        wheels = [_wheel(path) for path in sorted(downloaded.glob("*.whl"))]
        if not wheels or len({record["distribution"] for record in wheels}) != len(
            wheels
        ):
            raise EnvironmentError(
                "environment must select exactly one wheel per distribution"
            )
        selected = {record["distribution"]: record for record in wheels}
        _validate_dependencies(selected, fingerprint)
        for name, artifact in artifacts.items():
            if name not in selected or selected[name]["sha256"] != artifact["sha256"]:
                raise EnvironmentError(
                    f"resolver replaced a pinned module artifact: {name}"
                )
        lock = {
            "schema_version": 1,
            "format": "module-families-environment-lock",
            "assembly": copy.deepcopy(assembly),
            "interpreter": fingerprint,
            "wheels": wheels,
            "runtime": _wheel(runtime),
            "wheelhouse": "wheels",
        }
        lock["sha256"] = _digest(lock)
        wheelhouse = root / "wheels"
        wheelhouse.mkdir()
        for record in wheels:
            shutil.copyfile(
                downloaded / record["filename"], wheelhouse / record["filename"]
            )
        (root / "environment.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    return {
        "lock": str(root / "environment.lock.json"),
        "sha256": lock["sha256"],
        "wheels": len(wheels),
        "environment_locked": True,
    }


def verify_environment(
    path: str | Path, *, python: str = sys.executable
) -> tuple[dict, Path]:
    try:
        return _verify_environment(path, python=python)
    except EnvironmentError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise EnvironmentError(f"invalid environment lock: {error}") from error


def _verify_environment(path: str | Path, *, python: str) -> tuple[dict, Path]:
    path = Path(path).resolve()
    lock = json.loads(path.read_text())
    if (
        not isinstance(lock, dict)
        or lock.get("format") != "module-families-environment-lock"
        or lock.get("schema_version") != 1
        or _digest({k: v for k, v in lock.items() if k != "sha256"})
        != lock.get("sha256")
    ):
        raise EnvironmentError("environment lock hash or format mismatch")
    if set(lock) != {
        "schema_version",
        "format",
        "assembly",
        "interpreter",
        "wheels",
        "runtime",
        "wheelhouse",
        "sha256",
    }:
        raise EnvironmentError("environment lock has missing or unknown fields")
    verify_assembly(lock["assembly"])
    if interpreter(python) != lock["interpreter"]:
        raise EnvironmentError(
            "interpreter differs from locked Python version, platform, or binary"
        )
    if lock.get("wheelhouse") != "wheels":
        raise EnvironmentError("wheelhouse must be the lock's local wheels directory")
    wheelhouse = path.parent / "wheels"
    _no_symlinks(wheelhouse)
    records = {}
    if not isinstance(lock["wheels"], list) or not lock["wheels"]:
        raise EnvironmentError("environment must contain locked wheels")
    for record in lock["wheels"]:
        if Path(record["filename"]).name != record["filename"]:
            raise EnvironmentError("invalid wheel filename")
        wheel = wheelhouse / record["filename"]
        _no_symlinks(wheel)
        if record["distribution"] in records or _wheel(wheel) != record:
            raise EnvironmentError("locked wheel integrity or uniqueness mismatch")
        records[record["distribution"]] = record
    if records.get("module-families") != lock["runtime"]:
        raise EnvironmentError("locked execution runtime is absent or changed")
    for binding in lock["assembly"]["bindings"].values():
        for artifact in binding["artifacts"]:
            record = records.get(canonicalize_name(artifact["distribution"]))
            if record is None or record["sha256"] != artifact["sha256"]:
                raise EnvironmentError("environment omits a pinned module dependency")
    _validate_dependencies(records, lock["interpreter"])
    return lock, wheelhouse


def sync_environment(
    path: str | Path, target: str | Path, *, python: str = sys.executable
) -> dict:
    """Verify every wheel, then install an isolated environment without network."""
    lock, wheelhouse = verify_environment(path, python=python)
    target = _root_path(Path(target))
    _no_symlinks(target)
    if target.exists():
        raise EnvironmentError(f"environment target already exists: {target}")
    subprocess.run(
        [str(python), "-I", "-m", "venv", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    executable = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = target / ".mf-requirements.txt"
    requirements.write_text(
        "\n".join(
            f"{record['distribution']}=={record['version']} --hash=sha256:{record['sha256']}"
            for record in lock["wheels"]
        )
        + "\n"
    )
    process = subprocess.run(
        [
            str(executable),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(requirements),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise EnvironmentError(
            "offline installation failed; incomplete environment was not activated"
        )
    checked = subprocess.run(
        [str(executable), "-I", "-m", "pip", "--isolated", "check"],
        capture_output=True,
        text=True,
    )
    if checked.returncode:
        raise EnvironmentError("installed dependency environment is inconsistent")
    (target / ".mf-environment.json").write_text(
        json.dumps({"sha256": lock["sha256"]}) + "\n"
    )
    return {
        "target": str(target),
        "python": str(executable),
        "sha256": lock["sha256"],
        "wheels": len(lock["wheels"]),
        "offline": True,
    }


class FrozenRepository:
    """Selected trusted lock metadata plus the locally verified wheelhouse."""

    def __init__(self, lock: dict, wheelhouse: Path):
        self.assembly = lock["assembly"]
        self.wheels = {
            record["sha256"]: wheelhouse / record["filename"]
            for record in lock["wheels"]
        }

    def interface(self, identifier, version):
        for spec in self.assembly["interfaces"]:
            if spec["id"] == identifier and spec["version"] == version:
                return copy.deepcopy(spec)
        raise RegistryError("interface is absent from frozen environment")

    def _verify_lock(self, selected):
        if not any(
            canonical_bytes(selected) == canonical_bytes(value)
            for value in self.assembly["bindings"].values()
        ):
            raise RegistryError("member is absent from frozen environment")
        return copy.deepcopy(selected)

    def _blob(self, digest):
        path = self.wheels.get(digest)
        if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RegistryError("frozen artifact is absent or corrupt")
        return path


def run_environment(
    path: str | Path,
    target: str | Path,
    *,
    export: str,
    args: list | None = None,
    kwargs: dict | None = None,
    timeout: float | None = None,
) -> dict:
    """Execute through the locked runtime in a separately installed interpreter."""
    if not isinstance(export, str) or not export.isidentifier():
        raise EnvironmentError("export must be a Python identifier")
    if timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise EnvironmentError("timeout must be finite positive seconds")
    if args is not None and not isinstance(args, list):
        raise EnvironmentError("args must be a list")
    if kwargs is not None and (
        not isinstance(kwargs, dict) or any(not isinstance(key, str) for key in kwargs)
    ):
        raise EnvironmentError("kwargs must map string names to values")
    target = Path(target).resolve()
    executable = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lock, _ = verify_environment(path, python=str(executable))
    if (
        json.loads((target / ".mf-environment.json").read_text()).get("sha256")
        != lock["sha256"]
    ):
        raise EnvironmentError("environment was not prepared for this lock")
    process = subprocess.run(
        [
            str(executable),
            "-I",
            "-m",
            "module_families.worker",
            str(Path(path).resolve()),
            str(target / ".mf-site"),
            export,
            json.dumps(args or []),
            json.dumps(kwargs or {}),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode:
        raise EnvironmentError(
            f"locked program execution failed: {process.stderr.strip()}"
        )
    return json.loads(process.stdout)

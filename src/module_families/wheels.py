"""Small deterministic, standards-compatible wheel writer.

The builder deliberately accepts bytes rather than a directory tree: no files
outside the explicitly compiled closure can enter an artifact accidentally.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path, PurePosixPath


def normalize_distribution(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name):
        raise ValueError(f"Invalid distribution name: {name!r}")
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_filename(distribution: str, version: str) -> str:
    distribution = normalize_distribution(distribution)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
        raise ValueError(f"Invalid wheel version: {version!r}")
    return f"{distribution.replace('-', '_')}-{version}-py3-none-any.whl"


def build_wheel(
    output: Path,
    *,
    distribution: str,
    version: str,
    files: dict[str, bytes | str],
    requires_dist: list[str] | tuple[str, ...] = (),
    description: str = "",
    license_files: dict[str, bytes] | None = None,
    provenance: bytes | str | None = None,
) -> dict:
    """Write one reproducible wheel and return its byte identity.

    Namespace directories contain no __init__.py: independently installed
    wheels share ``mf_cells`` and ``mf_members`` without owning common files.
    """
    distribution = normalize_distribution(distribution)
    filename = wheel_filename(distribution, version)
    dist_info = f"{distribution.replace('-', '_')}-{version}.dist-info"
    entries = {}
    for name, data in files.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or str(path) != name
        ):
            raise ValueError(f"Unsafe wheel path: {name!r}")
        entries[name] = data.encode() if isinstance(data, str) else data
    requirements = sorted(set(requires_dist))
    for requirement in requirements:
        if not requirement.strip() or "\n" in requirement or "\r" in requirement:
            raise ValueError(f"Invalid requirement: {requirement!r}")
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {distribution}",
        f"Version: {version}",
        f"Summary: {' '.join(description.split())[:400]}",
        "Requires-Python: >=3.11",
        "Description-Content-Type: text/plain; charset=UTF-8",
    ]
    metadata.extend(f"Requires-Dist: {item}" for item in requirements)
    for name, data in sorted((license_files or {}).items()):
        if PurePosixPath(name).name != name or name in {".", ".."}:
            raise ValueError(f"Invalid license filename: {name!r}")
        entries[f"{dist_info}/licenses/{name}"] = data
        metadata.append(f"License-File: {name}")
    entries[f"{dist_info}/METADATA"] = (
        "\n".join(metadata) + "\n\n" + description + "\n"
    ).encode()
    entries[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: module-families 1\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n"
    ).encode()
    if provenance is not None:
        entries[f"{dist_info}/module-families.json"] = (
            provenance.encode() if isinstance(provenance, str) else provenance
        )
    record_name = f"{dist_info}/RECORD"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(entries.items()):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode()
        )
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((record_name, "", ""))
    entries[record_name] = record.getvalue().encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )
    content = buffer.getvalue()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / filename
    if destination.is_symlink():
        raise ValueError(f"Wheel output must not be a symlink: {destination}")
    # A filename or timestamp is not evidence that an artifact is current.
    # Compare the complete deterministic bytes before reusing an old wheel.
    if not destination.exists() or destination.read_bytes() != content:
        destination.write_bytes(content)
    return {
        "distribution": distribution,
        "version": version,
        "filename": filename,
        "sha256": hashlib.sha256(content).hexdigest(),
        "requires_dist": requirements,
    }

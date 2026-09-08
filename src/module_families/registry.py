"""Import-free local publication, discovery, and exact module artifact locks.

This module deliberately does not resolve third-party Python requirements or
claim that declared effects constrain code once it is executed.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


class RegistryError(ValueError):
    """Invalid publication, incompatible policy, or failed integrity check."""


def canonical_bytes(value: Any) -> bytes:
    """The version-1 format uses UTF-8, sorted keys, and compact JSON."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value
    ):
        raise RegistryError(f"Invalid distribution name: {value!r}")
    return re.sub(r"[-_.]+", "-", value).lower()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise RegistryError(f"{field} must be a nonempty string")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(v, str) or not v.strip() for v in value
    ):
        raise RegistryError(f"{field} must be a list of nonempty strings")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RegistryError("sha256 must be 64 lowercase hexadecimal characters")
    return value


def _filename(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl", value
    ):
        raise RegistryError(f"Unsafe wheel filename: {value!r}")
    return value


def _path(value: str) -> str:
    """Validate a portable relative archive path, before filesystem access."""
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or value.startswith("/")
        or any(p in ("", ".", "..") for p in value.split("/"))
    ):
        raise RegistryError(f"Unsafe wheel path: {value!r}")
    return str(PurePosixPath(value))


def _no_symlinks(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RegistryError(f"Symlink is not allowed: {parent}")


def _root_path(path: Path) -> Path:
    # Canonicalize user-selected roots (including macOS /tmp -> /private/tmp),
    # while forbidding a symlink as the selected destination itself. Descendants
    # are checked separately so an archive cannot redirect writes through one.
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise RegistryError(f"Symlink is not allowed: {path}")
    return path.parent.resolve() / path.name


def _json_file(path: Path) -> dict[str, Any]:
    _no_symlinks(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            result = json.load(stream)
    except (OSError, ValueError) as exc:
        raise RegistryError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise RegistryError("A publication or lock must be a JSON object")
    return result


def _file_hash(path: Path) -> str:
    _no_symlinks(path)
    if not path.is_file():
        raise RegistryError(f"Missing artifact: {path}")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _wheel_files(path: Path, artifact: dict[str, Any]) -> dict[str, bytes]:
    """Validate wheel structure, identities, RECORD, and installation paths."""
    if _file_hash(path) != artifact["sha256"]:
        raise RegistryError(f"Artifact hash mismatch: {artifact['filename']}")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            total_size = 0
            for entry in archive.infolist():
                name = _path(
                    entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                )
                file_type = stat.S_IFMT(entry.external_attr >> 16)
                if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise RegistryError(
                        f"Wheel contains symlink or special file: {name}"
                    )
                if entry.is_dir():
                    continue
                if name in files:
                    raise RegistryError(f"Duplicate wheel path: {name}")
                total_size += entry.file_size
                if (
                    entry.file_size > 256 * 1024 * 1024
                    or total_size > 1024 * 1024 * 1024
                ):
                    raise RegistryError("Wheel exceeds local extraction size limits")
                files[name] = archive.read(entry)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise RegistryError(f"Invalid wheel {artifact['filename']}: {exc}") from exc
    dist_infos = {
        name.split("/")[0]
        for name in files
        if name.split("/")[0].endswith(".dist-info")
    }
    if len(dist_infos) != 1:
        raise RegistryError("A wheel must contain exactly one dist-info directory")
    dist_info = next(iter(dist_infos))
    for required in ("METADATA", "WHEEL", "RECORD"):
        if f"{dist_info}/{required}" not in files:
            raise RegistryError(f"Wheel is missing {required}")
    metadata = BytesParser(policy=compat32).parsebytes(files[f"{dist_info}/METADATA"])
    if (
        _name(metadata.get("Name", "")) != _name(artifact["distribution"])
        or metadata.get("Version") != artifact["version"]
    ):
        raise RegistryError("Wheel METADATA identity does not match artifact")
    if sorted(metadata.get_all("Requires-Dist", [])) != sorted(
        artifact["requires_dist"]
    ):
        raise RegistryError("Wheel Requires-Dist does not match artifact")
    expected_stem = artifact["filename"].removesuffix(".whl").split("-")
    if len(expected_stem) not in (5, 6):
        raise RegistryError("Invalid wheel filename format")
    if _name(expected_stem[0]) != _name(artifact["distribution"]) or expected_stem[
        1
    ] != artifact["version"].replace("-", "_"):
        raise RegistryError("Wheel filename identity does not match artifact")
    if dist_info != f"{expected_stem[0]}-{expected_stem[1]}.dist-info":
        raise RegistryError("Wheel dist-info identity does not match filename")
    wheel = BytesParser(policy=compat32).parsebytes(files[f"{dist_info}/WHEEL"])
    if wheel.get("Wheel-Version", "").split(".")[0] != "1":
        raise RegistryError("Unsupported Wheel-Version")
    # Materialization is intentionally limited to pure Python site directories.
    if wheel.get("Root-Is-Purelib", "").lower() != "true":
        raise RegistryError(
            "This registry materializer supports pure Python wheels only"
        )
    record_name = f"{dist_info}/RECORD"
    seen: set[str] = set()
    try:
        rows = csv.reader(io.StringIO(files[record_name].decode("utf-8")))
        for row in rows:
            if len(row) != 3:
                raise RegistryError("Invalid wheel RECORD row")
            name, encoded_hash, size = row
            _path(name)
            if name in seen or name not in files:
                raise RegistryError("Wheel RECORD has duplicate or nonexistent file")
            seen.add(name)
            if name == record_name:
                if encoded_hash or size:
                    raise RegistryError("RECORD must not hash itself")
                continue
            if not encoded_hash or not size:
                raise RegistryError(f"Unhashed wheel file: {name}")
            algorithm, separator, expected_hash = encoded_hash.partition("=")
            if not separator or algorithm not in ("sha256", "sha384", "sha512"):
                raise RegistryError("Wheel RECORD must use SHA-256 or stronger SHA-2")
            actual_hash = (
                base64.urlsafe_b64encode(hashlib.new(algorithm, files[name]).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            if actual_hash != expected_hash or str(len(files[name])) != size:
                raise RegistryError(f"Wheel RECORD mismatch: {name}")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RegistryError(f"Invalid wheel RECORD: {exc}") from exc
    if seen != set(files):
        raise RegistryError("Wheel contains files missing from RECORD")
    for name in files:
        if name.split("/")[0].endswith(".data"):
            raise RegistryError("Wheel .data installation schemes are not supported")
    return files


def _effects_allowed(
    card: dict[str, Any], allowed_effects: list[str] | set[str] | None
) -> bool:
    if allowed_effects is None:
        return True
    effects = card.get("effects")
    if not isinstance(effects, list) or any(not isinstance(v, str) for v in effects):
        return False
    # Unknown is not an effect that a permissive label can authorize.
    return not any(
        v.lower() in ("unknown", "unspecified", "*") for v in effects
    ) and set(effects) <= set(allowed_effects)


def _contract(card: dict[str, Any]) -> str | None:
    provides = card.get("provides")
    if isinstance(provides, dict):
        return provides.get("id")
    return None


def _publisher(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", value):
        raise RegistryError(
            "Publisher must contain lowercase letters, digits, and hyphens"
        )
    return value


def _owner(index: dict) -> str:
    return index["publisher"]


def _validate_index(index: dict[str, Any]) -> dict[str, Any]:
    # Round-trip rejects non-JSON values and avoids retaining caller-owned data.
    try:
        index = json.loads(canonical_bytes(index))
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"Invalid index JSON: {exc}") from exc
    if not isinstance(index, dict):
        raise RegistryError("A publication index must be an object")
    if index.get("schema_version") != 1:
        raise RegistryError("Unsupported index schema_version")
    family = index.get("family")
    if not isinstance(family, dict):
        raise RegistryError("Index requires family metadata")
    _text(family.get("name"), "family.name")
    _text(family.get("version"), "family.version")
    publisher = _publisher(index.get("publisher"))
    interfaces = index.get("interfaces", [])
    if not isinstance(interfaces, list):
        raise RegistryError("Index interfaces must be an array")
    if interfaces:
        from .interfaces import validate_interface

        try:
            index["interfaces"] = [validate_interface(spec) for spec in interfaces]
        except ValueError as exc:
            raise RegistryError(f"Invalid interface: {exc}") from exc
        references = [(spec["id"], spec["version"]) for spec in index["interfaces"]]
        if len(set(references)) != len(references):
            raise RegistryError("Duplicate interface ID/version in publication")
        index.setdefault("members", [])
        index.setdefault("artifacts", [])
    if not isinstance(index.get("members"), list) or not isinstance(
        index.get("artifacts"), list
    ):
        raise RegistryError("Index requires members and artifacts arrays")
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact in index["artifacts"]:
        if not isinstance(artifact, dict):
            raise RegistryError("Artifact must be an object")
        name = _name(artifact.get("distribution"))
        _text(artifact.get("version"), "artifact.version")
        _filename(artifact.get("filename"))
        _hash(artifact.get("sha256"))
        _strings(artifact.get("dependencies"), "artifact.dependencies")
        _strings(artifact.get("requires_dist"), "artifact.requires_dist")
        if name in artifacts:
            raise RegistryError(f"Duplicate artifact distribution: {name}")
        artifacts[name] = artifact
    pending: dict[str, int] = {}
    parents: dict[str, list[str]] = {name: [] for name in artifacts}
    for name, artifact in artifacts.items():
        dependencies = [_name(dep) for dep in artifact["dependencies"]]
        if len(set(dependencies)) != len(dependencies):
            raise RegistryError(f"Duplicate internal dependency in {name}")
        for dependency in dependencies:
            if dependency not in artifacts:
                raise RegistryError(f"Missing internal dependency: {dependency}")
            parents[dependency].append(name)
        internal_requirements = set()
        for requirement in artifact["requires_dist"]:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement)
            if match is None:
                raise RegistryError(f"Invalid dependency requirement: {requirement}")
            requirement_name = _name(match.group(1))
            if requirement_name in artifacts:
                internal_requirements.add(requirement_name)
                exact = re.sub(r"\s+", "", match.group(2))
                if exact != "==" + artifacts[requirement_name]["version"]:
                    raise RegistryError(
                        f"Internal dependency must pin its exact artifact version: {requirement}"
                    )
        if internal_requirements != set(dependencies):
            raise RegistryError(
                f"Internal dependencies disagree with Requires-Dist: {name}"
            )
        pending[name] = len(dependencies)
    ready = [name for name, count in pending.items() if count == 0]
    checked = 0
    while ready:
        current = ready.pop()
        checked += 1
        for parent in parents[current]:
            pending[parent] -= 1
            if pending[parent] == 0:
                ready.append(parent)
    if checked != len(artifacts):
        raise RegistryError("Internal dependency graph contains a cycle")
    seen_members: set[str] = set()
    for member in index["members"]:
        if not isinstance(member, dict):
            raise RegistryError("Member must be an object")
        member_id = _text(member.get("id"), "member.id")
        local_id = _text(member.get("local_id"), "member.local_id")
        if (
            member.get("publisher") != publisher
            or member_id != f"{publisher}.{local_id}"
        ):
            raise RegistryError(
                "Publisher-qualified member identity does not match publication principal"
            )
        if member_id in seen_members:
            raise RegistryError(f"Duplicate member: {member_id}")
        seen_members.add(member_id)
        artifact = artifacts.get(_name(member.get("distribution")))
        if artifact is None:
            raise RegistryError(f"Missing member artifact: {member_id}")
        if (
            member.get("wheel") != artifact["filename"]
            or member.get("sha256") != artifact["sha256"]
        ):
            raise RegistryError(f"Member artifact identity mismatch: {member_id}")
        for field in ("import_module", "export"):
            value = _text(member.get(field), f"member.{field}")
            if not all(part.isidentifier() for part in value.split(".")):
                raise RegistryError(f"Invalid member {field}")
        if "effects" in member:
            _strings(member["effects"], "member.effects")
        if "provides" in member:
            from .interfaces import validate_interface_reference

            try:
                validate_interface_reference(member["provides"])
            except ValueError as exc:
                raise RegistryError(f"Invalid provides reference: {exc}") from exc
        from .indices import validate_indices

        try:
            validate_indices(member)
        except ValueError as exc:
            raise RegistryError(f"Invalid semantic indices: {exc}") from exc
        _text(member.get("version", family["version"]), "member.version")
        if member.get("version", family["version"]) != artifact["version"]:
            raise RegistryError("Member version does not match its artifact version")
        try:
            Version(member.get("version", family["version"]))
        except InvalidVersion as exc:
            raise RegistryError(f"Invalid member version: {exc}") from exc
        if (
            member.get("family", family["name"]) != family["name"]
            or member.get("family_version", family["version"]) != family["version"]
        ):
            raise RegistryError("Member family metadata contradicts index")
    return index


class Registry:
    """An append-only logical catalog backed by SQLite and immutable blobs."""

    def __init__(self, root: Path):
        self.root = _root_path(root)
        _no_symlinks(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "registry.sqlite3"
        _no_symlinks(self.database)
        with self._connect() as db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            has_tables = (
                db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
                ).fetchone()
                is not None
            )
            if schema != 3 and (has_tables or schema != 0):
                raise RegistryError(
                    "Unsupported registry schema; create a fresh repository for publisher-scoped publications"
                )
            db.executescript("""
                CREATE TABLE IF NOT EXISTS releases (
                    family TEXT NOT NULL, version TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY (family, version));
                CREATE TABLE IF NOT EXISTS artifacts (
                    distribution TEXT NOT NULL, version TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY (distribution, version));
                CREATE TABLE IF NOT EXISTS artifact_owners (
                    distribution TEXT PRIMARY KEY, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publications (
                    digest TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members (
                    family TEXT NOT NULL, member TEXT NOT NULL, version TEXT NOT NULL,
                    contract TEXT, card TEXT NOT NULL, snapshot TEXT NOT NULL,
                    provides_id TEXT, provides_version TEXT,
                    UNIQUE (family, member, version));
                CREATE TABLE IF NOT EXISTS interfaces (
                    id TEXT NOT NULL, version TEXT NOT NULL,
                    family TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY (id, version));
                CREATE TABLE IF NOT EXISTS interface_owners (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS member_owners (
                    family TEXT NOT NULL, member TEXT NOT NULL, owner TEXT NOT NULL,
                    PRIMARY KEY (family, member));
                CREATE INDEX IF NOT EXISTS members_family ON members(family, member);
                CREATE INDEX IF NOT EXISTS members_contract ON members(contract);
                CREATE VIRTUAL TABLE IF NOT EXISTS member_search USING fts5(text);
                CREATE INDEX IF NOT EXISTS members_provides ON members(provides_id, provides_version, family);
                PRAGMA user_version=3;
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        _no_symlinks(self.database)
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _blob(self, digest: str) -> Path:
        digest = _hash(digest)
        result = self.root / "objects" / "sha256" / digest[:2] / f"{digest}.whl"
        _no_symlinks(result)
        return result

    def _store(self, artifact: dict[str, Any], source: Path) -> None:
        target = self._blob(artifact["sha256"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _file_hash(target) != artifact["sha256"]:
                raise RegistryError("Existing content-addressed object is damaged")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".publishing-", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with (
                os.fdopen(descriptor, "wb") as output,
                source.open("rb") as input_stream,
            ):
                for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if _file_hash(temporary) != artifact["sha256"]:
                raise RegistryError("Artifact changed during publication")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if _file_hash(target) != artifact["sha256"]:
                    raise RegistryError(
                        "Conflicting content-addressed object"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)

    def publish(self, index_path: Path) -> dict[str, Any]:
        index_path = _root_path(index_path)
        index = _validate_index(_json_file(index_path))
        family = index["family"]
        principal = _owner(index)
        # All artifact bytes are checked before any catalog mutation.
        artifact_files = {}
        for artifact in index["artifacts"]:
            artifact_files[_name(artifact["distribution"])] = set(
                _wheel_files(index_path.parent / artifact["filename"], artifact)
            )
        for member in index["members"]:
            module_path = member["import_module"].replace(".", "/")
            paths = artifact_files[_name(member["distribution"])]
            if (
                f"{module_path}.py" not in paths
                and f"{module_path}/__init__.py" not in paths
            ):
                raise RegistryError("Member import_module is not provided by its wheel")
        encoded = canonical_bytes(index).decode("utf-8")
        family_record = canonical_bytes(family).decode("utf-8")
        snapshot = _digest(index)
        added_members = 0
        added_interfaces = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT record FROM releases WHERE family=? AND version=?",
                (family["name"], family["version"]),
            ).fetchone()
            if existing is not None and existing["record"] != family_record:
                raise RegistryError("Family header name/version is immutable")
            previous_snapshot = db.execute(
                "SELECT digest FROM publications WHERE digest=?", (snapshot,)
            ).fetchone()
            for spec in index.get("interfaces", []):
                owner = db.execute(
                    "SELECT owner FROM interface_owners WHERE id=?", (spec["id"],)
                ).fetchone()
                if owner is not None:
                    if owner["owner"] != principal:
                        raise RegistryError(
                            "Interface ID is owned by another publisher"
                        )
                existing_interface = db.execute(
                    "SELECT record FROM interfaces WHERE id=? AND version=?",
                    (spec["id"], spec["version"]),
                ).fetchone()
                if existing_interface is not None and existing_interface[
                    "record"
                ] != canonical_bytes(spec).decode("utf-8"):
                    raise RegistryError("Interface ID/version is immutable")
            for artifact in index["artifacts"]:
                row = db.execute(
                    "SELECT record FROM artifacts WHERE distribution=? AND version=?",
                    (_name(artifact["distribution"]), artifact["version"]),
                ).fetchone()
                if row is not None and row["record"] != canonical_bytes(
                    artifact
                ).decode("utf-8"):
                    raise RegistryError("Artifact distribution/version is immutable")
                owner = db.execute(
                    "SELECT owner FROM artifact_owners WHERE distribution=?",
                    (_name(artifact["distribution"]),),
                ).fetchone()
                if row is None and owner is not None and owner["owner"] != principal:
                    raise RegistryError(
                        "Artifact distribution is owned by another publisher"
                    )
            cards = []
            for member in index["members"]:
                owner = db.execute(
                    "SELECT owner FROM member_owners WHERE family=? AND member=?",
                    (family["name"], member["id"]),
                ).fetchone()
                if owner is not None and owner["owner"] != principal:
                    raise RegistryError("Member ID is owned by another publisher")
                card = dict(
                    member,
                    family=family["name"],
                    family_version=family["version"],
                    version=member.get("version", family["version"]),
                )
                card_record = canonical_bytes(card).decode("utf-8")
                row = db.execute(
                    "SELECT card FROM members WHERE family=? AND member=? AND version=?",
                    (family["name"], member["id"], card["version"]),
                ).fetchone()
                if row is not None and row["card"] != card_record:
                    raise RegistryError("Member family/id/version is immutable")
                if row is None:
                    cards.append(card)
            for artifact in index["artifacts"]:
                self._store(artifact, index_path.parent / artifact["filename"])
                db.execute(
                    "INSERT OR IGNORE INTO artifact_owners VALUES (?, ?)",
                    (_name(artifact["distribution"]), principal),
                )
                db.execute(
                    "INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?)",
                    (
                        _name(artifact["distribution"]),
                        artifact["version"],
                        canonical_bytes(artifact).decode("utf-8"),
                    ),
                )
            if existing is None:
                db.execute(
                    "INSERT INTO releases VALUES (?, ?, ?)",
                    (family["name"], family["version"], family_record),
                )
            db.execute(
                "INSERT OR IGNORE INTO publications VALUES (?, ?)", (snapshot, encoded)
            )
            for spec in index.get("interfaces", []):
                db.execute(
                    "INSERT OR IGNORE INTO interface_owners VALUES (?, ?)",
                    (spec["id"], principal),
                )
                cursor = db.execute(
                    "INSERT OR IGNORE INTO interfaces VALUES (?, ?, ?, ?)",
                    (
                        spec["id"],
                        spec["version"],
                        family["name"],
                        canonical_bytes(spec).decode("utf-8"),
                    ),
                )
                added_interfaces += cursor.rowcount
            for card in cards:
                db.execute(
                    "INSERT OR IGNORE INTO member_owners VALUES (?, ?, ?)",
                    (family["name"], card["id"], principal),
                )
                provides = card.get("provides", {})
                cursor = db.execute(
                    "INSERT INTO members (family, member, version, contract, card, snapshot, provides_id, provides_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        family["name"],
                        card["id"],
                        card["version"],
                        _contract(card),
                        canonical_bytes(card).decode("utf-8"),
                        snapshot,
                        provides.get("id"),
                        provides.get("version"),
                    ),
                )
                searchable = canonical_bytes({"family": family, "member": card}).decode(
                    "utf-8"
                )
                db.execute(
                    "INSERT INTO member_search(rowid, text) VALUES (?, ?)",
                    (cursor.lastrowid, searchable),
                )
                added_members += 1
        return {
            "family": family["name"],
            "version": family["version"],
            "members": len(index["members"]),
            "added_members": added_members,
            "artifacts": len(index["artifacts"]),
            "interfaces": len(index.get("interfaces", [])),
            "added_interfaces": added_interfaces,
            "sha256": snapshot,
            "already_published": previous_snapshot is not None,
        }

    def search(
        self,
        query: str,
        *,
        family: str | None = None,
        contract: str | None = None,
        allowed_effects: list[str] | set[str] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(query, str)
            or not isinstance(limit, int)
            or not isinstance(offset, int)
            or limit < 0
            or offset < 0
        ):
            raise RegistryError("Search requires text and nonnegative pagination")
        if limit > 1000:
            raise RegistryError("Search limit must not exceed 1000")
        if limit == 0:
            return []
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        clauses = [
            "m.rowid=(SELECT MAX(latest.rowid) FROM members latest WHERE latest.family=m.family AND latest.member=m.member)"
        ]
        arguments: list[Any] = []
        if family is not None:
            clauses.append("m.family=?")
            arguments.append(family)
        if contract is not None:
            clauses.append("m.contract=?")
            arguments.append(contract)
        join = ""
        order = "m.family, m.member, m.version"
        if tokens:
            join = "JOIN member_search ON member_search.rowid=m.rowid"
            clauses.append("member_search MATCH ?")
            arguments.append(" OR ".join('"' + token + '"' for token in tokens[:100]))
            order = "bm25(member_search), " + order
        sql = f"SELECT m.card FROM members m {join} WHERE {' AND '.join(clauses)} ORDER BY {order}"
        results: list[dict[str, Any]] = []
        skipped = 0
        with self._connect() as db:
            for row in db.execute(sql, arguments):
                card = json.loads(row["card"])
                if not _effects_allowed(card, allowed_effects):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                results.append(card)
                if len(results) >= limit:
                    break
        return results

    def inspect(
        self, family: str, member: str, version: str | None = None
    ) -> dict[str, Any]:
        clauses = "family=? AND member=?"
        arguments = [family, member]
        if version is not None:
            clauses += " AND version=?"
            arguments.append(version)
        with self._connect() as db:
            row = db.execute(
                f"SELECT card FROM members WHERE {clauses} ORDER BY rowid DESC LIMIT 1",
                arguments,
            ).fetchone()
        if row is None:
            raise RegistryError(
                f"Unknown member: {family}/{member}"
                + (f"@{version}" if version else "")
            )
        return json.loads(row["card"])

    def families(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List immutable family headers, including their context versions."""
        if (
            not isinstance(limit, int)
            or not isinstance(offset, int)
            or not 0 <= limit <= 1000
            or offset < 0
        ):
            raise RegistryError(
                "Family listing requires limit 0..1000 and nonnegative offset"
            )
        with self._connect() as db:
            return [
                json.loads(row["record"])
                for row in db.execute(
                    "SELECT record FROM releases ORDER BY family, version LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def versions(self, family: str, member: str) -> list[dict[str, Any]]:
        """List published member versions, most recent publication first."""
        with self._connect() as db:
            return [
                json.loads(row["card"])
                for row in db.execute(
                    "SELECT card FROM members WHERE family=? AND member=? ORDER BY rowid DESC",
                    (family, member),
                )
            ]

    def interface(self, id: str, version: str) -> dict[str, Any]:
        """Read one immutable interface specification without importing providers."""
        with self._connect() as db:
            row = db.execute(
                "SELECT record FROM interfaces WHERE id=? AND version=?", (id, version)
            ).fetchone()
        if row is None:
            raise RegistryError(f"Unknown interface: {id}@{version}")
        return json.loads(row["record"])

    def interfaces(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 0 <= limit <= 1000
            or offset < 0
        ):
            raise RegistryError(
                "Interface listing requires limit 0..1000 and nonnegative offset"
            )
        with self._connect() as db:
            return [
                json.loads(row["record"])
                for row in db.execute(
                    "SELECT record FROM interfaces ORDER BY id, version LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def candidates(
        self,
        contract_id: str,
        contract_version: str,
        *,
        family: str | None = None,
        version_spec: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Enumerate exact declared providers, newest PEP 440 versions first.

        Unlike ranked search this includes every matching immutable member
        release, with explicit pagination; legacy contract labels do not count.
        """
        _text(contract_id, "contract_id")
        _text(contract_version, "contract_version")
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 0 <= limit <= 1000
            or offset < 0
        ):
            raise RegistryError(
                "Candidate listing requires limit 0..1000 and nonnegative offset"
            )
        try:
            specifier = SpecifierSet(version_spec or "")
        except (InvalidSpecifier, TypeError) as exc:
            raise RegistryError(f"Invalid candidate version specifier: {exc}") from exc
        clauses = "provides_id=? AND provides_version=?"
        arguments = [contract_id, contract_version]
        if family is not None:
            clauses += " AND family=?"
            arguments.append(family)
        with self._connect() as db:
            cards = [
                json.loads(row["card"])
                for row in db.execute(
                    f"SELECT card FROM members WHERE {clauses} ORDER BY family, member, version",
                    arguments,
                )
            ]
        try:
            cards = [
                card
                for card in cards
                if specifier.contains(Version(card["version"]), prereleases=True)
            ]
            cards.sort(key=lambda card: Version(card["version"]), reverse=True)
        except InvalidVersion as exc:
            raise RegistryError(f"Stored candidate has invalid version: {exc}") from exc
        return cards[offset : offset + limit]

    def lock(
        self,
        family: str,
        member: str,
        version: str | None = None,
        allowed_effects: list[str] | set[str] | None = None,
    ) -> dict[str, Any]:
        card = self.inspect(family, member, version)
        if not _effects_allowed(card, allowed_effects):
            raise RegistryError(
                "Member effects are unknown or outside the declared policy"
            )
        with self._connect() as db:
            row = db.execute(
                "SELECT p.record FROM publications p JOIN members m ON m.snapshot=p.digest WHERE m.family=? AND m.member=? AND m.version=?",
                (family, member, card["version"]),
            ).fetchone()
        if row is None:
            raise RegistryError("Member has no matching family release")
        index = _validate_index(json.loads(row["record"]))
        artifacts = {_name(item["distribution"]): item for item in index["artifacts"]}
        selected: dict[str, dict[str, Any]] = {}
        pending = [_name(card["distribution"])]
        while pending:
            name = pending.pop()
            if name in selected:
                continue
            if name not in artifacts:
                raise RegistryError(f"Missing dependency in release: {name}")
            artifact = artifacts[name]
            selected[name] = artifact
            pending.extend(_name(dep) for dep in artifact["dependencies"])
        external: set[str] = set()
        for artifact in selected.values():
            for requirement in artifact["requires_dist"]:
                match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
                if match is None:
                    raise RegistryError(
                        f"Invalid dependency requirement: {requirement}"
                    )
                if _name(match.group(1)) not in artifacts:
                    external.add(requirement)
        result: dict[str, Any] = {
            "schema_version": 1,
            "format": "module-families-lock",
            "family": family,
            "version": card["version"],
            "family_version": card["family_version"],
            "member": card,
            "artifacts": [selected[name] for name in sorted(selected)],
            "external_requirements": sorted(external),
            "environment": {
                "locked": False,
                "reason": "Third-party Python dependencies and interpreter are not resolved by this module lock.",
            },
        }
        if allowed_effects is not None:
            result["allowed_effects"] = sorted(set(allowed_effects))
        result["sha256"] = _digest(result)
        return result

    def _verify_lock(self, lock: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(lock, dict):
            raise RegistryError("Lock must be an object")
        payload = {key: value for key, value in lock.items() if key != "sha256"}
        if _digest(payload) != lock.get("sha256"):
            raise RegistryError("Lock hash mismatch")
        try:
            expected = self.lock(
                lock["family"],
                lock["member"]["id"],
                lock["version"],
                lock.get("allowed_effects"),
            )
        except (KeyError, TypeError) as exc:
            raise RegistryError("Malformed module lock") from exc
        if canonical_bytes(expected) != canonical_bytes(lock):
            raise RegistryError(
                "Lock does not match stored member identity or complete dependency closure"
            )
        with self._connect() as db:
            for artifact in expected["artifacts"]:
                row = db.execute(
                    "SELECT record FROM artifacts WHERE distribution=? AND version=?",
                    (_name(artifact["distribution"]), artifact["version"]),
                ).fetchone()
                if row is None or json.loads(row["record"]) != artifact:
                    raise RegistryError(
                        "Locked artifact does not match registry metadata"
                    )
        return expected

    def materialize(self, lock: dict[str, Any], target: Path) -> dict[str, Any]:
        locked = self._verify_lock(lock)
        target = _root_path(target)
        _no_symlinks(target)
        planned: dict[str, bytes] = {}
        for artifact in locked["artifacts"]:
            files = _wheel_files(self._blob(artifact["sha256"]), artifact)
            for name, content in files.items():
                if name in planned:
                    raise RegistryError(
                        f"Multiple distributions own the same path: {name}"
                    )
                planned[name] = content
        # Complete preflight before creating target or writing any selected file.
        for name, content in planned.items():
            destination = target / name
            _no_symlinks(destination)
            for parent in destination.parents:
                if parent.exists() and not parent.is_dir():
                    raise RegistryError(
                        f"Installation parent is not a directory: {parent}"
                    )
            if destination.exists() and (
                not destination.is_file() or destination.read_bytes() != content
            ):
                raise RegistryError(
                    f"Installation would overwrite conflicting file: {name}"
                )
        for name, content in sorted(planned.items()):
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open("xb") as stream:
                    stream.write(content)
            except FileExistsError:
                _no_symlinks(destination)
                if not destination.is_file() or destination.read_bytes() != content:
                    raise RegistryError(
                        f"Concurrent installation conflict: {name}"
                    ) from None
        return {
            "target": str(target),
            "files": len(planned),
            "artifacts": len(locked["artifacts"]),
            "external_requirements": locked["external_requirements"],
            "environment_locked": False,
        }

    def export_simple(self, target: Path) -> dict[str, Any]:
        target = _root_path(target)
        _no_symlinks(target)
        with self._connect() as db:
            artifacts = [
                json.loads(row["record"])
                for row in db.execute(
                    "SELECT record FROM artifacts ORDER BY distribution, version"
                )
            ]
        files: dict[str, bytes] = {}
        projects: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            blob = self._blob(artifact["sha256"])
            wheel_files = _wheel_files(blob, artifact)
            name = _name(artifact["distribution"])
            projects.setdefault(name, []).append(artifact)
            archive_path = "files/" + artifact["filename"]
            content = blob.read_bytes()
            if archive_path in files and files[archive_path] != content:
                raise RegistryError("Duplicate wheel filename with different bytes")
            files[archive_path] = content
            metadata = next(
                value
                for path, value in wheel_files.items()
                if path.endswith(".dist-info/METADATA")
            )
            files[archive_path + ".metadata"] = metadata
        files["index.html"] = (
            "<!doctype html><html><body>\n"
            + "\n".join(
                f'<a href="{html.escape(name)}/">{html.escape(name)}</a>'
                for name in sorted(projects)
            )
            + "\n</body></html>\n"
        ).encode("utf-8")
        for project, members in projects.items():
            links = []
            for artifact in members:
                filename = artifact["filename"]
                metadata_hash = hashlib.sha256(
                    files[f"files/{filename}.metadata"]
                ).hexdigest()
                links.append(
                    f'<a href="../files/{html.escape(filename)}#sha256={artifact["sha256"]}" data-core-metadata="sha256={metadata_hash}">{html.escape(filename)}</a>'
                )
            files[f"{project}/index.html"] = (
                "<!doctype html><html><body>\n"
                + "\n".join(links)
                + "\n</body></html>\n"
            ).encode("utf-8")
        for name, content in files.items():
            destination = target / name
            _no_symlinks(destination)
            if destination.exists() and (
                not destination.is_file() or destination.read_bytes() != content
            ):
                raise RegistryError(
                    f"Simple index export conflicts with existing file: {name}"
                )
        for name, content in sorted(files.items()):
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open("xb") as stream:
                    stream.write(content)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise RegistryError(
                        f"Concurrent simple index export conflict: {name}"
                    ) from None
        return {
            "target": str(target),
            "projects": len(projects),
            "artifacts": len(artifacts),
            "index": str(target / "index.html"),
        }

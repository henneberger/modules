"""Authenticated publication and public discovery over a bounded HTTP protocol.

The server is a local/reverse-proxy service, not a TLS terminator or a sandbox.
Remote locks and object downloads retain the local registry's integrity checks.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import stat
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .registry import (
    Registry,
    RegistryError,
    _file_hash,
    _filename,
    _hash,
    _json_file,
    _no_symlinks,
    _publisher,
    _root_path,
    _strings,
    _validate_index,
    canonical_bytes,
)

MAX_PUBLICATION_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000


def _bundle_index(content: bytes) -> tuple[dict, dict[str, bytes]]:
    files = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise RegistryError("Publication has too many archive entries")
        if sum(entry.file_size for entry in entries) > MAX_PUBLICATION_BYTES:
            raise RegistryError("Expanded publication exceeds size limit")
        for entry in entries:
            if entry.filename != "index.json":
                _filename(entry.filename)
            if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (
                0,
                stat.S_IFREG,
            ):
                raise RegistryError("Publication contains a symlink or special file")
            if entry.filename in files:
                raise RegistryError("Publication contains duplicate archive names")
            if entry.filename == "index.json" and entry.file_size > MAX_JSON_BYTES:
                raise RegistryError("Publication JSON exceeds size limit")
            files[entry.filename] = archive.read(entry)
    if "index.json" not in files:
        raise RegistryError("Publication bundle requires index.json")
    index = _validate_index(json.loads(files.pop("index.json")))
    if set(files) != {item["filename"] for item in index["artifacts"]}:
        raise RegistryError(
            "Publication bundle must contain exactly its indexed wheels"
        )
    # Wheels are themselves ZIPs: bound their combined expanded size too.
    expanded = 0
    for content in files.values():
        with zipfile.ZipFile(io.BytesIO(content)) as wheel:
            entries = wheel.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise RegistryError("Wheel has too many archive entries")
            expanded += sum(entry.file_size for entry in entries)
            if expanded > MAX_PUBLICATION_BYTES:
                raise RegistryError("Expanded wheels exceed publication size limit")
    return index, files


def make_server(
    registry: Registry,
    host: str = "127.0.0.1",
    port: int = 0,
    tokens: dict[str, dict] | None = None,
) -> ThreadingHTTPServer:
    """Create a server; call ``serve_forever`` and ``shutdown`` as usual.

    Principal tokens use ``{"publisher": "alice"}``, optionally with a
    ``families`` allowlist. They may contribute their qualified members to any
    family by default. With no tokens, writes are disabled.
    """
    scopes = {}
    for token, policy in (tokens or {}).items():
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise RegistryError("Repository tokens must be nonempty without whitespace")
        if isinstance(policy, dict):
            if set(policy) - {"publisher", "families"}:
                raise RegistryError("Unknown publisher token policy fields")
            publisher = _publisher(policy.get("publisher"))
            families = policy.get("families")
            scopes[token] = {
                "publisher": publisher,
                "families": None
                if families is None
                else set(_strings(families, "token family scopes")),
            }
        else:
            raise RegistryError("Each token policy requires a publisher principal")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ModuleFamilies/1"

        def log_message(self, format, *args):
            # Applications can add access logging without printing credentials.
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def send_json(self, value, status=200):
            content = canonical_bytes(value)
            if len(content) > MAX_JSON_BYTES:
                raise RegistryError("Repository JSON response exceeds size limit")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def fail(self, error, status=400):
            try:
                self.send_json({"error": str(error)}, status)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def do_GET(self):
            try:
                if len(self.path) > 16_384:
                    raise RegistryError("Request URL exceeds size limit")
                url = urlsplit(self.path)
                if url.path.startswith("/v1/objects/"):
                    digest = _hash(url.path.removeprefix("/v1/objects/"))
                    path = registry._blob(digest)
                    if _file_hash(path) != digest:
                        raise RegistryError("Stored object hash mismatch")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("ETag", '"sha256:' + digest + '"')
                    self.end_headers()
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            self.wfile.write(chunk)
                    return
                routes = {
                    "/v1/search": registry.search,
                    "/v1/inspect": registry.inspect,
                    "/v1/lock": registry.lock,
                    "/v1/families": registry.families,
                    "/v1/versions": registry.versions,
                    "/v1/interface": registry.interface,
                    "/v1/interfaces": registry.interfaces,
                    "/v1/candidates": registry.candidates,
                    "/v1/revision": registry.revision,
                }
                if url.path not in routes:
                    self.fail("Unknown repository endpoint", 404)
                    return
                parsed = parse_qs(url.query, keep_blank_values=True, max_num_fields=100)
                if any(len(values) != 1 for values in parsed.values()):
                    raise RegistryError("Duplicate query arguments are not supported")
                arguments = {key: values[0] for key, values in parsed.items()}
                for key in ("limit", "offset"):
                    if key in arguments:
                        arguments[key] = int(arguments[key])
                if "allowed_effects" in arguments:
                    arguments["allowed_effects"] = _strings(
                        json.loads(arguments["allowed_effects"]), "allowed_effects"
                    )
                if url.path == "/v1/search":
                    arguments.setdefault("query", "")
                self.send_json(routes[url.path](**arguments))
            except (RegistryError, OSError, ValueError, TypeError, KeyError) as error:
                self.fail(error)

        def do_POST(self):
            if self.path != "/v1/publish":
                self.fail("Unknown repository endpoint", 404)
                return
            authorization = self.headers.get("Authorization", "")
            secret = (
                authorization.removeprefix("Bearer ")
                if authorization.startswith("Bearer ")
                else ""
            )
            permitted = next(
                (
                    policy
                    for token, policy in scopes.items()
                    if hmac.compare_digest(secret.encode(), token.encode())
                ),
                None,
            )
            if permitted is None:
                self.fail("Publication requires an authorized bearer token", 401)
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise RegistryError("Chunked uploads are not supported")
                if self.headers.get_content_type() != "application/zip":
                    raise RegistryError(
                        "Publication content type must be application/zip"
                    )
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1:
                    raise RegistryError("Publication requires one Content-Length")
                length = int(lengths[0])
                if not 0 < length <= MAX_PUBLICATION_BYTES:
                    self.fail("Publication exceeds request size limit", 413)
                    return
                content = self.rfile.read(length)
                if len(content) != length:
                    raise RegistryError("Truncated publication upload")
                index, files = _bundle_index(content)
                if index.get("publisher") != permitted["publisher"]:
                    self.fail("Token cannot impersonate another publisher", 403)
                    return
                families = permitted["families"]
                if (
                    families is not None
                    and "*" not in families
                    and index["family"]["name"] not in families
                ):
                    self.fail("Token is not authorized for this family", 403)
                    return
                with tempfile.TemporaryDirectory(
                    prefix=".upload-", dir=registry.root
                ) as directory:
                    staging = Path(directory)
                    (staging / "index.json").write_bytes(canonical_bytes(index))
                    for filename, data in files.items():
                        (staging / filename).write_bytes(data)
                    self.send_json(registry.publish(staging / "index.json"), 201)
            except (
                RegistryError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                zipfile.BadZipFile,
                RuntimeError,
            ) as error:
                self.fail(error)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never forward a bearer token to another origin.
        return None


class RemoteRegistry:
    """Registry-compatible reads, publication, and verified closure downloads."""

    def __init__(
        self, location: str, *, token: str | None = None, cache: Path | None = None
    ):
        url = urlsplit(location)
        if (
            url.scheme not in ("http", "https")
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise RegistryError(
                "Repository must be an HTTP(S) base URL without credentials, query, or fragment"
            )
        self.location = location.rstrip("/")
        if token is not None and (
            not isinstance(token, str) or not token or any(c.isspace() for c in token)
        ):
            raise RegistryError("Repository token must be nonempty without whitespace")
        self.token = token
        if cache is None:
            cache = (
                Path(tempfile.gettempdir())
                / "module-families-http-cache"
                / hashlib.sha256(self.location.encode()).hexdigest()
            )
        self.root = _root_path(Path(cache))
        _no_symlinks(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._opener = build_opener(_NoRedirect())

    def _request(self, route, arguments=None, *, body=None, binary=False):
        query = {}
        for key, value in (arguments or {}).items():
            if value is not None:
                query[key] = (
                    canonical_bytes(sorted(value)).decode()
                    if key == "allowed_effects"
                    else value
                )
        url = self.location + route + (("?" + urlencode(query)) if query else "")
        headers = {
            "Accept": "application/octet-stream" if binary else "application/json"
        }
        if body is not None:
            headers["Content-Type"] = "application/zip"
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
        request = Request(url, data=body, headers=headers)
        limit = MAX_PUBLICATION_BYTES if binary else MAX_JSON_BYTES
        try:
            with self._opener.open(request, timeout=30) as response:
                if int(response.headers.get("Content-Length", "0")) > limit:
                    raise RegistryError("Repository response exceeds size limit")
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise RegistryError("Repository response exceeds size limit")
        except HTTPError as error:
            content = error.read(MAX_JSON_BYTES + 1)
            try:
                message = json.loads(content).get("error", error.reason)
            except (ValueError, AttributeError):
                message = error.reason
            raise RegistryError(f"Repository HTTP {error.code}: {message}") from error
        except (URLError, OSError) as error:
            raise RegistryError(f"Repository request failed: {error}") from error
        if binary:
            return content
        try:
            return json.loads(content)
        except (ValueError, UnicodeDecodeError) as error:
            raise RegistryError("Repository returned invalid JSON") from error

    def revision(self):
        record = self._request("/v1/revision")
        if (
            not isinstance(record, dict)
            or set(record) != {"sequence", "publication_sha256"}
            or type(record["sequence"]) is not int
            or record["sequence"] < 0
        ):
            raise RegistryError("invalid repository publication revision")
        digest = record["publication_sha256"]
        if record["sequence"] == 0:
            if digest is not None:
                raise RegistryError("empty repository revision must have no publication")
        else:
            _hash(digest)
        return record

    def publish(self, index_path: Path) -> dict:
        index_path = _root_path(Path(index_path))
        index = _validate_index(_json_file(index_path))
        encoded = canonical_bytes(index)
        if len(encoded) > MAX_JSON_BYTES:
            raise RegistryError("Publication JSON exceeds size limit")
        buffer = io.BytesIO()
        total = len(encoded)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("index.json", encoded)
            for artifact in index["artifacts"]:
                path = index_path.parent / artifact["filename"]
                _no_symlinks(path)
                total += path.stat().st_size
                if total > MAX_PUBLICATION_BYTES:
                    raise RegistryError("Publication exceeds size limit")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                    raise RegistryError("Publication artifact hash mismatch")
                archive.writestr(artifact["filename"], content)
        content = buffer.getvalue()
        if len(content) > MAX_PUBLICATION_BYTES:
            raise RegistryError("Publication exceeds size limit")
        return self._request("/v1/publish", body=content)

    def search(
        self,
        query: str,
        *,
        family=None,
        contract=None,
        allowed_effects=None,
        limit=10,
        offset=0,
    ):
        return self._request("/v1/search", locals() | {"self": None})

    def inspect(self, family, member, version=None):
        return self._request(
            "/v1/inspect", {"family": family, "member": member, "version": version}
        )

    def lock(self, family, member, version=None, allowed_effects=None):
        return self._request(
            "/v1/lock",
            {
                "family": family,
                "member": member,
                "version": version,
                "allowed_effects": allowed_effects,
            },
        )

    def families(self, *, limit=100, offset=0):
        return self._request("/v1/families", {"limit": limit, "offset": offset})

    def versions(self, family, member):
        return self._request("/v1/versions", {"family": family, "member": member})

    def interface(self, id, version):
        return self._request("/v1/interface", {"id": id, "version": version})

    def interfaces(self, *, limit=100, offset=0):
        return self._request("/v1/interfaces", {"limit": limit, "offset": offset})

    def candidates(
        self,
        contract_id,
        contract_version,
        *,
        family=None,
        version_spec=None,
        limit=100,
        offset=0,
    ):
        return self._request("/v1/candidates", locals() | {"self": None})

    def _verify_lock(self, lock: dict) -> dict:
        if (
            not isinstance(lock, dict)
            or lock.get("format") != "module-families-lock"
            or lock.get("schema_version") != 1
        ):
            raise RegistryError("Malformed module lock")
        payload = {key: value for key, value in lock.items() if key != "sha256"}
        if hashlib.sha256(canonical_bytes(payload)).hexdigest() != lock.get("sha256"):
            raise RegistryError("Lock hash mismatch")
        try:
            _validate_index(
                {
                    "schema_version": 1,
                    "publisher": lock["member"]["publisher"],
                    "family": {
                        "name": lock["family"],
                        "version": lock["family_version"],
                    },
                    "members": [lock["member"]],
                    "artifacts": lock["artifacts"],
                }
            )
            expected = self.lock(
                lock["family"],
                lock["member"]["id"],
                lock["version"],
                lock.get("allowed_effects"),
            )
        except (TypeError, KeyError) as error:
            raise RegistryError("Malformed module lock") from error
        if canonical_bytes(expected) != canonical_bytes(lock):
            raise RegistryError(
                "Lock does not match repository member identity or complete dependency closure"
            )
        return expected

    def _blob(self, digest: str) -> Path:
        digest = _hash(digest)
        destination = self.root / "objects" / digest[:2] / f"{digest}.whl"
        _no_symlinks(destination)
        if destination.exists():
            if _file_hash(destination) != digest:
                raise RegistryError("Cached object hash mismatch")
            return destination
        content = self._request("/v1/objects/" + digest, binary=True)
        if hashlib.sha256(content).hexdigest() != digest:
            raise RegistryError("Downloaded object hash mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".download-", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return destination

    def materialize(self, lock: dict, target: Path) -> dict:
        return Registry.materialize(self, lock, target)


def open_repository(location, token=None, cache=None) -> Registry | RemoteRegistry:
    """Open a filesystem repository or an HTTP(S) repository with the same API."""
    if str(location).startswith(("http://", "https://")):
        return RemoteRegistry(str(location), token=token, cache=cache)
    if str(location).endswith(".federation.toml"):
        from .federation import open_federation

        return open_federation(location)
    return Registry(Path(location))

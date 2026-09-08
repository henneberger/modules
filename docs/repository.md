# A runnable repository for open module families

The repository service supports authenticated publication, public discovery, immutable interface lookup, exact member locks, and verified artifact downloads. Family context and publisher identity are separate: independently authenticated publishers can contribute their own qualified members to the same family. Existing family headers remain immutable, so contributors use the same name, context version, description, and context metadata.

```python
import os
from pathlib import Path
from module_families.registry import Registry
from module_families.repository import make_server

server = make_server(
    Registry(Path(".mf/final-repository")),
    host="127.0.0.1",
    port=8765,
    tokens={
        os.environ["ALICE_TOKEN"]: {"publisher": "alice"},
        os.environ["BOB_TOKEN"]: {"publisher": "bob"},
    },
)
try:
    server.serve_forever()
finally:
    server.server_close()
```

Each token identifies one publisher. An optional `families` list restricts where that principal can contribute; omission permits any family. There is no family-owner approval requirement and no administrator impersonation mode. With no configured tokens, publication is disabled. Every publication requires its publisher string and matching member `publisher`, `local_id`, and qualified `id` fields. The interface and distribution ownership rules in [registry.md](registry.md) prevent another principal from replacing their versions while allowing exact artifact reuse.

Clients use a URL wherever they would use a local repository path:

```python
from module_families.repository import open_repository

publisher = open_repository(
    "http://127.0.0.1:8765", token=os.environ["ALICE_TOKEN"],
    cache=Path(".mf/alice-cache"),
)
publisher.publish(Path("dist/alice-solve/index.json"))

reader = open_repository("http://127.0.0.1:8765", cache=Path(".mf/reader-cache"))
providers = reader.candidates("linear.solve", "1", version_spec=">=0.1")
selected = providers[0]  # Application or agent policy chooses explicitly.
lock = reader.lock(selected["family"], selected["id"], selected["version"])
reader.materialize(lock, Path(".mf/site"))
```

`open_repository()` returns `Registry` for a filesystem path and `RemoteRegistry` for an HTTP(S) URL. Remote clients implement `publish`, `search`, `inspect`, `lock`, `families`, `versions`, `interface`, `interfaces`, `candidates`, and `materialize`, and work with `atomic_import`. `export_simple` is a local administrative operation. Discovery and locking download metadata only. Materialization fetches only the locked transitive closure, checks each downloaded SHA-256, validates wheel metadata and RECORD entries, and reuses verified cached blobs. A forged lock is compared against the repository's complete authoritative lock before writing selected files. Remote verification currently requires a reachable repository even when all blobs are cached.

The versioned protocol uses public GET endpoints `/v1/search`, `/v1/inspect`, `/v1/lock`, `/v1/families`, `/v1/versions`, `/v1/interface`, `/v1/interfaces`, and `/v1/candidates`; query arguments match the Python method arguments. `allowed_effects` is a JSON array in its query value. `/v1/objects/<sha256>` returns a verified wheel object. Authenticated `POST /v1/publish` accepts `application/zip` containing exactly `index.json` and the wheel basenames referenced by that index. It does not accept caller-selected server paths.

Uploads, expanded outer archives, and the combined expanded wheels are limited to 128 MiB. JSON documents are limited to 16 MiB. The service rejects traversal, symlinks, duplicate names, extra files, malformed wheels, hash mismatches, and conflicting immutable records. Publication stages validated basenames and then uses the registry's catalog transaction; concurrent requests cannot expose half-published membership. Artifact data can remain unreferenced after a failed transaction and is not automatically garbage-collected.

The stdlib server defaults to loopback HTTP. Use HTTPS termination and operator-managed token provisioning when exposing it beyond a trusted local connection; the client verifies HTTPS certificates and rejects redirects, so bearer credentials are not forwarded to another origin. This service has no built-in account enrollment, TLS termination, distributed replication, signed provenance, revocation protocol, or runtime sandbox. Module locks still report unresolved external Python requirements; they do not lock the interpreter environment.

`tests/test_repository.py` runs real TCP publication and discovery, independent Alice/Bob contributions, publisher impersonation rejection, exact dependency reuse, interface-only releases, fresh-interpreter remote atomic import, lock and download tampering, and archive boundary checks.

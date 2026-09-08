from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from module_families.registry import Registry, RegistryError, canonical_bytes
from module_families.repository import RemoteRegistry, make_server, open_repository
from module_families.wheels import build_wheel


@contextmanager
def serving(registry, *, tokens=None):
    server = make_server(registry, tokens=tokens)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def service(tmp_path):
    registry = Registry(tmp_path / "repository")
    with serving(
        registry,
        tokens={
            "publisher": {"publisher": "alice", "families": ["example"]},
            "other": {"publisher": "alice", "families": ["other"]},
            "alice-token": {"publisher": "alice"},
            "bob-token": {"publisher": "bob"},
        },
    ) as url:
        yield registry, url


@pytest.fixture
def publication(tmp_path):
    out = tmp_path / "build"
    source = "def run(value):\n    return value * 2\n"
    cell_id = hashlib.sha256(source.encode()).hexdigest()
    cell_module = f"mf_cells.c_{cell_id}"
    cell = build_wheel(
        out,
        distribution=f"mf-cell-{cell_id}",
        version="0.1.0",
        files={cell_module.replace(".", "/") + ".py": source},
    )
    cell["dependencies"] = []
    facade_source = f"from {cell_module} import run\nvalue = run\n"
    facade_id = hashlib.sha256(facade_source.encode()).hexdigest()
    facade_module = f"mf_members.m_{facade_id}"
    facade = build_wheel(
        out,
        distribution="mf-example-run",
        version="0.1.0",
        files={facade_module.replace(".", "/") + ".py": facade_source},
        requires_dist=[f"{cell['distribution']}==0.1.0"],
    )
    facade["dependencies"] = [cell["distribution"]]
    unrelated = build_wheel(
        out,
        distribution="mf-example-unrelated",
        version="0.1.0",
        files={"never_loaded.py": "raise RuntimeError('unrelated code imported')\n"},
    )
    unrelated["dependencies"] = []
    spec = {
        "id": "example.Compute",
        "version": "1",
        "callables": {
            "run": {
                "asynchronous": False,
                "parameters": [
                    {"name": "value", "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                ],
            }
        },
        "types": [],
    }
    index = {
        "schema_version": 1,
        "publisher": "alice",
        "family": {
            "name": "example",
            "version": "0.1.0",
            "description": "Remote algorithms",
            "context": {},
        },
        "members": [
            {
                "id": "alice.run",
                "local_id": "run",
                "publisher": "alice",
                "summary": "Double a number",
                "kind": "operation",
                "effects": [],
                "provides": {"id": spec["id"], "version": spec["version"]},
                "distribution": facade["distribution"],
                "wheel": facade["filename"],
                "sha256": facade["sha256"],
                "import_module": facade_module,
                "export": "run",
            }
        ],
        "artifacts": [cell, facade, unrelated],
        "interfaces": [spec],
    }
    path = out / "index.json"
    path.write_bytes(canonical_bytes(index))
    return path, index


def test_http_publish_public_discovery_and_selected_closure_download(
    service, publication, tmp_path
):
    registry, url = service
    path, index = publication
    publisher = open_repository(
        url, token="publisher", cache=tmp_path / "publisher-cache"
    )
    result = publisher.publish(path)
    assert result["members"] == result["interfaces"] == 1
    reader = open_repository(url, cache=tmp_path / "reader-cache")
    assert reader.search("double")[0]["id"] == "alice.run"
    assert reader.search("double", allowed_effects=[])[0]["id"] == "alice.run"
    assert reader.inspect("example", "alice.run")["version"] == "0.1.0"
    assert reader.families() == registry.families()
    assert reader.versions("example", "alice.run") == registry.versions(
        "example", "alice.run"
    )
    assert reader.interface("example.Compute", "1") == index["interfaces"][0]
    assert reader.interfaces() == registry.interfaces()
    assert reader.candidates("example.Compute", "1") == registry.candidates(
        "example.Compute", "1"
    )
    lock = reader.lock("example", "alice.run", allowed_effects=[])
    assert not list(reader.root.rglob("*.whl"))
    result = reader.materialize(lock, tmp_path / "site")
    assert result["artifacts"] == 2
    assert len(list(reader.root.rglob("*.whl"))) == 2
    assert not (tmp_path / "site/never_loaded.py").exists()
    assert publisher.publish(path)["already_published"] is True


@pytest.mark.parametrize(
    "token,status", [(None, "401"), ("wrong", "401"), ("other", "403")]
)
def test_publication_requires_token_authorized_for_family(
    service, publication, tmp_path, token, status
):
    registry, url = service
    client = RemoteRegistry(url, token=token, cache=tmp_path / "cache")
    with pytest.raises(RegistryError, match=status):
        client.publish(publication[0])
    assert registry.families() == []
    assert registry.interfaces() == []
    assert not list(registry.root.glob(".upload-*"))


def test_server_without_configured_tokens_is_read_only(tmp_path, publication):
    registry = Registry(tmp_path / "repository")
    with serving(registry) as url:
        client = RemoteRegistry(url, token="anything", cache=tmp_path / "cache")
        assert client.families() == []
        with pytest.raises(RegistryError, match="401"):
            client.publish(publication[0])


def test_remote_lock_tampering_and_cached_object_corruption_are_rejected(
    service, publication, tmp_path
):
    registry, url = service
    client = RemoteRegistry(url, token="publisher", cache=tmp_path / "cache")
    client.publish(publication[0])
    lock = client.lock("example", "alice.run")
    changed = json.loads(json.dumps(lock))
    changed["member"]["summary"] = "Forged context"
    changed["sha256"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in changed.items() if k != "sha256"})
    ).hexdigest()
    with pytest.raises(RegistryError, match="identity"):
        client.materialize(changed, tmp_path / "forged")
    assert not (tmp_path / "forged").exists()
    missing = json.loads(json.dumps(lock))
    missing["artifacts"] = [
        item
        for item in missing["artifacts"]
        if item["distribution"] == "mf-example-run"
    ]
    missing["sha256"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in missing.items() if k != "sha256"})
    ).hexdigest()
    with pytest.raises(RegistryError, match="Missing internal dependency"):
        client.materialize(missing, tmp_path / "missing")
    object_path = client._blob(lock["artifacts"][0]["sha256"])
    object_path.write_bytes(b"corrupted cached bytes")
    with pytest.raises(RegistryError, match="Cached object hash mismatch"):
        client.materialize(lock, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()


@pytest.mark.parametrize("unsafe", ["../escape.whl", "/escape.whl", "folder/wheel.whl"])
def test_publication_bundle_rejects_unsafe_paths(
    service, publication, tmp_path, unsafe
):
    registry, url = service
    client = RemoteRegistry(url, token="publisher", cache=tmp_path / "cache")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.json", canonical_bytes(publication[1]))
        archive.writestr(unsafe, b"not a wheel")
    with pytest.raises(RegistryError, match="Unsafe wheel filename"):
        client._request("/v1/publish", body=buffer.getvalue())
    assert registry.families() == []
    assert not (tmp_path / "escape.whl").exists()


def test_bundle_rejects_symlinks_and_expansion_over_limit(
    service, publication, tmp_path, monkeypatch
):
    import module_families.repository as repository

    registry, url = service
    client = RemoteRegistry(url, token="publisher", cache=tmp_path / "cache")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.json", canonical_bytes(publication[1]))
        entry = zipfile.ZipInfo("linked.whl")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, b"/outside")
    with pytest.raises(RegistryError, match="symlink"):
        client._request("/v1/publish", body=buffer.getvalue())
    monkeypatch.setattr(repository, "MAX_PUBLICATION_BYTES", 8192)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.json", canonical_bytes(publication[1]))
        archive.writestr("oversized.whl", b"0" * 100_000)
    assert len(buffer.getvalue()) < 8192
    with pytest.raises(RegistryError, match="Expanded publication exceeds"):
        client._request("/v1/publish", body=buffer.getvalue())
    assert registry.families() == []


def test_remote_registry_works_with_atomic_import_in_fresh_interpreter(
    service, publication, tmp_path
):
    _, url = service
    RemoteRegistry(url, token="publisher", cache=tmp_path / "publish-cache").publish(
        publication[0]
    )
    source_root = Path(__file__).resolve().parents[1] / "src"
    program = f"""
import sys
sys.path.insert(0, {str(source_root)!r})
from pathlib import Path
from module_families.repository import RemoteRegistry
from module_families.runtime import atomic_import
repository = RemoteRegistry({url!r}, cache=Path({str(tmp_path / "runtime-cache")!r}))
lock = repository.lock('example', 'alice.run')
bundle = atomic_import(repository, {{'double': lock}}, Path({str(tmp_path / "runtime")!r}))
assert bundle['double'](21) == 42
print('remote import verified')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "remote import verified"


def test_open_repository_retains_local_api_and_rejects_credential_urls(tmp_path):
    assert isinstance(open_repository(tmp_path / "local"), Registry)
    with pytest.raises(RegistryError, match="without credentials"):
        RemoteRegistry("http://user:secret@localhost:1234", cache=tmp_path / "cache")


def contribution(publication, publisher, *, version="0.1.0"):
    original_path, original = publication
    index = json.loads(json.dumps(original))
    out = original_path.parent / publisher / version
    out.mkdir(parents=True)
    cell, old_facade = original["artifacts"][:2]
    shutil.copy2(original_path.parent / cell["filename"], out / cell["filename"])
    old_module = original["members"][0]["import_module"].replace(".", "/") + ".py"
    with zipfile.ZipFile(original_path.parent / old_facade["filename"]) as archive:
        source = f"# Publisher: {publisher}\n" + archive.read(old_module).decode()
    module = "mf_members.m_" + hashlib.sha256(source.encode()).hexdigest()
    facade = build_wheel(
        out,
        distribution=f"mf-example-{publisher}-run",
        version=version,
        files={module.replace(".", "/") + ".py": source},
        requires_dist=[f"{cell['distribution']}=={cell['version']}"],
    )
    facade["dependencies"] = [cell["distribution"]]
    index["publisher"] = publisher
    index["members"] = [
        {
            **original["members"][0],
            "publisher": publisher,
            "id": publisher + ".run",
            "local_id": "run",
            "version": version,
            "distribution": facade["distribution"],
            "wheel": facade["filename"],
            "sha256": facade["sha256"],
            "import_module": module,
        }
    ]
    index["artifacts"] = [cell, facade]
    index.pop("interfaces")
    path = out / "index.json"
    path.write_bytes(canonical_bytes(index))
    return path, index


def test_independent_publishers_join_family_without_owner_permission(
    service, publication, tmp_path
):
    registry, url = service
    alice = RemoteRegistry(url, token="alice-token", cache=tmp_path / "alice-cache")
    bob = RemoteRegistry(url, token="bob-token", cache=tmp_path / "bob-cache")
    alice.publish(publication[0])
    bob_path, bob_index = contribution(publication, "bob")
    with pytest.raises(RegistryError, match="impersonate"):
        alice.publish(bob_path)
    bob.publish(bob_path)
    assert [card["id"] for card in alice.candidates("example.Compute", "1")] == [
        "alice.run",
        "bob.run",
    ]
    assert len(registry.families()) == 1
    assert len(list((registry.root / "objects").rglob("*.whl"))) == 4
    first = bob.lock("example", "bob.run")
    next_path, _ = contribution(publication, "bob", version="0.2.0")
    bob.publish(next_path)
    assert bob.lock("example", "bob.run", "0.1.0") == first
    assert [
        card["version"]
        for card in bob.candidates("example.Compute", "1", version_spec=">=0.2")
    ] == ["0.2.0"]
    bob_index["interfaces"] = publication[1]["interfaces"]
    bob_path.write_bytes(canonical_bytes(bob_index))
    with pytest.raises(RegistryError, match="owned by another publisher"):
        bob.publish(bob_path)
    bob_index.pop("interfaces")
    bob_index["family"]["description"] = "Bob cannot rewrite the shared context"
    bob_path.write_bytes(canonical_bytes(bob_index))
    with pytest.raises(RegistryError, match="Family header"):
        bob.publish(bob_path)


def test_interface_only_release_over_http_and_no_unscoped_token_mode(service, tmp_path):
    _, url = service
    index = {
        "schema_version": 1,
        "publisher": "alice",
        "family": {
            "name": "types",
            "version": "1.0",
            "description": "Shared type contracts",
            "context": {},
        },
        "interfaces": [{"id": "shared.Item", "version": "1", "types": ["Item"]}],
    }
    path = tmp_path / "interface-index.json"
    path.write_bytes(canonical_bytes(index))
    client = RemoteRegistry(url, token="alice-token", cache=tmp_path / "cache")
    result = client.publish(path)
    assert result["artifacts"] == result["members"] == 0
    assert client.interface("shared.Item", "1")["types"] == ["Item"]
    assert not list(client.root.rglob("*.whl"))
    with pytest.raises(RegistryError, match="publisher principal"):
        make_server(Registry(tmp_path / "other-repository"), tokens={"admin": ["*"]})


def test_client_verifies_downloaded_bytes_before_caching(
    service, publication, tmp_path
):
    _, url = service
    RemoteRegistry(url, token="alice-token", cache=tmp_path / "publish-cache").publish(
        publication[0]
    )

    class CorruptingTransport(RemoteRegistry):
        def _request(self, *args, **kwargs):
            result = super()._request(*args, **kwargs)
            return result + b"corruption" if kwargs.get("binary") else result

    client = CorruptingTransport(url, cache=tmp_path / "corrupt-cache")
    lock = client.lock("example", "alice.run")
    with pytest.raises(RegistryError, match="Downloaded object hash mismatch"):
        client.materialize(lock, tmp_path / "site")
    assert not list(client.root.rglob("*.whl"))
    assert not (tmp_path / "site").exists()

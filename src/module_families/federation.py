"""Bounded read federation over independently administered immutable repositories.

No global index is created. Exact identity conflicts fail closed; shard failure
is not mistaken for absence. This adapter does not provide snapshot consensus.
"""

from __future__ import annotations

import hashlib
import heapq
import tomllib
from functools import total_ordering
from pathlib import Path

from packaging.version import Version

from .interfaces import validate_interface
from .registry import Registry, RegistryError, _file_hash, canonical_bytes


class FederationError(RegistryError):
    pass


@total_ordering
class _Newest:
    def __init__(self, version):
        self.version = Version(version)

    def __eq__(self, other):
        return self.version == other.version

    def __lt__(self, other):
        return self.version > other.version


def _member_key(card):
    return card["family"], card["id"], card["version"]


def _missing(error, prefix):
    # Existing local/HTTP repositories use the same exact missing-object text.
    # Never treat arbitrary network, authorization or validation errors as absence.
    text = str(error)
    if text.startswith("Repository HTTP 400: "):
        text = text.removeprefix("Repository HTTP 400: ")
    return text.startswith(prefix)


class FederatedRegistry:
    def __init__(self, shards, *, page_size=100, max_scan=100000):
        if not isinstance(shards, dict) or not 1 <= len(shards) <= 32:
            raise FederationError("federation requires 1 to 32 named shards")
        if any(not isinstance(name, str) or not name.strip() for name in shards):
            raise FederationError("shard names must be nonempty strings")
        if (
            type(page_size) is not int
            or not 1 <= page_size <= 1000
            or type(max_scan) is not int
            or not 1 <= max_scan <= 1000000
        ):
            raise FederationError("invalid federation page size or scan bound")
        if any(isinstance(shard, FederatedRegistry) for shard in shards.values()):
            raise FederationError(
                "nested federations are unsupported; list concrete shards"
            )
        self.shards = dict(sorted(shards.items()))
        self.page_size, self.max_scan = page_size, max_scan
        self._objects = {}
        self._artifacts = {}

    def revision(self):
        return {name: shard.revision() for name, shard in self.shards.items()}

    @staticmethod
    def _pagination(limit, offset):
        if (
            type(limit) is not int
            or not 0 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise FederationError(
                "listing requires limit0..1000 and nonnegative offset"
            )

    def _exact(self, operation, arguments, missing):
        matches = []
        for name, shard in self.shards.items():
            try:
                value = getattr(shard, operation)(*arguments)
            except RegistryError as error:
                if _missing(error, missing):
                    continue
                raise FederationError(f"shard {name}: {error}") from error
            if matches and canonical_bytes(value) != canonical_bytes(matches[0][1]):
                raise FederationError(
                    f"immutable {operation} collision between {matches[0][0]} and {name}: {arguments}"
                )
            matches.append((name, value))
        if not matches:
            raise FederationError(f"{missing}{arguments}")
        return matches

    def interface(self, id, version):
        return validate_interface(
            self._exact("interface", (id, version), "Unknown interface: ")[0][1]
        )

    def inspect(self, family, member, version=None):
        if version is None:
            versions = self.versions(family, member)
            if not versions:
                raise FederationError(f"Unknown member: {family}/{member}")
            version = versions[0]["version"]
        return self._exact("inspect", (family, member, version), "Unknown member: ")[0][
            1
        ]

    def versions(self, family, member):
        records = {}
        scanned = 0
        for shard in self.shards.values():
            for card in shard.versions(family, member):
                scanned += 1
                if scanned > self.max_scan:
                    raise FederationError("version discovery exceeds scan bound")
                key = _member_key(card)
                if key in records and canonical_bytes(records[key]) != canonical_bytes(
                    card
                ):
                    raise FederationError(f"immutable member collision: {key}")
                records[key] = card
        return sorted(
            records.values(),
            key=lambda card: (_Newest(card["version"]), _member_key(card)),
        )

    def _merge(self, operation, arguments, key, limit, offset):
        self._pagination(limit, offset)
        if not limit:
            return []
        scanned = 0

        def stream(shard):
            page_offset = 0
            previous = None
            while True:
                page = getattr(shard, operation)(
                    **arguments, limit=self.page_size, offset=page_offset
                )
                if not isinstance(page, list) or len(page) > self.page_size:
                    raise FederationError("shard violated bounded page protocol")
                for record in page:
                    order = key(record)
                    if previous is not None and order <= previous:
                        raise FederationError("shard violated discovery ordering")
                    previous = order
                    yield record
                if len(page) < self.page_size:
                    return
                page_offset += len(page)

        heap = []
        for name, shard in self.shards.items():
            iterator = stream(shard)
            first = next(iterator, None)
            if first is not None:
                heapq.heappush(heap, (key(first), name, first, iterator))
        result, unique = [], 0
        while heap:
            order = heap[0][0]
            group = []
            while heap and heap[0][0] == order:
                _, name, record, iterator = heapq.heappop(heap)
                scanned += 1
                if scanned > self.max_scan:
                    raise FederationError(
                        "discovery exceeds scan bound; narrow the query"
                    )
                if group and canonical_bytes(group[0]) != canonical_bytes(record):
                    raise FederationError(f"immutable {operation} collision: {order}")
                group.append(record)
                following = next(iterator, None)
                if following is not None:
                    heapq.heappush(heap, (key(following), name, following, iterator))
            if unique >= offset:
                result.append(group[0])
                if len(result) == limit:
                    return result
            unique += 1
        return result

    def interfaces(self, *, limit=100, offset=0):
        return self._merge(
            "interfaces",
            {},
            lambda value: (value["id"], value["version"]),
            limit,
            offset,
        )

    def families(self, *, limit=100, offset=0):
        return self._merge(
            "families",
            {},
            lambda value: (value["name"], value["version"]),
            limit,
            offset,
        )

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
        # Contract identities are checked across all shards, even if only one
        # shard has matching providers on the requested page.
        self.interface(contract_id, contract_version)
        cards = self._merge(
            "candidates",
            {
                "contract_id": contract_id,
                "contract_version": contract_version,
                "family": family,
                "version_spec": version_spec,
            },
            lambda card: (_Newest(card["version"]), *_member_key(card)),
            limit,
            offset,
        )
        # An identity could be advertised with a different contract in another
        # shard, escaping the filtered query. Exact lookups detect that too.
        return [self.inspect(*_member_key(card)) for card in cards]

    def search(
        self,
        query,
        *,
        family=None,
        contract=None,
        allowed_effects=None,
        limit=10,
        offset=0,
    ):
        """Shard-name order, then each shard's ranking; no invented global BM25."""
        self._pagination(limit, offset)
        if not limit:
            return []
        result, scanned, unique = [], 0, 0
        for name, shard in self.shards.items():
            page_offset = 0
            while True:
                page = shard.search(
                    query,
                    family=family,
                    contract=contract,
                    allowed_effects=allowed_effects,
                    limit=self.page_size,
                    offset=page_offset,
                )
                if not isinstance(page, list) or len(page) > self.page_size:
                    raise FederationError("shard violated bounded page protocol")
                for card in page:
                    scanned += 1
                    if scanned > self.max_scan:
                        raise FederationError(
                            "search exceeds scan bound; narrow the query"
                        )
                    owners = self._exact(
                        "inspect", _member_key(card), "Unknown member: "
                    )
                    if owners[0][0] != name:
                        continue
                    if unique >= offset:
                        result.append(card)
                        if len(result) == limit:
                            return result
                    unique += 1
                if len(page) < self.page_size:
                    break
                page_offset += len(page)
        return result

    def lock(self, family, member, version=None, allowed_effects=None):
        card = self.inspect(family, member, version)
        for reference in [card.get("provides"), *card.get("requires", {}).values()]:
            if reference is not None:
                self.interface(reference["id"], reference["version"])
        owners = self._exact("inspect", _member_key(card), "Unknown member: ")
        expected = None
        for name, _ in owners:
            locked = self.shards[name].lock(
                family, member, card["version"], allowed_effects
            )
            if expected is not None and canonical_bytes(expected) != canonical_bytes(
                locked
            ):
                raise FederationError("immutable artifact closure collision")
            expected = locked
            for artifact in locked["artifacts"]:
                key = (artifact["distribution"], artifact["version"])
                if key in self._artifacts and canonical_bytes(
                    self._artifacts[key]
                ) != canonical_bytes(artifact):
                    raise FederationError(
                        f"immutable selected artifact collision: {key}"
                    )
                self._artifacts[key] = artifact
                self._objects.setdefault(artifact["sha256"], set()).add(name)
        return expected

    def _verify_lock(self, lock):
        if not isinstance(lock, dict):
            raise FederationError("lock must be an object")
        payload = {key: value for key, value in lock.items() if key != "sha256"}
        if hashlib.sha256(canonical_bytes(payload)).hexdigest() != lock.get("sha256"):
            raise FederationError("lock hash mismatch")
        try:
            expected = self.lock(
                lock["family"],
                lock["member"]["id"],
                lock["version"],
                lock.get("allowed_effects"),
            )
        except (KeyError, TypeError) as error:
            raise FederationError("malformed module lock") from error
        if canonical_bytes(expected) != canonical_bytes(lock):
            raise FederationError("lock does not match federated immutable content")
        for name, _ in self._exact(
            "inspect", _member_key(lock["member"]), "Unknown member: "
        ):
            self.shards[name]._verify_lock(lock)
        return expected

    def _blob(self, digest):
        if digest not in self._objects:
            raise FederationError("artifact must belong to a selected verified lock")
        name = sorted(self._objects[digest])[0]
        path = self.shards[name]._blob(digest)
        if _file_hash(path) != digest:
            raise FederationError(f"artifact hash mismatch in shard {name}")
        return path

    def materialize(self, lock, target):
        return Registry.materialize(self, lock, target)


def open_federation(path):
    """Read schema1 TOML containing named [shards.NAME] location tables."""
    from .repository import open_repository

    path = Path(path).resolve()
    doc = tomllib.loads(path.read_text())
    if (
        set(doc) - {"schema_version", "shards", "page_size", "max_scan"}
        or type(doc.get("schema_version")) is not int
        or doc["schema_version"] != 1
    ):
        raise FederationError("federation manifest requires schema_version1")
    locations = doc.get("shards")
    if not isinstance(locations, dict) or not 1 <= len(locations) <= 32:
        raise FederationError("federation requires 1 to 32 named shards")
    shards = {}
    for name, spec in locations.items():
        if (
            not isinstance(spec, dict)
            or set(spec) != {"location"}
            or not isinstance(spec["location"], str)
            or not spec["location"]
        ):
            raise FederationError("shard requires a location")
        location = spec["location"]
        if not location.startswith(("http://", "https://")):
            if location.endswith(".federation.toml"):
                raise FederationError(
                    "nested federations are unsupported; list concrete shards"
                )
            location = path.parent / location
        shards[name] = open_repository(location)
    return FederatedRegistry(
        shards,
        page_size=doc.get("page_size", 100),
        max_scan=doc.get("max_scan", 100000),
    )

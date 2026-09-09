"""Candidate pages bound decoded payloads and retain exact PEP440 ordering."""

import json
from functools import cmp_to_key

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from module_families.registry import Registry, RegistryError, _pep440_compare


def rows(repository, versions, *, count=None):
    count = len(versions) if count is None else count
    records = []
    for index in range(count):
        version = versions[index % len(versions)]
        family = "even" if index % 2 == 0 else "odd"
        member = f"publisher.member{index:08d}"
        records.append(
            (
                family,
                member,
                version,
                json.dumps({"family": family, "id": member, "version": version}),
                "snapshot",
                "call",
                "1",
            )
        )
    with repository._connect() as db:
        db.executemany(
            "INSERT INTO members(family,member,version,card,snapshot,provides_id,provides_version) VALUES(?,?,?,?,?,?,?)",
            records,
        )
    return [json.loads(record[3]) for record in records]


def expected(cards, version_spec="", family=None):
    specifier = SpecifierSet(version_spec)
    cards = sorted(
        [
            c
            for c in cards
            if specifier.contains(Version(c["version"]), prereleases=True)
            and (family is None or c["family"] == family)
        ],
        key=lambda c: (c["family"], c["id"], c["version"]),
    )
    cards.sort(key=lambda c: Version(c["version"]), reverse=True)
    return cards


@pytest.mark.parametrize(
    "version_spec", ["", ">=1.0rc1,<2", "==1.*", "!=1.0", "===1.0", "~=1.0", "==1.0.*"]
)
@pytest.mark.parametrize("family", [None, "even"])
def test_sql_pages_match_existing_pep440_semantics(tmp_path, version_spec, family):
    repository = Registry(tmp_path)
    versions = [
        "1.0",
        "1.0.0",
        "1.0rc1",
        "1.0.dev1",
        "1.0.post1",
        "1.0+local.1",
        "1!0.1",
        "2.0",
        "0.9",
        "1.0a1",
        "1.0b1",
    ]
    cards = rows(repository, versions, count=44)
    wanted = expected(cards, version_spec, family)
    actual = []
    for offset in range(0, len(wanted) + 3, 3):
        actual.extend(
            repository.candidates(
                "call",
                "1",
                version_spec=version_spec,
                family=family,
                limit=3,
                offset=offset,
            )
        )
    assert actual == wanted


def test_one_card_page_decodes_only_one_card_body(tmp_path, monkeypatch):
    import module_families.registry as registry_module

    repository = Registry(tmp_path)
    rows(repository, ["1.0"], count=10000)
    original = json.loads
    decoded = []

    def counted(value):
        decoded.append(value)
        return original(value)

    monkeypatch.setattr(registry_module.json, "loads", counted)
    assert len(repository.candidates("call", "1", limit=1, offset=9000)) == 1
    assert len(decoded) == 1


def test_zero_limit_validates_but_never_opens_database(tmp_path, monkeypatch):
    repository = Registry(tmp_path)
    monkeypatch.setattr(
        repository, "_connect", lambda: pytest.fail("zero-limit database access")
    )
    assert repository.candidates("call", "1", limit=0) == []
    with pytest.raises(RegistryError, match="version specifier"):
        repository.candidates("call", "1", limit=0, version_spec="not-a-version")
    with pytest.raises(RegistryError):
        repository.candidates("call", "1", limit=False)


@pytest.mark.parametrize("family", [None, "even"])
def test_candidate_query_uses_ordered_index_without_temp_sort(tmp_path, family):
    repository = Registry(tmp_path)
    condition = "provides_id=? AND provides_version=?"
    args = ["call", "1"]
    if family:
        condition += " AND family=?"
        args.append(family)
    with repository._connect() as db:
        plan = " ".join(
            row[3]
            for row in db.execute(
                f"EXPLAIN QUERY PLAN SELECT card FROM members WHERE {condition} ORDER BY version COLLATE PEP440 DESC,family,member,version LIMIT 1",
                args,
            )
        )
    assert "members_candidate_" in plan
    assert "TEMP B-TREE" not in plan


def test_pep440_collation_handles_semantically_equal_versions():
    values = ["1.0rc1", "1.0", "1.0.0", "1.0+abc", "1.0.post1", "1!0.0"]
    assert sorted(values, key=cmp_to_key(_pep440_compare)) == sorted(
        values, key=Version
    )
    assert _pep440_compare("1.0", "1.0.0") == 0

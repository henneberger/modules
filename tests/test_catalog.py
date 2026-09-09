import copy
from pathlib import Path

import pytest
from mari_fixture import generate_mari

from module_families.catalog import Catalog, ManifestError

ROOT = Path(__file__).resolve().parents[1]


def test_mari_manifest_coverage_and_decision_context():
    catalog = Catalog.load(ROOT / "families/mari/family.toml")
    assert len(catalog.document["members"]) == 988
    results = catalog.search("combine lexical dense ranked search", kind="algorithm")
    assert results[0]["id"] == "mari.retrieval.fusion.reciprocal_rank_fusion"
    assert (
        catalog.search("half life freshness", allowed_effects=[])[0]["id"]
        == "mari.algorithms.temporal.recency_decay"
    )
    assert not catalog.search("fastcdc", allowed_effects=[])
    assert catalog.inspect(results[0]["id"])["avoid_when"]


def test_search_does_not_import_source_and_documents_are_defensive_copies(
    tmp_path, monkeypatch
):
    document = Catalog.load(ROOT / "families/mari/family.toml").document
    evil = tmp_path / "mark_kit"
    evil.mkdir()
    (evil / "__init__.py").write_text("raise RuntimeError('discovery executed code')")
    monkeypatch.syspath_prepend(str(tmp_path))
    document["source"]["root"] = str(tmp_path)
    catalog = Catalog(document)
    document["members"].clear()
    first = catalog.search("recency")
    first[0]["tags"].clear()
    assert catalog.search("recency")[0]["tags"]
    assert catalog.inspect("mari.algorithms.temporal.recency_decay")["symbol"].endswith(
        ":recency_decay"
    )


def test_distribution_name_collisions_and_unknown_effects_rejected():
    document = Catalog.load(ROOT / "families/mari/family.toml").document
    member = copy.deepcopy(document["members"][0])
    member["local_id"] = member["local_id"].replace(".", "-").lower()
    member["id"] = "mari." + member["local_id"]
    document["members"].append(member)
    with pytest.raises(ManifestError, match="colliding"):
        Catalog(document)
    document["members"].pop()
    document["members"][0]["effects"] = ["unknown", "network"]
    with pytest.raises(ManifestError, match="unknown effects"):
        Catalog(document)


def test_regenerated_manifest_has_same_semantic_metadata(tmp_path):
    output = tmp_path / "family.toml"
    result = generate_mari(ROOT / "projects/mari-kit", output)
    assert result["members"] == 988
    actual = Catalog.load(output).document
    checked_in = Catalog.load(ROOT / "families/mari/family.toml").document
    assert actual["members"] == checked_in["members"]
    assert actual["interfaces"] == checked_in["interfaces"]
    assert result["explicit_annotations"] == 7
    assert len(output.read_text().splitlines()) < 60
    assert "[[members]]" not in output.read_text()

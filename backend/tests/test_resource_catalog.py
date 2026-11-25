from app import knowledge
from app.resource_catalog import build_catalog


def test_catalog_covers_every_source_resource_and_keeps_holdout_only_items_out():
    raw_ids = {
        item["id"]
        for group, records in knowledge.get_resources().items()
        if group != "meta" and isinstance(records, list)
        for item in records
        if isinstance(item, dict) and item.get("id")
    }
    catalog = build_catalog()
    items = {item["id"]: item for item in catalog["items"]}
    assert set(items) == raw_ids
    assert items["S1"]["recommendation_eligible"] is True
    assert items["S1"]["style_tags"]
    assert items["S1"]["scene_tags"] == ["正式场合"]
    assert items["F7"]["recommendation_eligible"] is False
    assert items["F7"]["style_tags"] == []
    assert items["F7"]["basis_cases"] == []
    assert "开发案例依据" in items["F7"]["missing_metadata"]


def test_catalog_evidence_never_cites_holdout_case():
    catalog = build_catalog()
    for item in catalog["items"]:
        assert "c5" not in item["basis_cases"]
        assert all("案例五" not in evidence for evidence in item["evidence"])


def test_catalog_api_shape(client):
    response = client.get("/api/resource-catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["total"] == len(payload["items"])
    assert payload["counts"]["recommendable"] < payload["counts"]["total"]
    assert all("usage_guidance" in item for item in payload["items"])

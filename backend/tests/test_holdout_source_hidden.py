"""留出案例（案例五）不能出现在任何对外的资源来源里：资源库接口、分类目录、来源文字。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import knowledge


def _all_sources(document: dict) -> list[str]:
    found: list[str] = []
    for key, items in document.items():
        if key == "meta" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                found.extend(item.get("sources") or [])
    return found


def test_raw_data_does_reference_holdout() -> None:
    """前提：原始资源数据里确实有引用案例五的来源，下面的过滤才有意义。"""
    assert "c5" in _all_sources(knowledge.get_resources())


def test_resources_endpoint_hides_holdout(client: TestClient) -> None:
    sources = _all_sources(client.get("/api/resources").json())
    assert sources, "过滤后仍应保留开发集来源"
    assert "c5" not in sources


def test_catalog_hides_holdout(client: TestClient) -> None:
    body = client.get("/api/resource-catalog").json()
    text = json.dumps(body, ensure_ascii=False)
    for item in body["items"]:
        assert "c5" not in (item.get("sources") or [])
        assert "c5" not in item["basis_cases"]
    assert "案例五" not in text


def test_sources_label_hides_holdout() -> None:
    assert knowledge.sources_label(["c1", "c5"]) == "案例一"
    assert knowledge.sources_label(["c5"]) == "未注明来源"
    assert knowledge.sources_label(None) == "未注明来源"

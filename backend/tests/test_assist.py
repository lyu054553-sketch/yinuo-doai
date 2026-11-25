"""相似旧单、面料备选、复述确认：程序规则判断，改方案留版本和台账，不冒充客户已确认。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.pipeline import assist


def _case2(client: TestClient) -> dict:
    response = client.post("/api/sessions", json={"sample_id": "c2"})
    assert response.status_code == 200
    return response.json()


def test_similar_cases_skip_self_and_holdout(client: TestClient) -> None:
    session = _case2(client)
    cases = client.get(f"/api/sessions/{session['id']}/similar-cases").json()
    assert cases and len(cases) <= 3
    ids = {c["case_id"] for c in cases}
    assert "c2" not in ids and "c5" not in ids
    for case in cases:
        assert len(case["fields"]) == 8
        for field in case["fields"]:
            assert field["verdict"] in ("和本单一样", "可直接用", "要按本单改", "不能照搬", "本单没提", "旧单没要求")


def test_reuse_field_creates_version_and_hands_to_designer(client: TestClient) -> None:
    session = _case2(client)
    cases = client.get(f"/api/sessions/{session['id']}/similar-cases").json()
    pick = next(
        (c, f) for c in cases for f in c["fields"]
        if f["verdict"] == "可直接用" and f["field"] == "版型结构"
    )
    case, field = pick
    response = client.post(
        f"/api/sessions/{session['id']}/reuse", json={"case_id": case["case_id"], "field": "版型结构"}
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    current = updated["spec_versions"][-1]
    assert current["version"] == session["spec_versions"][-1]["version"] + 1
    assert current["fields"]["版型结构"]["value"] == field["value"]
    assert current["fields"]["版型结构"]["confirmed"] is False
    assert updated["ledger"][-1]["action"] == "引用旧案"
    # 原来“整体宽松”对“微微收腰”的冲突交给设计师复核，而不是记成客户已确认
    loose = next(u for u in updated["utterances"] if "宽松" in u["text"])
    statuses = {m["status"] for m in loose["mappings"] if m["field"] == "版型结构"}
    assert "已与客户确认" not in statuses and "有冲突" not in statuses


def test_reuse_conflicting_field_is_refused(client: TestClient) -> None:
    session = _case2(client)
    for case in client.get(f"/api/sessions/{session['id']}/similar-cases").json():
        for field in case["fields"]:
            if field["verdict"] == "不能照搬":
                response = client.post(
                    f"/api/sessions/{session['id']}/reuse",
                    json={"case_id": case["case_id"], "field": field["field"]},
                )
                assert response.status_code == 400
                return


def test_reuse_holdout_case_is_refused(client: TestClient) -> None:
    session = _case2(client)
    response = client.post(f"/api/sessions/{session['id']}/reuse", json={"case_id": "c5", "field": "领型"})
    assert response.status_code == 400


def test_fabric_options_and_switch(client: TestClient) -> None:
    session = _case2(client)
    options = client.get(f"/api/sessions/{session['id']}/fabric-options").json()
    assert options[0]["current"] is True
    assert [r["label"] for r in options[0]["reasons"]] == ["风格适配", "体感适配", "体型适配"]
    other = next(o for o in options if not o["current"])
    response = client.post(f"/api/sessions/{session['id']}/fabric", json={"resource_id": other["id"]})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["spec_versions"][-1]["fields"]["面料"]["resource_ids"] == [other["id"]]
    assert updated["ledger"][-1]["action"] == "换面料"
    bad = client.post(f"/api/sessions/{session['id']}/fabric", json={"resource_id": "S1"})
    assert bad.status_code == 400


def test_restatement_template_and_answers(client: TestClient) -> None:
    session = _case2(client)
    response = client.post(f"/api/sessions/{session['id']}/restatement")
    assert response.status_code == 200, response.text
    restated = response.json()["restatement"]
    assert restated["text"] and 1 <= len(restated["points"]) <= 3
    texts = {u["id"]: u["text"] for u in session["utterances"]}
    for point in restated["points"]:
        assert texts[point["utterance_ids"][0]] in point["question"]

    first = restated["points"][0]["id"]
    ok = client.post(
        f"/api/sessions/{session['id']}/restatement/answer", json={"point_id": first, "answer": "对"}
    ).json()
    assert ok["restatement"]["points"][0]["answer"] == "对"
    missing_note = client.post(
        f"/api/sessions/{session['id']}/restatement/answer", json={"point_id": first, "answer": "不对"}
    )
    assert missing_note.status_code == 400
    wrong = client.post(
        f"/api/sessions/{session['id']}/restatement/answer",
        json={"point_id": first, "answer": "不对", "note": "要的是刺绣梅花"},
    ).json()
    assert any("刺绣梅花" in item["text"] for item in wrong["designer_items"])
    assert wrong["ledger"][-1]["action"] == "复述确认"


def test_labels_are_deduplicated() -> None:
    labels = assist._labels({"领子", "领子不能高", "R2", "宽松"})
    assert "领子" not in labels and "宽松" in labels

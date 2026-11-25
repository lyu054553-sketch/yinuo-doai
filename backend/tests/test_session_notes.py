def test_notes_are_persisted_per_session_and_validated(client, case2):
    session_id = case2["id"]
    url = f"/api/sessions/{session_id}/notes"
    assert client.get(url).json() == {}
    assert client.patch(url, json={"title": "风格调性", "value": "端庄，客户确认"}).status_code == 200
    assert client.patch(url, json={"title": "业务台账备注", "value": "待设计师确认"}).status_code == 200
    assert client.get(url).json() == {"风格调性": "端庄，客户确认", "业务台账备注": "待设计师确认"}
    assert client.patch(url, json={"title": "未知", "value": "x"}).status_code == 400
    assert client.get("/api/sessions/not-found/notes").status_code == 404
    stored = client.get(f"/api/sessions/{session_id}").json()
    assert stored["utterances"] == case2["utterances"]
    assert stored["spec_versions"] == case2["spec_versions"]
    assert stored["ledger"][:len(case2["ledger"])] == case2["ledger"]
    assert [row["summary"] for row in stored["ledger"][-2:]] == [
        "保存需求报告：风格调性",
        "保存业务台账备注",
    ]

import json

from app.llm import LLMClient
from app.pipeline.requirements import RequirementsAnalysis, TITLES
from app.schemas import Session
from app.store import save_session


def test_semantic_analysis_retries_invalid_reference_and_preserves_verbatim_ids(client, case2, monkeypatch):
    from app.config import settings
    from app.routers import sessions

    case2["source"] = "llm"
    save_session(Session.model_validate(case2))
    monkeypatch.setattr(settings, "mock_mode", False)
    first_id = case2["utterances"][0]["id"]
    categories = [{"title": title, "utterance_ids": []} for title in TITLES]
    invalid = [dict(row) for row in categories]
    invalid[0]["utterance_ids"] = ["invented-id"]
    valid = [dict(row) for row in categories]
    valid[0]["utterance_ids"] = [first_id]
    responses = iter([{"categories": invalid}, {"categories": valid}])
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append(payload)
        return json.dumps({"choices": [{"message": {"content": json.dumps(next(responses), ensure_ascii=False)}}]})

    fake = LLMClient("http://fake", "fake", "test-model", transport=transport)
    monkeypatch.setattr(sessions, "get_client", lambda: fake)
    url = f"/api/sessions/{case2['id']}/requirements-analysis"
    assert client.get(url).json() is None
    response = client.post(url)
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert response.json()["categories"][0]["utterance_ids"] == [first_id]
    assert client.get(url).json() == response.json()
    stored = client.get(f"/api/sessions/{case2['id']}").json()
    assert stored["utterances"] == case2["utterances"]
    assert stored["spec_versions"] == case2["spec_versions"]
    assert stored["ledger"][:-1] == case2["ledger"]
    assert stored["ledger"][-1]["summary"] == "模型完成需求语义归类"


def test_mock_session_does_not_call_model(client, case2):
    url = f"/api/sessions/{case2['id']}/requirements-analysis"
    assert client.post(url).status_code == 400


def test_human_review_persists_revision_and_ledger_entry(client, case2, monkeypatch):
    from app.config import settings
    from app.routers import sessions

    case2["source"] = "llm"
    save_session(Session.model_validate(case2))
    monkeypatch.setattr(settings, "mock_mode", False)
    first_id = case2["utterances"][0]["id"]
    categories = [{"title": title, "utterance_ids": []} for title in TITLES]
    categories[0]["utterance_ids"] = [first_id]

    class FakeClient:
        model = "test-model"

    monkeypatch.setattr(sessions, "get_client", lambda: FakeClient())
    monkeypatch.setattr(
        sessions,
        "analyze",
        lambda _session, _client: RequirementsAnalysis(categories=categories),
    )
    url = f"/api/sessions/{case2['id']}/requirements-analysis"
    assert client.post(url).status_code == 200

    corrected = [dict(row) for row in categories]
    corrected[0]["utterance_ids"] = []
    corrected[1]["utterance_ids"] = [first_id]
    response = client.patch(
        url,
        json={"categories": corrected, "note": "人工将首句改到廓形要求"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["review"]["status"] == "已人工核对"
    assert payload["categories"] == corrected
    assert payload["revisions"][-1]["note"] == "人工将首句改到廓形要求"
    stored = client.get(url).json()
    assert stored["categories"] == corrected
    session = client.get(f"/api/sessions/{case2['id']}").json()
    assert session["ledger"][-1]["summary"] == "人工核对需求语义归类"


def test_human_review_rejects_invented_utterance_id(client, case2, monkeypatch):
    from app.config import settings
    from app.routers import sessions

    case2["source"] = "llm"
    save_session(Session.model_validate(case2))
    monkeypatch.setattr(settings, "mock_mode", False)
    categories = [{"title": title, "utterance_ids": []} for title in TITLES]

    class FakeClient:
        model = "test-model"

    monkeypatch.setattr(sessions, "get_client", lambda: FakeClient())
    monkeypatch.setattr(
        sessions,
        "analyze",
        lambda _session, _client: RequirementsAnalysis(categories=categories),
    )
    url = f"/api/sessions/{case2['id']}/requirements-analysis"
    assert client.post(url).status_code == 200
    categories[0]["utterance_ids"] = ["invented"]
    response = client.patch(url, json={"categories": categories, "note": ""})
    assert response.status_code == 400

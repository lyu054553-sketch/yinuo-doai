import pytest
from fastapi.testclient import TestClient

from app.pipeline.revise import apply_answer
from app.schemas import AnswerRequest, Session


@pytest.mark.parametrize("old_ids", [["u4"], ["u4", "u6"]])
def test_answer_confirms_only_question_field(client: TestClient, old_ids: list[str]) -> None:
    response = client.post("/api/sessions", json={"sample_id": "c3"})
    assert response.status_code == 200, response.text
    session = Session.model_validate(response.json())
    (question,) = session.questions
    assert question.field == "版型结构"
    assert question.utterance_ids == ["u6"]
    current = session.spec_versions[-1]
    field = current.fields[question.field]
    field.utterance_ids = old_ids
    option = next(o for o in question.options if o.patch[question.field] != field.value)
    request = AnswerRequest(question_id=question.id, option_id=option.id)

    waist = next(u for u in session.utterances if u.id == "u6")
    before = {m.field: m for m in waist.mappings}
    assert before["版型结构"].status == before["松弛余量"].status == "有冲突"

    updated = apply_answer(session, request)
    waist = next(u for u in updated.utterances if u.id == "u6")
    after = {m.field: m for m in waist.mappings}
    assert after["版型结构"].status == "已与客户确认"
    assert after["松弛余量"] == before["松弛余量"]
    assert waist.status == "有冲突"
    assert waist.resolved_by is None
    version = updated.spec_versions[-1]
    assert version.version == current.version + 1
    assert version.fields[question.field].utterance_ids == ["u4", "u6"]

    repeated = apply_answer(updated, request)
    assert repeated.spec_versions == updated.spec_versions
    assert repeated.ledger == updated.ledger

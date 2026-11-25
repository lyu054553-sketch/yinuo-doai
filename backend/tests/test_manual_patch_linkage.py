"""人工改栏与原话的联动回归（示例模式，只使用开发集案例）。

规则：
* 人工改掉一栏，等于替客户解决了这一栏的出入：相关原话里这一栏的映射改记为
  “已与客户确认”，但绝不能抹掉同一句原话在其他字段上仍然存在的冲突；
* 这一栏还没回答的追问不再列出，已经答过的问题记录保留；
* 台账写明人工原因；
* 原话状态始终等于它自己各映射中最严重的那个状态；
* 人工修改的原因不能为空，空原因返回中文 400。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas import most_severe

FIELDS = ("品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量")


def patch(client: TestClient, session: dict, field: str, value: str, reason: str):
    return client.patch(
        f"/api/sessions/{session['id']}/spec",
        json={"field": field, "value": value, "reason": reason},
    )


def utterance_by_id(session: dict, uid: str) -> dict:
    return next(item for item in session["utterances"] if item["id"] == uid)


def mapping_of(utterance: dict, field: str) -> dict:
    return next(item for item in utterance["mappings"] if item["field"] == field)


def statuses_are_consistent(session: dict) -> None:
    for utterance in session["utterances"]:
        assert utterance["status"] == most_severe(
            [mapping["status"] for mapping in utterance["mappings"]]
        ), utterance["id"]


def test_manual_pattern_patch_confirms_utterance_and_drops_question(
    client: TestClient, case2: dict
) -> None:
    session = case2
    before_version = session["spec_versions"][-1]["version"]
    before_ledger = len(session["ledger"])
    before_fields = session["spec_versions"][-1]["fields"]
    pattern_question = next(q for q in session["questions"] if q["field"] == "装饰纹样")
    kept_questions = {
        q["id"]: q for q in session["questions"] if q["id"] != pattern_question["id"]
    }

    plum = utterance_by_id(session, "u9")
    assert mapping_of(plum, "装饰纹样")["status"] == "被改写"

    reason = "客户现场坚持要梅花，业务员确认按梅花落料"
    response = patch(client, session, "装饰纹样", "衣身梅花纹样，含蓄点缀", reason)
    assert response.status_code == 200, response.text
    updated = response.json()

    # 方案升一个版本，这一栏标人工修改
    assert updated["spec_versions"][-1]["version"] == before_version + 1
    field = updated["spec_versions"][-1]["fields"]["装饰纹样"]
    assert field["value"] == "衣身梅花纹样，含蓄点缀"
    assert field["origin"] == "人工修改"

    # 相关原话确认：映射转“已与客户确认”，原话状态一致，且没有编造问题 id
    plum_after = utterance_by_id(updated, "u9")
    assert mapping_of(plum_after, "装饰纹样")["status"] == "已与客户确认"
    assert plum_after["status"] == "已与客户确认"
    assert plum_after["resolved_by"] is None

    # 这一栏还没回答的追问不再出现
    unanswered_fields = [q["field"] for q in updated["questions"] if q["answer"] is None]
    assert "装饰纹样" not in unanswered_fields

    # 其他问题原样保留
    remaining = {q["id"]: q for q in updated["questions"]}
    assert set(remaining) == set(kept_questions)
    for qid, question in kept_questions.items():
        assert remaining[qid] == question

    # 其他设计字段一栏都不动
    for name in FIELDS:
        if name == "装饰纹样":
            continue
        assert updated["spec_versions"][-1]["fields"][name] == before_fields[name], name

    # 台账：多一条人工修改，写明原因
    assert len(updated["ledger"]) == before_ledger + 1
    entry = updated["ledger"][-1]
    assert entry["action"] == "人工修改"
    assert entry["changed_fields"] == ["装饰纹样"]
    assert reason in entry["detail"]
    assert "人工原因" in entry["detail"]

    statuses_are_consistent(updated)


def test_manual_patch_requires_reason(client: TestClient, case2: dict) -> None:
    response = patch(client, case2, "装饰纹样", "衣身梅花纹样", "   ")
    assert response.status_code == 400
    body = response.json()
    assert "error" in body
    assert "原因" in body["error"]

    # 被拒绝时不升版本、不改方案
    again = client.get(f"/api/sessions/{case2['id']}").json()
    assert again["spec_versions"][-1]["version"] == case2["spec_versions"][-1]["version"]
    assert (
        again["spec_versions"][-1]["fields"]["装饰纹样"]
        == case2["spec_versions"][-1]["fields"]["装饰纹样"]
    )


def test_manual_patch_keeps_conflict_in_other_field(client: TestClient) -> None:
    """同一句原话有两个设计字段冲突：人工只改一栏，另一栏的冲突必须保留。"""
    session = client.post("/api/sessions", json={"sample_id": "c3"}).json()
    waist = utterance_by_id(session, "u6")
    assert mapping_of(waist, "版型结构")["status"] == "有冲突"
    assert mapping_of(waist, "松弛余量")["status"] == "有冲突"

    shape_questions = [
        q for q in session["questions"] if q["field"] == "版型结构" and q["answer"] is None
    ]
    assert shape_questions

    response = patch(
        client, session, "版型结构", "连衣裙腰部不收紧，改为腰部宽松的 A 摆", "客户要求腰部宽松"
    )
    assert response.status_code == 200, response.text
    updated = response.json()

    waist_after = utterance_by_id(updated, "u6")
    assert mapping_of(waist_after, "版型结构")["status"] == "已与客户确认"
    # 另一栏的冲突没有被错误确认，说明也原样保留
    ease_before = mapping_of(waist, "松弛余量")
    ease_after = mapping_of(waist_after, "松弛余量")
    assert ease_after == ease_before
    assert ease_after["status"] == "有冲突"
    # 这句原话仍是它最严重的状态
    assert waist_after["status"] == "有冲突"

    # 版型结构的追问不再列出
    assert all(
        q["field"] != "版型结构" or q["answer"] for q in updated["questions"]
    )

    # 其他原话没有被顺手确认
    before_texts = {u["id"]: u for u in session["utterances"]}
    for utterance in updated["utterances"]:
        if utterance["id"] == "u6":
            continue
        assert utterance == before_texts[utterance["id"]], utterance["id"]

    statuses_are_consistent(updated)


def test_answered_question_history_is_kept(client: TestClient, case2: dict) -> None:
    """回答过的问题记录保留，人工再改同一栏也不把它删掉。"""
    session = case2
    question = next(q for q in session["questions"] if q["field"] == "装饰纹样")
    current = session["spec_versions"][-1]["fields"]["装饰纹样"]["value"]
    option = next(
        option
        for option in question["options"]
        if option["patch"].get("装饰纹样") not in (None, current)
    )
    answered = client.post(
        f"/api/sessions/{session['id']}/answers",
        json={"question_id": question["id"], "option_id": option["id"]},
    )
    assert answered.status_code == 200, answered.text
    session = answered.json()

    response = patch(client, session, "装饰纹样", "衣身梅花纹样，含蓄点缀", "客户改口要梅花")
    assert response.status_code == 200, response.text
    updated = response.json()

    kept = next(q for q in updated["questions"] if q["id"] == question["id"])
    assert kept["answer"] == option["id"]
    statuses_are_consistent(updated)
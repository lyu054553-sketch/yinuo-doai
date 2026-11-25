"""M0 验收测试：示例模式、结构、案例二三个追问、改版、幂等、台账、交接单。

只用 c1、c2、c3、c4、c6 五个开发案例，不使用留出案例五。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import knowledge
from app.schemas import most_severe

SESSION_KEYS = {
    "id", "title", "mode", "source", "created_at", "input", "utterances",
    "spec_versions", "questions", "designer_items", "skipped", "ledger",
    "reference", "images", "restatement",
}
SPEC_FIELD_KEYS = {"value", "resource_ids", "utterance_ids", "origin", "confirmed"}
QUESTION_KEYS = {"id", "utterance_ids", "field", "title", "context", "why", "options", "answer"}
OPTION_KEYS = {"id", "label", "detail", "resource_ids", "patch", "note", "todo"}
LEDGER_KEYS = {"version", "at", "action", "summary", "detail", "utterance_ids", "changed_fields"}
FIELDS = ("品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量")
DEV_CASES = ("c1", "c2", "c3", "c4", "c6")


def answer(client: TestClient, session: dict, question: dict, option: dict) -> dict:
    response = client.post(
        f"/api/sessions/{session['id']}/answers",
        json={"question_id": question["id"], "option_id": option["id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def changing_options(session: dict, question: dict) -> list[dict]:
    current = session["spec_versions"][-1]["fields"]
    return [
        option
        for option in question["options"]
        if any(
            option["patch"].get(field) != current[field]["value"]
            for field in option["patch"]
        )
    ]


def create_session(client: TestClient, case_id: str) -> dict:
    response = client.post("/api/sessions", json={"sample_id": case_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"ok", "mock_mode", "llm_model", "image_provider", "vision_ok"}
    assert data["ok"] is True
    assert data["mock_mode"] is True


def test_samples_only_dev_cases(client: TestClient) -> None:
    response = client.get("/api/samples")
    assert response.status_code == 200
    samples = response.json()
    ids = [item["id"] for item in samples]
    for case_id in DEV_CASES:
        assert case_id in ids
    assert "c5" not in ids
    for item in samples:
        assert set(item) == {"id", "title", "text", "existing_spec", "holdout"}
        assert set(item["existing_spec"]) == set(FIELDS)
        assert item["holdout"] is False


def test_samples_match_data_file(client: TestClient) -> None:
    """示例列表等于 data/cases.json 里的非留出案例，顺序也一致。"""
    raw_cases = knowledge.get_cases()
    samples = client.get("/api/samples").json()
    expected = [case for case in raw_cases if not knowledge.is_blocked_sample(case)]
    assert [item["id"] for item in samples] == [case["id"] for case in expected]
    by_id = {item["id"]: item for item in samples}
    for case in expected:
        item = by_id[case["id"]]
        assert item["text"] == case.get("customer_text", "")
        assert item["holdout"] is False


def test_resources(client: TestClient) -> None:
    response = client.get("/api/resources")
    assert response.status_code == 200
    data = response.json()
    assert data["silhouettes"] and data["fabrics"] and data["patterns"]


def test_resources_endpoint_returns_file_unchanged(client: TestClient) -> None:
    """GET /api/resources 先原样返回 data/resources.json 的全部条目（含不在推荐候选里的），
    只有 sources 去掉了留出案例；模拟资源库的条目排在同类之后，来源一律是 sim。"""
    raw = json.loads((knowledge.DATA_DIR / "resources.json").read_text(encoding="utf-8"))
    dev = knowledge.dev_case_ids()
    data = client.get("/api/resources").json()
    for key, items in raw.items():
        if key == "meta" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("sources"), list):
                item["sources"] = [s for s in item["sources"] if s in dev]
        assert data[key][: len(items)] == items
        for extra in data[key][len(items):]:
            assert extra["sources"] == ["sim"] and extra["id"].startswith("M")
    assert data["meta"]["simulated"]["source"] == "sim"
    assert all(entry["sku"].startswith("SIM-") for entry in data["inventory"])


def test_case2_session_structure(client: TestClient, case2: dict) -> None:
    session = case2
    assert set(session) == SESSION_KEYS
    assert session["mode"] == "审计已有方案"
    assert session["source"] == "mock"
    assert session["input"]["sample_id"] == "c2"
    assert len(session["spec_versions"]) == 1
    version = session["spec_versions"][0]
    assert version["version"] == 1
    assert set(version["fields"]) == set(FIELDS)
    for field in FIELDS:
        assert set(version["fields"][field]) == SPEC_FIELD_KEYS
        assert version["fields"][field]["origin"] in {"已有方案", "无出处"}

    # 原话必须是原文子串，位置正确
    text = session["input"]["text"]
    assert len(session["utterances"]) == 10
    for utterance in session["utterances"]:
        assert text[utterance["start"]:utterance["end"]] == utterance["text"]

    # 台账：导入 + 审计
    assert len(session["ledger"]) >= 2
    for entry in session["ledger"]:
        assert set(entry) == LEDGER_KEYS


def test_case2_three_questions(client: TestClient, case2: dict) -> None:
    session = case2
    questions = session["questions"]
    assert len(questions) == 3
    assert [q["field"] for q in questions] == ["装饰纹样", "版型结构", "松弛余量"]
    titles = " ".join(q["title"] for q in questions)
    assert "梅花" in titles
    assert "腰身" in titles
    assert "内搭" in titles
    for question in questions:
        assert set(question) == QUESTION_KEYS
        assert question["options"]
        for option in question["options"]:
            assert set(option) == OPTION_KEYS
            assert option["patch"]
        assert changing_options(session, question), "每个问题至少要有一个会改变方案的选项"


def test_case2_designer_items_and_skipped(client: TestClient, case2: dict) -> None:
    session = case2
    designer_text = " ".join(item["text"] for item in session["designer_items"])
    assert "领宽" in designer_text
    assert any(item["field"] == "领型" for item in session["designer_items"])

    skipped_fields = {item["field"] for item in session["skipped"]}
    assert "色彩" in skipped_fields
    assert "长度比例" in skipped_fields


def test_case2_answer_flow_to_v4(client: TestClient, case2: dict) -> None:
    session = case2
    start_version = session["spec_versions"][-1]["version"]
    start_ledger = len(session["ledger"])
    answered_labels = []

    for question in session["questions"]:
        option = changing_options(session, question)[0]
        session = answer(client, session, question, option)
        answered_labels.append(option["label"])

    assert session["spec_versions"][-1]["version"] == start_version + 3 == 4
    assert len(session["ledger"]) == start_ledger + 3
    assert all(q["answer"] for q in session["questions"])

    # 相关原话变为“已与客户确认”
    assert any(u["status"] == "已与客户确认" for u in session["utterances"])
    for question in session["questions"]:
        for utterance in session["utterances"]:
            if utterance["id"] in question["utterance_ids"]:
                assert utterance["resolved_by"] == question["id"]

    # 台账记录了改动的字段
    summaries = " ".join(entry["summary"] for entry in session["ledger"])
    for field in ("装饰纹样", "版型结构", "松弛余量"):
        assert f"【{field}】" in summaries
    assert all(entry["summary"] for entry in session["ledger"])

    handoff = client.get(f"/api/sessions/{session['id']}/handoff").json()["markdown"]
    for label in answered_labels:
        assert label in handoff
    assert "第 4 版" in handoff
    for heading in (
        "## 一、当前方案",
        "## 二、和客户确认的结果",
        "## 三、请设计师判断",
        "## 四、待办",
        "## 五、打样核对项",
        "## 六、领料建议",
        "## 七、不由系统决定的事项",
    ):
        assert heading in handoff


def test_answer_same_option_is_idempotent(client: TestClient, case2: dict) -> None:
    session = case2
    question = session["questions"][0]
    option = changing_options(session, question)[0]

    once = answer(client, session, question, option)
    assert once["spec_versions"][-1]["version"] == 2
    ledger_after_once = len(once["ledger"])

    twice = answer(client, once, question, option)
    assert twice["spec_versions"][-1]["version"] == 2
    assert len(twice["ledger"]) == ledger_after_once
    assert twice["questions"][0]["answer"] == option["id"]


def test_manual_patch_creates_version_and_ledger(client: TestClient, case2: dict) -> None:
    session = case2
    before = session["spec_versions"][-1]["version"]
    response = client.patch(
        f"/api/sessions/{session['id']}/spec",
        json={"field": "色彩", "value": "米白底＋亮粉滚边", "reason": "客户现场补充了颜色"},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["spec_versions"][-1]["version"] == before + 1
    field = updated["spec_versions"][-1]["fields"]["色彩"]
    assert field["origin"] == "人工修改"
    assert field["confirmed"] is False
    assert updated["ledger"][-1]["action"] == "人工修改"
    assert updated["ledger"][-1]["changed_fields"] == ["色彩"]
    # 其余设计字段不变
    old_fields = session["spec_versions"][-1]["fields"]
    for name in FIELDS:
        if name != "色彩":
            assert updated["spec_versions"][-1]["fields"][name]["value"] == old_fields[name]["value"]


def test_manual_patch_rejects_bad_field(client: TestClient, case2: dict) -> None:
    response = client.patch(
        f"/api/sessions/{case2['id']}/spec",
        json={"field": "肩型", "value": "落肩", "reason": "测试"},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_answer_rejects_unknown_question(client: TestClient, case2: dict) -> None:
    response = client.post(
        f"/api/sessions/{case2['id']}/answers",
        json={"question_id": "q99", "option_id": "o1"},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_mock_mode_requires_sample_id(client: TestClient) -> None:
    response = client.post("/api/sessions", json={"text": "随便说一句话"})
    assert response.status_code == 400
    assert "示例模式只支持示例案例" in response.json()["error"]


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.get("/api/sessions/doesnotexist")
    assert response.status_code == 404
    assert "error" in response.json()


KIND_ORDER = ("成衣挂拍", "面料细节", "上身效果")


def test_prompts_endpoint_returns_three_kinds(client: TestClient, case2: dict) -> None:
    """出图提示词接口（出图与视觉负责）已实现：核对真实结构、三种类型和版本号。"""
    url = f"/api/sessions/{case2['id']}/prompts"
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()

    assert set(data) == {"version", "items", "checks"}
    assert data["version"] == case2["spec_versions"][-1]["version"]
    assert [item["kind"] for item in data["items"]] == list(KIND_ORDER)
    for item in data["items"]:
        assert set(item) == {"kind", "text"}
        assert item["text"].strip()

    assert data["checks"]
    for check in data["checks"]:
        assert set(check) == {"field", "expect"}
        assert check["field"] in FIELDS
        assert check["expect"].strip()

    # 同一份方案永远得到同样的提示词（确定性，不调模型）
    assert client.get(url).json() == data


def test_prompts_unknown_session_returns_404(client: TestClient) -> None:
    response = client.get("/api/sessions/doesnotexist/prompts")
    assert response.status_code == 404
    assert "error" in response.json()


def test_other_dev_cases_load(client: TestClient) -> None:
    for case_id in ("c1", "c3", "c4", "c6"):
        response = client.post("/api/sessions", json={"sample_id": case_id})
        assert response.status_code == 200, f"{case_id}: {response.text}"
        session = response.json()
        assert set(session) == SESSION_KEYS
        assert session["input"]["sample_id"] == case_id
        assert len(session["spec_versions"]) == 1
        text = session["input"]["text"]
        for utterance in session["utterances"]:
            assert utterance["text"] in text
            assert text[utterance["start"]:utterance["end"]] == utterance["text"]
        for question in session["questions"]:
            assert changing_options(session, question)


def test_session_list(client: TestClient, case2: dict) -> None:
    response = client.get("/api/sessions")
    assert response.status_code == 200
    items = response.json()
    assert any(item["id"] == case2["id"] for item in items)
    for item in items:
        assert set(item) == {"id", "title", "created_at", "version"}


# ---------------------------------------------------------------- 标注忠实度


def test_dev_cases_annotations_are_faithful(client: TestClient) -> None:
    """示例模式必须忠实使用标注：逐句、逐映射的状态与原话状态都要和标注一致。"""
    for case_id in DEV_CASES:
        case = knowledge.get_case(case_id)
        assert case is not None
        session = create_session(client, case_id)
        annotations = case["utterances"]
        assert len(session["utterances"]) == len(annotations), case_id

        for utterance, annotation in zip(session["utterances"], annotations):
            assert utterance["text"] == annotation["text"], case_id
            expected = annotation["mappings"]
            assert len(utterance["mappings"]) == len(expected), (case_id, utterance["id"])
            for mapping, expected_mapping in zip(utterance["mappings"], expected):
                assert mapping["field"] == expected_mapping["field"], (case_id, utterance["id"])
                assert mapping["status"] == expected_mapping["status"], (
                    case_id,
                    utterance["id"],
                    mapping["field"],
                )
                assert mapping["garment"] == expected_mapping.get("garment"), (
                    case_id,
                    utterance["id"],
                )
                assert mapping["rule_id"] == expected_mapping.get("rule"), (
                    case_id,
                    utterance["id"],
                )
            assert utterance["status"] == most_severe(
                [item["status"] for item in expected]
            ), (case_id, utterance["id"])


def test_case2_plum_is_not_overridden_by_program_rules(client: TestClient) -> None:
    """回归：标注里“面料要带一些暗纹”已落实，“最好是梅花”被改写，不能被程序规则抢占。"""
    session = create_session(client, "c2")
    by_text = {utterance["text"]: utterance for utterance in session["utterances"]}
    dark = by_text["面料要带一些暗纹"]
    assert [
        mapping["status"] for mapping in dark["mappings"] if mapping["field"] == "装饰纹样"
    ] == ["已落实"]
    plum = by_text["最好是梅花"]
    assert plum["mappings"][0]["field"] == "装饰纹样"
    assert plum["mappings"][0]["status"] == "被改写"

    pattern_question = next(q for q in session["questions"] if q["field"] == "装饰纹样")
    assert "梅花" in pattern_question["title"]
    assert pattern_question["utterance_ids"] == [plum["id"]]


# ---------------------------------------------------------------- 留出资源过滤


def _all_resources() -> list[dict]:
    items: list[dict] = []
    for value in knowledge.get_resources().values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("id"):
                items.append(item)
    return items


def _excluded_resource_ids() -> set[str]:
    return {item["id"] for item in _all_resources() if not knowledge.is_recommendable(item)}


def test_resources_for_field_excludes_holdout_only_sources() -> None:
    excluded = _excluded_resource_ids()
    for field in ("版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例"):
        ids = {item["id"] for item in knowledge.resources_for_field(field)}
        assert ids.isdisjoint(excluded), field


def test_question_options_use_only_recommendable_resources(client: TestClient) -> None:
    excluded = _excluded_resource_ids()
    for case_id in DEV_CASES:
        session = create_session(client, case_id)
        for question in session["questions"]:
            for option in question["options"]:
                assert excluded.isdisjoint(option["resource_ids"]), (case_id, question["id"])


def test_holdout_only_resource_is_not_recommended(monkeypatch) -> None:
    """只按 sources 元数据过滤：只有留出案例出处的资源不进推荐候选。"""
    fake = {
        "patterns": [
            {"id": "P_DEV", "name": "开发集纹样", "sources": ["c2"]},
            {"id": "P_HOLDOUT", "name": "留出案例纹样", "sources": ["c5"]},
            {"id": "P_NOSRC", "name": "通用参考", "sources": []},
        ]
    }
    monkeypatch.setattr(knowledge, "get_resources", lambda: fake)
    ids = [item["id"] for item in knowledge.resources_for_field("装饰纹样")]
    assert ids == ["P_DEV", "P_NOSRC"]
    # 留出案例的纹样也不能被方案文字“匹配”进来
    assert knowledge.find_resource_ids("装饰纹样", "留出案例纹样") == []

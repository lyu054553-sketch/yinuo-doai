"""M1 真实大模型模式的自检（不联网、不调付费服务）。

用假传输（mock transport）喂 OpenAI 兼容格式的响应，验证：
* 真实模式能构造会话，程序规则能检出梅花被改写、宽松与收腰冲突、领宽待设计师；
* 原话位置由程序定位，且必须是原文子串；
* 只重试一次；连不上/超时/输出不合格都返回清楚的中文错误；
* 同义纹样（暗纹≈暗花）和“不收腰”不会被规则误报。

只用开发集案例，绝不涉及留出案例五。
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app import llm
from app.config import settings
from app.llm import LLMClient
from app.pipeline import audit
from app.schemas import Mapping, SpecField, Utterance

CASE2_MODEL_OUTPUT = {
    "utterances": [
        {
            "text": "想要定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [
                {"field": "品类", "status": "已落实", "note": "方案品类是外套"},
            ],
        },
        {
            "text": "风格偏中式一些",
            "type": "风格",
            "strength": "软偏好",
            "mappings": [
                {"field": "面料", "status": "间接体现", "note": "靠宋锦面料体现中式感"},
            ],
        },
        {
            "text": "适合正式场合穿着",
            "type": "场景",
            "strength": "描述",
            "mappings": [
                {"field": "面料", "status": "间接体现", "note": "提花宋锦挺而不硬"},
            ],
        },
        {
            "text": "领子不要太高",
            "type": "领型",
            "strength": "硬约束",
            "mappings": [
                {"field": "领型", "status": "已落实", "note": "青果领是翻领，没有立领高度"},
            ],
        },
        {
            # 模型漏判成“已落实”，程序规则要改成需设计师判断
            "text": "要窄一点，显得脖子细一些",
            "type": "领型",
            "strength": "硬约束",
            "mappings": [
                {"field": "领型", "status": "已落实", "note": "方案写了青果领，但没有写领宽"},
            ],
        },
        {
            # 模型只映射了松弛余量，没发现版型写了“微微收腰”，靠程序规则查冲突
            "text": "整体宽松舒展一点",
            "type": "松量",
            "strength": "软偏好",
            "mappings": [
                {"field": "松弛余量", "status": "已落实", "note": "整体围度宽松"},
            ],
        },
        {
            "text": "我里面要搭配一件内搭",
            "type": "松量",
            "strength": "硬约束",
            "mappings": [
                {
                    "field": "松弛余量",
                    "status": "间接体现",
                    "note": "只写了整体围度宽松，没有写内搭放量",
                    "rule_id": "R8",
                },
            ],
        },
        {
            "text": "面料要带一些暗纹",
            "type": "纹样",
            "strength": "硬约束",
            "mappings": [
                {"field": "面料", "status": "已落实", "note": "提花宋锦"},
                {"field": "装饰纹样", "status": "已落实", "note": "植物暗花"},
            ],
        },
        {
            # 模型误判成“已落实”，程序规则要改成被改写
            "text": "最好是梅花",
            "type": "纹样",
            "strength": "软偏好",
            "mappings": [
                {"field": "装饰纹样", "status": "已落实", "note": "方案写的是植物暗花"},
            ],
        },
        {
            "text": "要显得优雅一些",
            "type": "风格",
            "strength": "目标",
            "mappings": [
                {"field": "面料", "status": "间接体现", "note": "柔和光泽"},
            ],
        },
    ]
}


class FakeTransport:
    """假传输：按顺序返回响应内容，不碰网络。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []
        self.payloads: list[dict] = []

    def __call__(self, url, headers, payload, timeout) -> str:
        self.urls.append(url)
        self.payloads.append(payload)
        if not self.responses:
            raise AssertionError("模型被调用的次数超出了预期")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
            ensure_ascii=False,
        )


def install(responses: list[object]) -> FakeTransport:
    transport = FakeTransport(responses)
    llm.set_client(
        LLMClient(
            base_url="http://model.invalid/v1",
            api_key="test-key",
            model="fake-model",
            timeout=5.0,
            transport=transport,
        )
    )
    return transport


@pytest.fixture()
def real_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    yield
    llm.reset_client()


def create(client, payload: dict):
    return client.post("/api/sessions", json=payload)


# ---------------------------------------------------------------- 正常路径


def test_real_mode_case2_session(client, real_mode) -> None:
    transport = install([CASE2_MODEL_OUTPUT])
    response = create(client, {"sample_id": "c2"})
    assert response.status_code == 200, response.text
    session = response.json()

    assert session["source"] == "llm"
    assert session["mode"] == "审计已有方案"
    assert session["input"]["sample_id"] == "c2"
    assert len(transport.urls) == 1
    assert transport.urls[0].endswith("/chat/completions")
    assert transport.payloads[0]["model"] == "fake-model"

    text = session["input"]["text"]
    assert len(session["utterances"]) == len(CASE2_MODEL_OUTPUT["utterances"])
    for utterance in session["utterances"]:
        assert text[utterance["start"]:utterance["end"]] == utterance["text"]

    assert set(session["spec_versions"][0]["fields"]) == {
        "品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量"
    }
    for field in session["spec_versions"][0]["fields"].values():
        assert field["origin"] in {"已有方案", "无出处"}


def test_real_mode_detects_case2_problems(client, real_mode) -> None:
    install([CASE2_MODEL_OUTPUT])
    session = create(client, {"sample_id": "c2"}).json()
    by_text = {u["text"]: u for u in session["utterances"]}

    plum = by_text["最好是梅花"]
    plum_mapping = next(m for m in plum["mappings"] if m["field"] == "装饰纹样")
    assert plum_mapping["status"] == "被改写"
    assert plum["status"] == "被改写"

    dark = by_text["面料要带一些暗纹"]
    dark_mapping = next(m for m in dark["mappings"] if m["field"] == "装饰纹样")
    assert dark_mapping["status"] == "已落实", "暗纹与暗花是同义表达，不能被误报"

    loose = by_text["整体宽松舒展一点"]
    assert any(
        m["field"] == "版型结构" and m["status"] == "有冲突" for m in loose["mappings"]
    ), "整体宽松与微微收腰应判冲突"

    narrow = by_text["要窄一点，显得脖子细一些"]
    assert any(
        m["field"] == "领型" and m["status"] == "需设计师判断" for m in narrow["mappings"]
    )

    designer_text = " ".join(item["text"] for item in session["designer_items"])
    assert "领宽" in designer_text
    assert any(item["field"] == "领型" for item in session["designer_items"])


def test_real_mode_case2_questions_and_skipped(client, real_mode) -> None:
    install([CASE2_MODEL_OUTPUT])
    session = create(client, {"sample_id": "c2"}).json()

    questions = session["questions"]
    assert [q["field"] for q in questions] == ["装饰纹样", "版型结构", "松弛余量"]
    titles = " ".join(q["title"] for q in questions)
    assert "梅花" in titles
    assert "腰身" in titles
    assert "内搭" in titles

    skipped = {item["field"] for item in session["skipped"]}
    assert {"色彩", "长度比例"}.issubset(skipped)


def test_real_mode_answer_flow_to_v4(client, real_mode) -> None:
    install([CASE2_MODEL_OUTPUT])
    session = create(client, {"sample_id": "c2"}).json()
    start_version = session["spec_versions"][-1]["version"]
    start_ledger = len(session["ledger"])

    for question in session["questions"]:
        current = session["spec_versions"][-1]["fields"]
        option = next(
            option
            for option in question["options"]
            if any(
                option["patch"].get(field) != current[field]["value"]
                for field in option["patch"]
            )
        )
        response = client.post(
            f"/api/sessions/{session['id']}/answers",
            json={"question_id": question["id"], "option_id": option["id"]},
        )
        assert response.status_code == 200, response.text
        session = response.json()

    assert session["spec_versions"][-1]["version"] == start_version + 3 == 4
    assert len(session["ledger"]) == start_ledger + 3

    handoff = client.get(f"/api/sessions/{session['id']}/handoff").json()["markdown"]
    assert "第 4 版" in handoff
    assert "## 二、和客户确认的结果" in handoff


# ---------------------------------------------------------------- 重试与错误


def test_invalid_json_retries_once_then_succeeds(client, real_mode) -> None:
    transport = install(["这不是 JSON，模型跑偏了", CASE2_MODEL_OUTPUT])
    response = create(client, {"sample_id": "c2"})
    assert response.status_code == 200, response.text
    assert len(transport.urls) == 2
    assert "不合格" in transport.payloads[1]["messages"][-1]["content"]


def test_invalid_json_twice_returns_chinese_error(client, real_mode) -> None:
    transport = install(["不是 JSON", "还是不是 JSON"])
    response = create(client, {"sample_id": "c2"})
    assert response.status_code == 502
    message = response.json()["error"]
    assert "大模型" in message and "重试" in message
    assert len(transport.urls) == 2


def test_utterance_not_in_text_is_rejected(client, real_mode) -> None:
    bad = json.loads(json.dumps(CASE2_MODEL_OUTPUT))
    bad["utterances"][0]["text"] = "客户想做的是一件大衣"
    transport = install([bad, bad])
    response = create(client, {"sample_id": "c2"})
    assert response.status_code == 502
    assert "原文" in response.json()["error"]
    assert len(transport.urls) == 2


def test_connection_error_returns_chinese_error(client, real_mode) -> None:
    install([urllib.error.URLError("connection refused")])
    response = create(client, {"sample_id": "c2"})
    assert response.status_code == 502
    message = response.json()["error"]
    assert "连不上大模型" in message


def test_from_zero_mode_generates_first_version(client, real_mode) -> None:
    """M2：只给口述时走“从零生成方案”，不再返回 501。"""
    output = {
        "utterances": [
            {
                "text": "想要一件宽松的外套",
                "type": "松量",
                "strength": "软偏好",
                "mappings": [{"field": "版型结构", "status": "已落实", "note": "客户要求宽松"}],
            }
        ]
    }
    install([output])
    response = create(client, {"text": "想要一件宽松的外套"})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["mode"] == "从零生成方案"
    assert session["source"] == "llm"
    assert session["input"]["existing_spec"] is None
    assert session["spec_versions"][0]["fields"]["版型结构"]["resource_ids"]


def test_unknown_sample_in_real_mode(client, real_mode) -> None:
    install([])
    response = create(client, {"sample_id": "c99"})
    assert response.status_code == 400
    assert "找不到" in response.json()["error"]


def test_client_reads_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(settings, "llm_model", "some-model")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "llm_timeout", 33.0)
    client = LLMClient.from_settings()
    assert client.endpoint == "http://127.0.0.1:9999/v1/chat/completions"
    assert client.model == "some-model"
    assert client.timeout == 33.0


# ---------------------------------------------------------------- 程序规则本身


def _utterance(text: str, field: str, status: str, strength: str = "软偏好") -> Utterance:
    return Utterance(
        id="u1",
        text=text,
        start=0,
        end=len(text),
        type="纹样",
        strength=strength,
        status=status,
        mappings=[Mapping(field=field, status=status, note="")],
    )


def _fields(**values: str) -> dict[str, SpecField]:
    names = ("品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量")
    return {
        name: SpecField(
            value=values.get(name, ""),
            resource_ids=[],
            utterance_ids=[],
            origin="已有方案" if values.get(name) else "无出处",
        )
        for name in names
    }


def test_pattern_synonym_is_not_false_positive() -> None:
    utterance = _utterance("面料要带一些暗纹", "装饰纹样", "已落实", "硬约束")
    fields = _fields(装饰纹样="衣身植物暗花纹样，点缀细碎点状星光")
    audit.apply_program_rules([utterance], fields)
    assert utterance.mappings[0].status == "已落实"


def test_negated_waist_tuck_is_not_conflict() -> None:
    utterance = _utterance("整体宽松一点，不收腰", "版型结构", "已落实")
    fields = _fields(版型结构="H 型直身结构，无收腰；肩型微落平肩")
    audit.apply_program_rules([utterance], fields)
    assert utterance.mappings[0].status == "已落实"
    assert audit.has_waist_tuck("H 型直身结构，无收腰") is False
    assert audit.has_waist_tuck("H 型直身结构，微微收腰") is True


def test_real_waist_tuck_conflicts() -> None:
    utterance = _utterance("整体宽松舒展一点", "版型结构", "已落实")
    fields = _fields(版型结构="H 型直身结构，微微收腰")
    audit.apply_program_rules([utterance], fields)
    assert utterance.mappings[0].status == "有冲突"


def test_specific_pattern_missing_is_rewritten() -> None:
    """方案里换了别的纹样 → 被改写；方案根本没写纹样 → 未落实。"""
    soft = _utterance("最好是梅花", "装饰纹样", "已落实", "软偏好")
    hard = _utterance("一定要梅花", "装饰纹样", "已落实", "硬约束")
    fields = _fields(装饰纹样="衣身植物暗花纹样")
    audit.apply_program_rules([soft, hard], fields)
    assert soft.mappings[0].status == "被改写"
    assert hard.mappings[0].status == "被改写"

    empty = _utterance("最好是梅花", "装饰纹样", "已落实", "软偏好")
    no_pattern = _fields(装饰纹样="无特殊需求")
    audit.apply_program_rules([empty], no_pattern)
    assert empty.mappings[0].status == "未落实"

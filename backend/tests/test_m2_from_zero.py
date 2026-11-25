"""M2 从零生成方案的自检（不联网、不调付费服务）。

用假传输喂 OpenAI 兼容格式的响应，验证：

* 只给客户口述（有/没有 sample_id 两种）都走“从零生成方案”，不会偷偷拿案例旧方案；
* 明确给了 existing_spec 才走审计路径；
* 每一栏要么有真实资源 id（来自 resources.json、且不是只有留出案例出处的），
  要么明确标“无出处”，resource_ids 为空；
* 客户硬要资源库里没有的梅花时标“无出处”，不编资源 id；
* 资源匹配命中身形规则 R1–R8 时写到字段和映射上；
* 没有原话涉及的字段用资源库最常见取值并写进“不问清单”；
* 审计的对象是刚匹配出来的初稿（梅花软偏好 → 被改写并生成追问）；
* 内搭问题不再出现没有出处的具体厘米数。

只用开发集数据，绝不涉及留出案例五。
"""

from __future__ import annotations

import json
import re

import pytest

from app import knowledge, llm
from app.config import settings
from app.llm import LLMClient

FIELDS = ("品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量")

FROM_ZERO_TEXT = (
    "想要定制一件外套，整体宽松舒展一点，风格偏中式一些，领子不要太高，"
    "面料要带一些暗纹，最好是梅花，我里面要搭配一件内搭"
)

FROM_ZERO_OUTPUT = {
    "utterances": [
        {
            "text": "想要定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "未落实", "note": "客户要外套"}],
        },
        {
            "text": "整体宽松舒展一点",
            "type": "松量",
            "strength": "软偏好",
            "mappings": [
                {"field": "松弛余量", "status": "未落实", "note": "要宽松"},
                {"field": "版型结构", "status": "未落实", "note": "不要收腰"},
            ],
        },
        {
            "text": "风格偏中式一些",
            "type": "风格",
            "strength": "软偏好",
            "mappings": [
                {"field": "领型", "status": "未落实", "note": "要中式"},
                {"field": "面料", "status": "未落实", "note": "要中式"},
            ],
        },
        {
            "text": "领子不要太高",
            "type": "领型",
            "strength": "硬约束",
            "mappings": [{"field": "领型", "status": "未落实", "note": "领子要低"}],
        },
        {
            "text": "面料要带一些暗纹",
            "type": "纹样",
            "strength": "硬约束",
            "mappings": [
                {"field": "面料", "status": "未落实", "note": "要暗纹"},
                {"field": "装饰纹样", "status": "未落实", "note": "要暗纹"},
            ],
        },
        {
            "text": "最好是梅花",
            "type": "纹样",
            "strength": "软偏好",
            "mappings": [{"field": "装饰纹样", "status": "未落实", "note": "要梅花"}],
        },
        {
            "text": "我里面要搭配一件内搭",
            "type": "松量",
            "strength": "硬约束",
            "mappings": [{"field": "松弛余量", "status": "未落实", "note": "要配内搭"}],
        },
    ]
}


class FakeTransport:
    """假传输：按顺序返回响应内容，不碰网络。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, url, headers, payload, timeout) -> str:
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


def _resource_ok(rid: str) -> bool:
    item = knowledge.resource_by_id(rid)
    assert item is not None, f"方案引用了资源库里不存在的 id：{rid}"
    return knowledge.is_recommendable(item)


LENGTHISH = re.compile(r"\d+(\.\d+)?\s*(cm|厘米|公分)")


# ---------------------------------------------------------------- 两条入口


def test_from_zero_without_sample_id(client, real_mode) -> None:
    transport = install([FROM_ZERO_OUTPUT])
    response = create(client, {"text": FROM_ZERO_TEXT})
    assert response.status_code == 200, response.text
    session = response.json()

    assert session["mode"] == "从零生成方案"
    assert session["source"] == "llm"
    assert session["input"]["existing_spec"] is None
    assert session["input"]["sample_id"] is None
    assert len(transport.payloads) == 1

    fields = session["spec_versions"][0]["fields"]
    assert set(fields) == set(FIELDS)
    for name in FIELDS:
        item = fields[name]
        assert item["origin"] in {"资源库", "客户确认", "无出处"}
        if item["resource_ids"]:
            assert item["origin"] == "资源库"
            for rid in item["resource_ids"]:
                assert _resource_ok(rid)
        else:
            assert item["origin"] in {"客户确认", "无出处"}, name
        for uid in item["utterance_ids"]:
            assert any(utterance["id"] == uid for utterance in session["utterances"])


def test_from_zero_ignores_case_spec_even_with_sample_id(client, real_mode) -> None:
    """给了口述、没给方案：即使带了 sample_id 也从零生成，不拿案例旧方案。"""
    install([FROM_ZERO_OUTPUT])
    response = create(client, {"sample_id": "c2", "text": FROM_ZERO_TEXT})
    assert response.status_code == 200, response.text
    session = response.json()

    assert session["mode"] == "从零生成方案"
    assert session["input"]["existing_spec"] is None
    case2 = knowledge.case_spec(knowledge.get_case("c2"))
    fields = session["spec_versions"][0]["fields"]
    assert fields["版型结构"]["value"] != case2["版型结构"]
    assert "微微收腰" not in fields["版型结构"]["value"]
    # 从零生成的这一栏必须有资源出处，而不是照抄案例文字
    assert fields["版型结构"]["origin"] == "资源库"
    assert fields["版型结构"]["resource_ids"]


AUDIT_OUTPUT = {
    "utterances": [
        {
            "text": "想要定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "方案品类是外套"}],
        },
    ]
}


def test_explicit_existing_spec_still_audits(client, real_mode) -> None:
    install([AUDIT_OUTPUT])
    session = create(
        client,
        {
            "text": "想要定制一件外套",
            "existing_spec": {"品类": "外套", "版型结构": "H 型直身结构，微微收腰"},
        },
    ).json()
    assert session["mode"] == "审计已有方案"
    assert session["input"]["existing_spec"]["品类"] == "外套"
    assert session["spec_versions"][0]["fields"]["品类"]["origin"] == "已有方案"
    assert session["spec_versions"][0]["fields"]["版型结构"]["value"] == "H 型直身结构，微微收腰"


def test_sample_id_without_text_still_audits_sample(client, real_mode) -> None:
    case = knowledge.get_case("c2")
    output = {
        "utterances": [
            {
                "text": case["utterances"][0]["text"],
                "type": "描述",
                "strength": "描述",
                "mappings": [{"field": "品类", "status": "已落实", "note": "方案品类是外套"}],
            }
        ]
    }
    install([output])
    session = create(client, {"sample_id": "c2"}).json()
    assert session["mode"] == "审计已有方案"
    assert session["input"]["sample_id"] == "c2"
    assert session["input"]["existing_spec"]["品类"] == case["spec"]["品类"]


def test_mock_mode_without_sample_id_is_400(client) -> None:
    """示例模式保持原样：没有 sample_id 直接 400，不走从零生成。"""
    response = create(client, {"text": FROM_ZERO_TEXT})
    assert response.status_code == 400
    assert "示例模式只支持示例案例" in response.json()["error"]


# ---------------------------------------------------------------- 匹配质量


def test_from_zero_fields_are_traceable(client, real_mode) -> None:
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    fields = session["spec_versions"][0]["fields"]

    # 宽松 + 不要收腰：廓形不能选收腰款
    shape = fields["版型结构"]["value"]
    assert "收腰" not in shape.replace("无收腰", "")
    # 客户要暗纹：面料这一栏要有出处
    assert fields["面料"]["origin"] == "资源库"
    assert fields["面料"]["resource_ids"]
    # 客户要配内搭：写明预留放量，不写死具体厘米数
    ease = fields["松弛余量"]["value"]
    assert "为内搭预留放量" in ease
    assert not LENGTHISH.search(ease)


def test_unmentioned_fields_use_common_value_and_are_skipped(client, real_mode) -> None:
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    fields = session["spec_versions"][0]["fields"]
    skipped = {item["field"]: item["reason"] for item in session["skipped"]}

    # 衣长没有原话：按资源库最常见取值（L1 衣长至臀中）
    assert fields["长度比例"]["value"]
    assert fields["长度比例"]["origin"] == "资源库"
    assert "L1" in fields["长度比例"]["resource_ids"]
    assert "长度比例" in skipped
    assert skipped["长度比例"]

    # 颜色没有原话：不硬套资源库里唯一的色彩参考
    assert "色彩" in skipped
    assert fields["色彩"]["value"] == ""
    assert fields["色彩"]["resource_ids"] == []


def test_soft_pattern_is_audited_against_the_generated_draft(client, real_mode) -> None:
    """审计对象是刚匹配出来的初稿：梅花软偏好 → 被改写，并生成追问。"""
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    by_text = {utterance["text"]: utterance for utterance in session["utterances"]}

    plum = by_text["最好是梅花"]
    mapping = next(m for m in plum["mappings"] if m["field"] == "装饰纹样")
    assert mapping["status"] == "被改写"
    assert session["spec_versions"][0]["fields"]["装饰纹样"]["origin"] == "资源库"

    pattern_question = next(q for q in session["questions"] if q["field"] == "装饰纹样")
    assert "梅花" in pattern_question["title"]
    current = session["spec_versions"][0]["fields"]["装饰纹样"]["value"]
    assert any(
        option["patch"].get("装饰纹样") not in (None, current)
        for option in pattern_question["options"]
    )
    persist = [
        option
        for option in pattern_question["options"]
        if "坚持原话要求" in option["label"]
    ]
    assert persist and persist[0]["resource_ids"] == [] and persist[0]["todo"]


def test_category_is_from_utterance_not_confirmed_without_question(
    client, real_mode
) -> None:
    """品类来自客户口述、资源库没有独立品类项：不写资源 id，也不能标“客户确认”。"""
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    category = session["spec_versions"][0]["fields"]["品类"]

    assert category["value"] == "外套"
    assert category["resource_ids"] == []
    assert category["origin"] == "无出处"
    assert category["confirmed"] is False
    # 保留真实原话，交接单才说得清这一栏是从哪句话来的
    assert category["utterance_ids"]
    assert any(u["id"] in category["utterance_ids"] for u in session["utterances"])

    handoff = client.get(f"/api/sessions/{session['id']}/handoff").json()["markdown"]
    assert "品类" in handoff
    # 交接单要说明品类来自口述，不能让人去找品类样布
    assert "客户口述" in handoff
    assert "资源库没有独立的品类项" in handoff
    assert "【品类】方案里这一栏没有资源出处，需要人工找样确认。" not in handoff


HARD_PLUM_TEXT = "想定制一件外套，一定要梅花纹样"
HARD_PLUM_OUTPUT = {
    "utterances": [
        {
            "text": "想定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "一定要梅花纹样",
            "type": "纹样",
            "strength": "硬约束",
            "mappings": [{"field": "装饰纹样", "status": "已落实", "note": "硬要梅花"}],
        },
    ]
}


def test_hard_pattern_without_resource_is_marked_no_source(client, real_mode, no_sim) -> None:
    """客户硬要资源库里没有的梅花（只用案例资源时）：标无出处，不编资源 id、不编库存。"""
    install([HARD_PLUM_OUTPUT])
    session = create(client, {"text": HARD_PLUM_TEXT}).json()
    field = session["spec_versions"][0]["fields"]["装饰纹样"]

    assert field["origin"] == "无出处"
    assert field["resource_ids"] == []
    assert "梅花" in field["value"]
    # 资源库里确实没有能对上的纹样，没有 id 被硬凑上去
    for item in knowledge.get_resources().get("patterns", []):
        assert knowledge.attr(item["id"]).get("梅花") is not True


def test_style_only_mapping_does_not_choose_specific_pattern(
    client, real_mode
) -> None:
    """“简洁大气”是整体风格，不能据此自动套用植物暗花等具体纹样。"""
    install(
        [
            {
                "utterances": [
                    {
                        "text": "想定制一件外套",
                        "type": "品类",
                        "strength": "描述",
                        "mappings": [
                            {
                                "field": "品类",
                                "status": "已落实",
                                "note": "客户要外套",
                            }
                        ],
                    },
                    {
                        "text": "团队形象要简洁大气",
                        "type": "风格",
                        "strength": "软偏好",
                        "mappings": [
                            {
                                "field": "装饰纹样",
                                "status": "未落实",
                                "note": "只表达整体风格",
                            }
                        ],
                    },
                ]
            }
        ]
    )
    session = create(
        client, {"text": "想定制一件外套，团队形象要简洁大气"}
    ).json()
    pattern = session["spec_versions"][0]["fields"]["装饰纹样"]

    assert pattern["value"] == ""
    assert pattern["resource_ids"] == []
    assert pattern["origin"] == "无出处"


def test_hard_color_alternatives_are_preserved_when_no_resource_exists(
    client, real_mode
) -> None:
    """客户给出“蓝色或灰色”时，初稿要保留两个待确认选项，不能截成蓝色。"""
    install(
        [
            {
                "utterances": [
                    {
                        "text": "想定制一件外套",
                        "type": "品类",
                        "strength": "描述",
                        "mappings": [
                            {
                                "field": "品类",
                                "status": "已落实",
                                "note": "客户要外套",
                            }
                        ],
                    },
                    {
                        "text": "颜色要蓝色或灰色",
                        "type": "色彩",
                        "strength": "硬约束",
                        "mappings": [
                            {
                                "field": "色彩",
                                "status": "未落实",
                                "note": "客户接受蓝色或灰色",
                            }
                        ],
                    },
                ]
            }
        ]
    )
    session = create(
        client, {"text": "想定制一件外套，颜色要蓝色或灰色"}
    ).json()
    color = session["spec_versions"][0]["fields"]["色彩"]

    assert "蓝色" in color["value"]
    assert "灰色" in color["value"]
    assert color["resource_ids"] == []
    assert color["origin"] == "无出处"


RULES_TEXT = "想要一件外套，我有肚腩，还有一点溜肩，里面要穿一件内搭"
RULES_OUTPUT = {
    "utterances": [
        {
            "text": "想要一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "我有肚腩",
            "type": "身形",
            "strength": "描述",
            "mappings": [{"field": "松弛余量", "status": "已落实", "note": "腹部要松量"}],
        },
        {
            "text": "还有一点溜肩",
            "type": "身形",
            "strength": "描述",
            "mappings": [{"field": "版型结构", "status": "已落实", "note": "溜肩"}],
        },
        {
            "text": "里面要穿一件内搭",
            "type": "松量",
            "strength": "硬约束",
            "mappings": [{"field": "松弛余量", "status": "已落实", "note": "要配内搭"}],
        },
    ]
}


def test_body_rules_r1_r7_r8_are_applied(client, real_mode) -> None:
    install([RULES_OUTPUT])
    session = create(client, {"text": RULES_TEXT}).json()

    rule_ids = {
        mapping["rule_id"]
        for utterance in session["utterances"]
        for mapping in utterance["mappings"]
        if mapping["rule_id"]
    }
    assert "R1" in rule_ids
    assert "R8" in rule_ids

    ease = session["spec_versions"][0]["fields"]["松弛余量"]["value"]
    assert "前片腹部预留充足松量" in ease
    assert "为内搭预留放量" in ease

    designer_text = " ".join(item["text"] for item in session["designer_items"])
    assert "溜肩" in designer_text


# ---------------------------------------------------------------- 内搭追问文案


def test_inner_layer_options_have_no_invented_numbers(client, real_mode) -> None:
    """R8 没有给出胸围加放数值，内搭问题里不能出现任何具体厘米数。"""
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    inner = next(
        question
        for question in session["questions"]
        if question["field"] == "松弛余量" and "内搭" in question["title"]
    )
    assert inner["options"]
    for option in inner["options"]:
        blob = option["label"] + option["detail"] + option["note"] + (option["todo"] or "")
        blob += " ".join(option["patch"].values())
        assert "4cm" not in blob.replace(" ", "")
        assert not LENGTHISH.search(blob), blob
        assert "胸围" not in blob


def test_answering_question_versions_the_from_zero_session(client, real_mode) -> None:
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    question = session["questions"][0]
    current = session["spec_versions"][-1]["fields"]
    option = next(
        option
        for option in question["options"]
        if any(
            option["patch"].get(name) != current[name]["value"] for name in option["patch"]
        )
    )
    before_ledger = len(session["ledger"])
    response = client.post(
        f"/api/sessions/{session['id']}/answers",
        json={"question_id": question["id"], "option_id": option["id"]},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["spec_versions"][-1]["version"] == 2
    assert len(updated["ledger"]) == before_ledger + 1
    changed = updated["spec_versions"][-1]["fields"][question["field"]]
    assert changed["origin"] == "客户确认"
    assert changed["confirmed"] is True


# ---------------------------------------------------------------- 资源词的正负向与单品匹配


def _want(field: str, text: str, strength: str = "软偏好", garment: str | None = None):
    from app.pipeline import match
    from app.schemas import Mapping, Utterance

    utterance = Utterance(
        id="u1",
        text=text,
        start=0,
        end=len(text),
        type="描述",
        strength=strength,
        status="已落实",
        mappings=[Mapping(field=field, garment=garment, status="已落实")],
    )
    return match.Want(
        field=field, text=text, strength=strength, utterance=utterance, garment=garment
    )


def test_positive_material_alias_scores_and_negative_excludes() -> None:
    """“想用醋酸”给 F1 加分；“不要醋酸”把 F1 排除，也不反向加分。"""
    from app.pipeline import match

    fabric_f1 = knowledge.resource_by_id("F1")
    fabric_f3 = knowledge.resource_by_id("F3")

    positive = [_want("面料", "面料想用醋酸")]
    assert match._score(fabric_f1, positive) > match._score(fabric_f3, positive)

    negative = [_want("面料", "面料不要醋酸", "硬约束")]
    kept = match._wanted_filter([fabric_f1, fabric_f3], negative)
    assert [item["id"] for item in kept] == ["F3"]
    assert match._score(fabric_f1, negative) <= match._score(fabric_f3, negative)


def test_hard_named_material_filters_out_other_fabrics() -> None:
    """硬点名“一定要醋酸”时只保留能满足的资源。"""
    from app.pipeline import match

    pool = [knowledge.resource_by_id(rid) for rid in ("F1", "F2", "F3", "F6", "F8")]
    kept = match._wanted_filter(pool, [_want("面料", "面料一定要醋酸", "硬约束")])
    assert [item["id"] for item in kept] == ["F1"]


def test_negated_collar_alias_is_excluded() -> None:
    """“不要交领”不能选中交领资源。"""
    from app.pipeline import match

    open_collar = knowledge.resource_by_id("CL5")
    low_collar = knowledge.resource_by_id("CL1")
    kept = match._wanted_filter(
        [open_collar, low_collar], [_want("领型", "领子不要交领", "硬约束")]
    )
    assert "CL5" not in [item["id"] for item in kept]


def test_shoulder_allow_keeps_only_non_pressing_shoulder() -> None:
    """要求不压肩时只保留压肩=False 的肩型（SH3），不能把 SH1/SH2 留下。"""
    from app.pipeline import match

    texts = "版型要不压肩"
    assert match._shoulder_allow(knowledge.resource_by_id("SH3"), texts) is True
    assert match._shoulder_allow(knowledge.resource_by_id("SH1"), texts) is False
    assert match._shoulder_allow(knowledge.resource_by_id("SH2"), texts) is False
    # 没有这条要求时不额外过滤
    assert match._shoulder_allow(knowledge.resource_by_id("SH1"), "整体宽松") is True


def test_lining_is_not_used_as_outer_fabric() -> None:
    """里料资源（真丝里布）不能当外套主面料，除非原话明确要里料。"""
    from app.pipeline import match

    lining = knowledge.resource_by_id("F4")
    assert match._fabric_allow(lining, "") is False
    assert match._fabric_allow(lining, "整体宽松") is False
    assert match._fabric_allow(lining, "里料想用真丝里布") is True


MATERIAL_TEXT = "想定制一件外套，面料想用醋酸"
MATERIAL_OUTPUT = {
    "utterances": [
        {
            "text": "想定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "面料想用醋酸",
            "type": "面料",
            "strength": "软偏好",
            "mappings": [{"field": "面料", "status": "已落实", "note": "想用醋酸"}],
        },
    ]
}


def test_from_zero_soft_material_uses_named_resource(client, real_mode) -> None:
    """实际输入“面料想用醋酸”：软偏好也点到了资源名，不能忽略它去选宋锦。"""
    install([MATERIAL_OUTPUT])
    session = create(client, {"text": MATERIAL_TEXT}).json()
    fabric = session["spec_versions"][0]["fields"]["面料"]
    assert fabric["origin"] == "资源库"
    assert fabric["resource_ids"] == ["F1"]
    assert "F4" not in fabric["resource_ids"]


HARD_MATERIAL_TEXT = "想定制一件外套，面料一定要醋酸"
HARD_MATERIAL_OUTPUT = {
    "utterances": [
        {
            "text": "想定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "面料一定要醋酸",
            "type": "面料",
            "strength": "硬约束",
            "mappings": [{"field": "面料", "status": "已落实", "note": "硬要醋酸"}],
        },
    ]
}


def test_from_zero_hard_material_filters_other_fabrics(client, real_mode) -> None:
    install([HARD_MATERIAL_OUTPUT])
    session = create(client, {"text": HARD_MATERIAL_TEXT}).json()
    fabric = session["spec_versions"][0]["fields"]["面料"]
    assert fabric["resource_ids"] == ["F1"]


NEGATIVE_TEXT = "想定制一件外套，面料不要醋酸，领子不要交领"
NEGATIVE_OUTPUT = {
    "utterances": [
        {
            "text": "想定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "面料不要醋酸",
            "type": "面料",
            "strength": "硬约束",
            "mappings": [{"field": "面料", "status": "已落实", "note": "不要醋酸"}],
        },
        {
            "text": "领子不要交领",
            "type": "领型",
            "strength": "硬约束",
            "mappings": [{"field": "领型", "status": "已落实", "note": "不要交领"}],
        },
    ]
}


def test_from_zero_negated_material_and_collar_not_selected(client, real_mode) -> None:
    install([NEGATIVE_OUTPUT])
    session = create(client, {"text": NEGATIVE_TEXT}).json()
    fields = session["spec_versions"][0]["fields"]
    assert "F1" not in fields["面料"]["resource_ids"]
    assert fields["面料"]["resource_ids"], "排除醋酸后还应从别的面料里选"
    assert "CL5" not in fields["领型"]["resource_ids"]


NO_PRESS_TEXT = "想定制一件外套，版型要不压肩，整体宽松一点"
NO_PRESS_OUTPUT = {
    "utterances": [
        {
            "text": "想定制一件外套",
            "type": "品类",
            "strength": "描述",
            "mappings": [{"field": "品类", "status": "已落实", "note": "客户要外套"}],
        },
        {
            "text": "版型要不压肩",
            "type": "身形",
            "strength": "硬约束",
            "mappings": [{"field": "版型结构", "status": "已落实", "note": "不压肩"}],
        },
        {
            "text": "整体宽松一点",
            "type": "松量",
            "strength": "软偏好",
            "mappings": [{"field": "松弛余量", "status": "已落实", "note": "要宽松"}],
        },
    ]
}


def test_from_zero_no_press_shoulder_picks_sh3(client, real_mode) -> None:
    install([NO_PRESS_OUTPUT])
    session = create(client, {"text": NO_PRESS_TEXT}).json()
    shape = session["spec_versions"][0]["fields"]["版型结构"]
    assert "SH3" in shape["resource_ids"]
    assert "SH1" not in shape["resource_ids"]
    assert "SH2" not in shape["resource_ids"]


def test_fields_are_returned_in_contract_order(client, real_mode) -> None:
    """八栏顺序固定：返回的字段顺序必须与约定一致。"""
    install([FROM_ZERO_OUTPUT])
    session = create(client, {"text": FROM_ZERO_TEXT}).json()
    fields = session["spec_versions"][0]["fields"]
    assert list(fields.keys()) == list(FIELDS)


def _case_from_zero_output(case_id: str) -> dict:
    """用开发集案例的标注拼一份“从零”的假模型输出（只拆句，不判落实）。"""
    case = knowledge.get_case(case_id)
    utterances = []
    for item in case["utterances"]:
        mappings = [
            {
                "field": mapping["field"],
                "garment": mapping.get("garment"),
                "status": "已落实",
                "note": f"客户在这句里对【{mapping['field']}】提出了要求",
                "rule_id": mapping.get("rule"),
            }
            for mapping in item["mappings"]
        ]
        utterances.append(
            {
                "text": item["text"],
                "type": item.get("type", "描述"),
                "strength": item.get("strength", "描述"),
                "mappings": mappings,
            }
        )
    return {"utterances": utterances}


def test_from_zero_multi_garment_keeps_each_piece(client, real_mode) -> None:
    """多件套按单品分别匹配：字段值写成“外套：……；连衣裙：……”，不丢单品。"""
    case = knowledge.get_case("c3")
    install([_case_from_zero_output("c3")])
    session = create(client, {"text": case["customer_text"]}).json()
    assert session["mode"] == "从零生成方案"
    fields = session["spec_versions"][0]["fields"]

    shape = fields["版型结构"]
    assert shape["value"].startswith("外套：")
    assert "；连衣裙：" in shape["value"]
    assert "S1" in shape["resource_ids"]
    # 连衣裙“腰部要宽松”不能被匹配成收腰大A摆（S4）；匹配不到就明确无出处
    assert "S4" not in shape["resource_ids"]
    assert "连衣裙：暂无版型资源出处" in shape["value"]
    assert shape["origin"] == "无出处"

    length = fields["长度比例"]
    assert "外套：衣长至臀中" in length["value"]
    assert "连衣裙：裙长至脚踝" in length["value"]
    assert {"L1", "L2"} <= set(length["resource_ids"])

    fabric = fields["面料"]
    assert "外套：" in fabric["value"] and "；连衣裙：" in fabric["value"]
    assert "F3" in fabric["resource_ids"]
    assert "F5" in fabric["resource_ids"]

    ease = fields["松弛余量"]
    assert "外套：整体围度宽松" in ease["value"]
    assert "连衣裙：裙摆放量大" in ease["value"]

    for name in FIELDS:
        for rid in fields[name]["resource_ids"]:
            assert _resource_ok(rid), rid


def test_multi_garment_does_not_copy_outerwear_shoulder_and_sleeve_rules_to_dress(
    client, real_mode
) -> None:
    text = (
        "想定制外套和连衣裙两件套，整体宽松，坐站和抬手都方便，"
        "里面要搭配内搭，不能紧身"
    )
    install([{
        "utterances": [
            {
                "text": "想定制外套和连衣裙两件套",
                "type": "品类",
                "strength": "硬约束",
                "mappings": [
                    {"field": "品类", "garment": "外套", "status": "已落实", "note": ""},
                    {"field": "品类", "garment": "连衣裙", "status": "已落实", "note": ""},
                ],
            },
            {
                "text": "整体宽松",
                "type": "松量",
                "strength": "硬约束",
                "mappings": [
                    {"field": "版型结构", "status": "已落实", "note": ""},
                    {"field": "松弛余量", "status": "已落实", "note": ""},
                ],
            },
            {
                "text": "坐站和抬手都方便",
                "type": "活动",
                "strength": "硬约束",
                "mappings": [{"field": "松弛余量", "status": "已落实", "note": ""}],
            },
            {
                "text": "里面要搭配内搭",
                "type": "内搭",
                "strength": "硬约束",
                "mappings": [{"field": "松弛余量", "status": "已落实", "note": "", "rule_id": "R8"}],
            },
            {
                "text": "不能紧身",
                "type": "松量",
                "strength": "硬约束",
                "mappings": [{"field": "松弛余量", "status": "已落实", "note": ""}],
            },
        ]
    }])
    session = create(client, {"text": text}).json()
    shape = session["spec_versions"][0]["fields"]["版型结构"]["value"]
    ease = session["spec_versions"][0]["fields"]["松弛余量"]["value"]
    dress_shape = shape.split("连衣裙：", 1)[1]
    dress_ease = ease.split("连衣裙：", 1)[1]
    assert "肩型" not in dress_shape
    assert "袖管放量" not in dress_ease
    assert "为内搭预留放量" not in dress_ease


# ---------------------------------------------------------------- 提示词与留出


def test_from_zero_prompt_never_mentions_holdout() -> None:
    from app.pipeline.extract import build_from_zero_prompt

    prompt = build_from_zero_prompt("想要一件外套")
    assert "c5" not in prompt
    assert "案例五" not in prompt
    assert "梅花" not in prompt


def test_hard_pattern_found_in_simulated_library(client, real_mode) -> None:
    """模拟资源库打开时，梅花能对上模拟库条目，来源记为 sim，不冒充上服案例。"""
    install([HARD_PLUM_OUTPUT])
    session = create(client, {"text": HARD_PLUM_TEXT}).json()
    field = session["spec_versions"][0]["fields"]["装饰纹样"]

    assert field["origin"] == "资源库"
    assert "梅花" in field["value"]
    assert field["resource_ids"]
    for rid in field["resource_ids"]:
        assert knowledge.resource_by_id(rid)["sources"] == ["sim"]

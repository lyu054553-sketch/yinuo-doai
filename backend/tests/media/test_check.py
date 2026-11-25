"""C2 单元测试：先解构、再由程序比对的核对逻辑、核对写回会话。

不连接真实视觉服务；`deconstruct` 全部用假 transport（fixture）替换。
真实图检查在 `test_check_live.py`（视觉服务没起时自动跳过）与 `check_c2.py`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.media import check, prompts, vision_client
from app.schemas import (
    FIELD_ORDER,
    GeneratedImage,
    Mapping,
    Session,
    SpecField,
    SpecVersion,
    Utterance,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"


# ------------------------------------------------------------ 数据


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def spec_version(case_id: str, version: int = 1) -> dict:
    case = load_case(case_id)
    return {
        "version": version,
        "created_at": "2026-11-18T09:00:00+08:00",
        "reason": "导入",
        "fields": {
            field: {
                "value": prompts.field_text(case["spec"].get(field, "")),
                "resource_ids": [],
                "utterance_ids": [],
                "origin": "已有方案",
                "confirmed": False,
            }
            for field in prompts.FIELD_ORDER
        },
    }


def c6_like_fields(**overrides) -> dict:
    """案例六成衣图（c6_garment.jpeg）的机器结果形态：交领、无扣、暗花。"""
    fields = {
        "collar_family": {"value": "交领", "confidence": "确定"},
        "front_closure": {"value": "无扣开襟", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "确定"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "确定"},
        "pattern": {"value": "暗花细密，低调", "confidence": "疑似"},
        "colors": {"value": ["藏青"], "confidence": "确定"},
    }
    fields.update(overrides)
    return fields


def c1_like_fields(**overrides) -> dict:
    """案例一成衣图（c1_garment.jpeg）的机器结果形态：立领、无花纹。"""
    fields = {
        "collar_family": {"value": "立领", "confidence": "确定"},
        "front_closure": {"value": "交叠闭合", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "疑似"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "确定"},
        "pattern": {"value": "无明显花纹", "confidence": "疑似"},
        "colors": {"value": ["黑"], "confidence": "确定"},
    }
    fields.update(overrides)
    return fields


def make_result(fields: dict, model: str = "qwen2.5vl:7b") -> vision_client.DeconstructResult:
    return vision_client.DeconstructResult(fields=fields, model=model, diagnostics={})


@pytest.fixture
def fake_vision(monkeypatch):
    """假 transport：返回预设字段，并记录传给视觉服务的参数（用于证明没传方案）。"""

    class Transport:
        def __init__(self):
            self.calls: list[tuple[tuple, dict]] = []
            self.fields = c6_like_fields()
            self.model = "qwen2.5vl:7b"
            self.error: Exception | None = None

        def __call__(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if self.error is not None:
                raise self.error
            return make_result(self.fields, self.model)

    transport = Transport()
    monkeypatch.setattr(vision_client, "deconstruct", transport)
    return transport


def compare_by_target(items: list[dict], target_fields: list[tuple[str, str]]) -> dict:
    """核对项没有 target 键（约定 CheckItem 只有五键），按顺序与核对清单一一对应。"""
    specs = prompts.build_check_specs(spec_version("c6"))
    assert [spec["target"] for spec in specs] == [t for t, _ in target_fields]
    return {t: item for (t, _), item in zip(target_fields, items)}


C6_TARGETS = [
    ("collar_family", "领型"),
    ("front_closure", "版型结构"),
    ("buttons_visible", "版型结构"),
    ("pattern", "装饰纹样"),
    ("length", "长度比例"),
    ("silhouette", "版型结构"),
    ("colors", "色彩"),
]


# ------------------------------------------------------------ 纹样归一


def test_observe_pattern_none_markers_win():
    assert check.observe_pattern("无明显花纹", "疑似") == "无可见纹样"
    assert check.observe_pattern("无可见花纹", "疑似") == "无可见纹样"
    assert check.observe_pattern("素色", "疑似") == "无可见纹样"


def test_observe_pattern_present_markers():
    assert check.observe_pattern("暗花细密，低调", "疑似") == "可见纹样"
    assert check.observe_pattern("提花宋锦", "疑似") == "可见纹样"


def test_observe_pattern_negated_present_marker_is_none():
    # “无可见花纹”里同时含“花纹”，先按“无”类标记归为无纹样
    assert check.observe_pattern("看不到花纹", "疑似") == "无可见纹样"
    # 否定词紧挨“图案”：不算看到图案
    assert check.observe_pattern("无可见图案", "疑似") == "无可见纹样"


def test_observe_pattern_prefers_unnegated_marker():
    """“不是明显的花纹，但局部有刺绣”仍然算有纹样。"""
    assert check.observe_pattern("不是明显的花纹，但局部有刺绣", "疑似") == "可见纹样"


def test_observe_pattern_ignores_stray_negation_chars():
    """“不错的暗纹”里的“不”不是在否定纹样，不能误判成无纹样。"""
    assert check.observe_pattern("不错的暗纹，很精致", "疑似") == "可见纹样"
    rules = check.load_check_rules()["pattern_observation"]
    assert "不" not in rules["negation_prefixes"]
    assert "没" not in rules["negation_prefixes"]


def test_observe_pattern_unknown_returns_none():
    assert check.observe_pattern("看不出", "看不出") is None
    assert check.observe_pattern("面料质感偏哑光", "疑似") is None
    assert check.observe_pattern("", "疑似") is None


# ------------------------------------------------------------ 比对：符合


def test_c6_fields_match_c6_spec():
    """c6 图对照 c6 方案：交领、无扣、暗花，四项符合。"""
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields())
    by_target = compare_by_target(items, C6_TARGETS)

    assert by_target["collar_family"]["verdict"] == "符合"
    assert by_target["collar_family"]["observed"] == "交领"
    # 交叠闭合的备选“无扣开襟”也算符合
    assert by_target["front_closure"]["verdict"] == "符合"
    assert by_target["buttons_visible"]["verdict"] == "符合"
    assert by_target["buttons_visible"]["observed"] == "无外露纽扣"
    assert by_target["pattern"]["verdict"] == "符合"
    assert by_target["pattern"]["observed"] == "可见纹样"


def test_items_shape_and_order_follow_check_specs():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields())
    assert len(items) == 7
    assert [item["field"] for item in items] == [spec["field"] for spec in specs]
    assert [item["expect"] for item in items] == [spec["expect"] for spec in specs]
    for item in items:
        assert set(item) == {"field", "expect", "observed", "verdict", "confidence"}
        assert item["verdict"] in check.VERDICTS
        assert item["confidence"] in {"确定", "疑似", "看不出"}


# ------------------------------------------------------------ 比对：不符合


def test_c1_fields_against_c6_spec():
    """c1 图（立领、无花纹）对照 c6 方案：领型、纹样不符合；无扣仍然符合。"""
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c1_like_fields())
    by_target = compare_by_target(items, C6_TARGETS)

    assert by_target["collar_family"]["verdict"] == "不符合"
    assert by_target["collar_family"]["observed"] == "立领"
    assert by_target["pattern"]["verdict"] == "不符合"
    assert by_target["pattern"]["observed"] == "无可见纹样"
    # 纽扣：c1 图看不到外露纽扣，与 c6“不应看到纽扣”一致 → 符合
    assert by_target["buttons_visible"]["verdict"] == "符合"


def test_visible_buttons_against_no_button_spec():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields(buttons_visible={"value": True, "confidence": "疑似"}))
    by_target = compare_by_target(items, C6_TARGETS)
    assert by_target["buttons_visible"]["verdict"] == "不符合"
    assert by_target["buttons_visible"]["observed"] == "有外露纽扣"


def test_closure_outside_expectation_and_alternatives():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields(front_closure={"value": "外露扣", "confidence": "疑似"}))
    by_target = compare_by_target(items, C6_TARGETS)
    assert by_target["front_closure"]["verdict"] == "不符合"


# ------------------------------------------------------------ 比对：看不出


def test_unseen_observation_never_counts_as_match():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields(collar_family={"value": "看不出", "confidence": "看不出"}))
    by_target = compare_by_target(items, C6_TARGETS)
    item = by_target["collar_family"]
    assert item["verdict"] == "看不出"
    assert item["confidence"] == "看不出"
    assert item["observed"] == "看不出"


def test_unclassifiable_pattern_is_unseen_not_match():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields(pattern={"value": "纹理很浅，说不清", "confidence": "疑似"}))
    by_target = compare_by_target(items, C6_TARGETS)
    item = by_target["pattern"]
    assert item["verdict"] == "看不出"
    assert item["observed"] == "纹理很浅，说不清"


def test_spec_without_expectation_cannot_be_judged():
    """案例二方案没写前襟闭合/纽扣，模型看出来了也不给结论。"""
    specs = prompts.build_check_specs(spec_version("c2"))
    items = check.compare(specs, c6_like_fields())
    closure = items[1]
    buttons = items[2]
    assert closure["verdict"] == "看不出"
    assert buttons["verdict"] == "看不出"
    assert closure["observed"] == "无扣开襟"  # 看到什么照实写，但不判符合


def test_manual_confirm_fields_always_unseen():
    """衣长/廓形/颜色即使模型给出“确定”，也一律看不出 + 需人工确认。"""
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields())
    for target, index in (("length", 4), ("silhouette", 5), ("colors", 6)):
        item = items[index]
        assert item["verdict"] == "看不出", target
        assert item["confidence"] == "看不出", target
        assert item["observed"] == "需人工确认", target
        assert item["field"] == prompts.load_mappings()["vision_keys"][target]["field"]
    # 判断只发生在冻结策略允许的四项
    for item in items[:4]:
        assert item["observed"] != "需人工确认"


def test_confidence_capped_at_suspected():
    specs = prompts.build_check_specs(spec_version("c6"))
    items = check.compare(specs, c6_like_fields())  # collar/buttons 原为“确定”
    assert items[0]["confidence"] == "疑似"
    assert items[2]["confidence"] == "疑似"


def test_missing_key_in_fields_is_unseen():
    specs = prompts.build_check_specs(spec_version("c6"))
    fields = c6_like_fields()
    del fields["collar_family"]
    items = check.compare(specs, fields)
    assert items[0]["verdict"] == "看不出"


# ------------------------------------------------------------ check_image


def test_check_image_shape_and_validation(fake_vision):
    result = check.check_image("data:image/jpeg;base64,AAAA", spec_version("c6"))
    assert set(result) == {"items", "model"}
    assert result["model"] == "qwen2.5vl:7b"
    assert len(result["items"]) == 7
    for item in result["items"]:
        assert set(item) == {"field", "expect", "observed", "verdict", "confidence"}
        assert item["field"] in FIELD_ORDER


def test_check_image_sends_only_the_image_to_vision(fake_vision):
    """核对不能把方案规格交给模型：调用里只有图片和 timeout/vision_url。

    禁止外传的内容 = 原始方案（`spec_version`）+ 由它生成的核对清单
    （`build_check_specs`）。后者里的“不应看到纽扣”“可见纹样”等措辞不来自原始方案，
    但它们同样是“方案信息的转写”，一样不能进模型请求。
    """
    spec = spec_version("c6")
    check_specs = prompts.build_check_specs(spec)
    check.check_image("data:image/jpeg;base64,AAAA", spec)
    assert len(fake_vision.calls) == 1
    args, kwargs = fake_vision.calls[0]
    # 传输的只有图片本身，其余只能是 timeout / vision_url
    assert args == ("data:image/jpeg;base64,AAAA",)
    assert set(kwargs) <= {"vision_url", "timeout"}
    # 原始方案 + 核对清单确实含这些字样（证明它们是“该禁的东西”），
    # 但它们一个都不能出现在调用参数里。
    forbidden_source = json.dumps(
        {"spec": spec, "check_specs": check_specs}, ensure_ascii=False
    )
    call_text = json.dumps([args, kwargs], ensure_ascii=False)
    for needle in ("交领", "暗花", "不应看到纽扣", "可见纹样"):
        assert needle in forbidden_source, needle
        assert needle not in call_text, needle


def test_check_image_is_deterministic(fake_vision):
    first = check.check_image("data:image/jpeg;base64,AAAA", spec_version("c6"))
    second = check.check_image("data:image/jpeg;base64,AAAA", spec_version("c6"))
    assert first == second


def test_check_image_propagates_service_unavailable(fake_vision):
    fake_vision.error = vision_client.VisionServiceUnavailable(vision_client.UNAVAILABLE_MESSAGE)
    with pytest.raises(vision_client.VisionServiceUnavailable):
        check.check_image("data:image/jpeg;base64,AAAA", spec_version("c6"))


# ------------------------------------------------------------ 会话与效果图


def make_session(images: list[GeneratedImage] | None = None, versions: int = 2) -> Session:
    text = "想要定制一件外套，交领，不要扣子"
    spec_fields = {f: SpecField(value="", origin="无出处") for f in FIELD_ORDER}
    spec_fields["领型"] = SpecField(value="交领开襟款式", origin="已有方案")
    spec_fields["装饰纹样"] = SpecField(value="暗花小巧，无外露纽扣装饰", origin="已有方案")
    spec_versions = [
        SpecVersion(
            version=index + 1,
            created_at="2026-11-18T10:00:00+00:00",
            reason="导入" if index == 0 else "客户确认",
            fields=dict(spec_fields),
        )
        for index in range(versions)
    ]
    return Session(
        id="s1234567890ab",
        title="C2 核对",
        mode="审计已有方案",
        source="mock",
        created_at="2026-11-18T10:00:00+00:00",
        input={"text": text, "existing_spec": None, "sample_id": None},
        utterances=[
            Utterance(
                id="u1",
                text=text,
                start=0,
                end=len(text),
                type="描述",
                strength="硬约束",
                status="已落实",
                mappings=[Mapping(field="领型", status="已落实")],
            )
        ],
        spec_versions=spec_versions,
        images=images or [],
    )


def make_image(image_id: str = "img1", version: int = 2) -> GeneratedImage:
    return GeneratedImage(
        id=image_id,
        version=version,
        kind="成衣挂拍",
        prompt="成衣挂拍照片：……",
        url="/files/generated/img1.png",
        provider="none",
        check=None,
    )


def test_check_generated_image_uses_current_version(fake_vision):
    session = make_session([make_image()])
    result = check.check_generated_image(session, "data:image/jpeg;base64,AAAA")
    assert len(result["items"]) == 7
    assert session.images[0].check is None  # 只核对，不写会话


def test_check_generated_image_explicit_version(fake_vision):
    session = make_session([make_image(version=1)], versions=3)
    result = check.check_generated_image(session, "data:image/jpeg;base64,AAAA", version=1)
    assert set(result) == {"items", "model"}


def test_check_generated_image_missing_version_raises(fake_vision):
    session = make_session([make_image()])
    with pytest.raises(check.CheckError):
        check.check_generated_image(session, "data:image/jpeg;base64,AAAA", version=9)


def test_record_check_writes_image_and_ledger():
    session = make_session([make_image()])
    payload = {
        "items": check.compare(
            prompts.build_check_specs(spec_version("c6")), c1_like_fields()
        ),
        "model": "qwen2.5vl:7b",
    }
    returned = check.record_check(session, "img1", payload, at="2026-11-18T10:05:00+00:00")

    assert returned is session
    stored = session.images[0].check
    assert stored is not None
    assert stored.model == "qwen2.5vl:7b"
    assert [item.field for item in stored.items] == [item["field"] for item in payload["items"]]

    entry = session.ledger[-1]
    assert entry.action == "核对"
    assert entry.version == 2  # 用效果图依据的方案版本
    assert entry.at == "2026-11-18T10:05:00+00:00"
    assert "不符合 2 项" in entry.summary
    assert "领型" in entry.summary and "装饰纹样" in entry.summary
    assert entry.changed_fields == ["领型", "装饰纹样"]
    assert "建议" in entry.detail
    assert "视觉模型独立看图" in entry.detail


def test_record_check_accepts_object_and_image_instance():
    session = make_session([make_image()])
    from app.schemas import CheckResult

    result = CheckResult.model_validate(
        {"items": check.compare(prompts.build_check_specs(spec_version("c6")), c6_like_fields()),
         "model": "qwen2.5vl:7b"}
    )
    check.record_check(session, session.images[0], result, at="2026-11-18T10:05:00+00:00")
    assert session.images[0].check == result
    assert session.ledger[-1].changed_fields == []


def test_record_check_rejects_unknown_image():
    session = make_session([make_image()])
    payload = {"items": [], "model": "x"}
    with pytest.raises(check.CheckError):
        check.record_check(session, "nope", payload)
    assert session.ledger == []


def test_record_check_rejects_invalid_payload():
    session = make_session([make_image()])
    bad = {
        "items": [
            {
                "field": "领型",
                "expect": "交领",
                "observed": "立领",
                "verdict": "差不多",
                "confidence": "疑似",
            }
        ],
        "model": "x",
    }
    with pytest.raises(ValidationError):
        check.record_check(session, "img1", bad)
    assert session.images[0].check is None
    assert session.ledger == []


def test_check_and_record_end_to_end(fake_vision):
    fake_vision.fields = c1_like_fields()
    session = make_session([make_image()])
    returned = check.check_and_record(
        session, "img1", "data:image/jpeg;base64,AAAA", at="2026-11-18T10:05:00+00:00"
    )
    assert returned is session
    stored = session.images[0].check
    assert stored is not None
    verdicts = {(item.field, item.verdict) for item in stored.items}
    assert ("领型", "不符合") in verdicts
    assert ("长度比例", "看不出") in verdicts
    assert session.ledger[-1].action == "核对"
    # 传参里没有方案文字
    for needle in ("交领", "暗花"):
        assert needle not in json.dumps(list(fake_vision.calls), ensure_ascii=False)


def test_needs_redraw():
    matched = {"items": check.compare(prompts.build_check_specs(spec_version("c6")), c6_like_fields())}
    mismatched = {"items": check.compare(prompts.build_check_specs(spec_version("c6")), c1_like_fields())}
    assert check.needs_redraw(matched) is False
    assert check.needs_redraw(mismatched) is True


# ------------------------------------------------------------ 数据表口径


def test_rules_data_table():
    rules = check.load_check_rules()
    assert set(rules["verdict_labels"].values()) == set(check.VERDICTS)
    mappings = prompts.load_mappings()
    assert rules["manual_targets"] == [
        key for key, meta in mappings["vision_keys"].items() if not meta["used"]
    ]
    assert check.JUDGED_TARGETS == tuple(
        key for key, meta in mappings["vision_keys"].items() if meta["used"]
    )
    assert rules["redraw_on_verdict"] == "不符合"
    for marker in rules["pattern_observation"]["present_markers"]:
        assert isinstance(marker, str) and marker
    for marker in rules["pattern_observation"]["none_markers"]:
        assert isinstance(marker, str) and marker
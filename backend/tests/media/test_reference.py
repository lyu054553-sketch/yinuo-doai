"""C1 单元测试：冻结策略、冲突检测、台账结构、analyze_reference 组装。

不连接真实视觉服务；真实图检查在 test_reference_live.py（服务没起时自动跳过）。
"""

from __future__ import annotations

import pytest

from app.media import reference, vision_client
from app.schemas import (
    FIELD_ORDER,
    Mapping,
    Session,
    SpecField,
    SpecVersion,
    Utterance,
)

# ------------------------------------------------------------ 数据


def c1_fields(collar: str = "立领", collar_conf: str = "确定", **overrides) -> dict:
    """参考图为立领的新中式外套（data/images/c1_garment.jpeg 的机器结果形态）。"""
    fields = {
        "collar_family": {"value": collar, "confidence": collar_conf},
        "front_closure": {"value": "交叠闭合", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "疑似"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "疑似"},
        "pattern": {"value": "无可见花纹", "confidence": "疑似"},
        "colors": {"value": ["黑", "灰"], "confidence": "疑似"},
    }
    fields.update(overrides)
    return fields


def utterance(uid: str, text: str, field: str = "领型") -> dict:
    return {
        "id": uid,
        "text": text,
        "start": 0,
        "end": len(text),
        "type": "领型",
        "strength": "硬约束",
        "status": "已落实",
        "mappings": [{"field": field, "status": "已落实", "note": ""}],
        "resolved_by": None,
    }


def make_result(fields: dict, model: str = "qwen2.5vl:7b") -> vision_client.DeconstructResult:
    return vision_client.DeconstructResult(fields=fields, model=model, diagnostics={})


def make_session(text: str, utterance_texts: list[tuple[str, str]] | None = None) -> Session:
    pairs = utterance_texts or []
    utterances = [
        Utterance(
            id=f"u{i + 1}",
            text=t,
            start=0,
            end=len(t),
            type="领型",
            strength="硬约束",
            status="已落实",
            mappings=[Mapping(field="领型", status="已落实")],
        )
        for i, (_, t) in enumerate(pairs)
    ]
    spec_fields = {f: SpecField(value="", origin="无出处") for f in FIELD_ORDER}
    spec_fields["领型"] = SpecField(value="立领", origin="已有方案")
    return Session(
        id="s1234567890ab",
        title="测试",
        mode="审计已有方案",
        source="mock",
        created_at="2026-11-18T10:00:00+00:00",
        input={"text": text, "existing_spec": None, "sample_id": None},
        utterances=utterances,
        spec_versions=[
            SpecVersion(
                version=2,
                created_at="2026-11-18T10:01:00+00:00",
                reason="客户确认",
                fields=spec_fields,
            )
        ],
    )


# ------------------------------------------------------------ 冻结策略


def test_freeze_caps_confidence_at_suspected():
    out = reference.build_reference_fields(c1_fields())
    assert out["领型"] == {"value": "立领", "confidence": "疑似", "used": True}
    assert out["前襟闭合"]["confidence"] == "疑似"
    assert out["纽扣"] == {"value": False, "confidence": "疑似", "used": True}
    assert out["纹样"]["confidence"] == "疑似"


def test_freeze_keeps_unused_fields_unjudged():
    out = reference.build_reference_fields(c1_fields())
    for label in ("衣长", "廓形", "颜色"):
        assert out[label] == {"value": "看不出", "confidence": "看不出", "used": False}, label
    # 颜色在原始结果里是列表，裁剪后不能漏出列表
    assert isinstance(out["颜色"]["value"], str)


def test_freeze_marks_uncertain_value_as_unseen():
    out = reference.build_reference_fields(c1_fields(collar="看不出", collar_conf="看不出"))
    assert out["领型"] == {"value": "看不出", "confidence": "看不出", "used": True}


def test_freeze_covers_every_mapped_key():
    out = reference.build_reference_fields(c1_fields())
    assert set(out) == {"领型", "前襟闭合", "纽扣", "纹样", "衣长", "廓形", "颜色"}


# ------------------------------------------------------------ 冲突检测


def test_conflict_standing_collar_vs_low_collar_request():
    """C1 验收场景：立领参考图 + “脖子短，领子不能太高”。"""
    conflicts = reference.detect_conflicts(
        c1_fields(), [utterance("u1", "脖子短，领子不能太高")]
    )
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["field"] == "领型"
    assert conflict["utterance_ids"] == ["u1"]
    assert "立领" in conflict["message"] and "领高" in conflict["message"]
    assert "脖子短，领子不能太高" in conflict["message"]


def test_conflict_visible_buttons_vs_no_button_request():
    fields = c1_fields(buttons_visible={"value": True, "confidence": "疑似"})
    conflicts = reference.detect_conflicts(fields, [utterance("u2", "不要扣子，要暗门襟", "版型结构")])
    assert [c["field"] for c in conflicts] == ["版型结构"]
    assert "外露纽扣" in conflicts[0]["message"]


def test_conflict_lapel_vs_chinese_standing_collar():
    fields = c1_fields(collar="翻领")
    conflicts = reference.detect_conflicts(fields, [utterance("u3", "想要中式立领")])
    assert [c["field"] for c in conflicts] == ["领型"]
    assert "翻领" in conflicts[0]["message"]


def test_conflict_no_pattern_vs_dark_pattern_request():
    conflicts = reference.detect_conflicts(
        c1_fields(pattern={"value": "无明显花纹", "confidence": "疑似"}),
        [utterance("u4", "面料要有暗纹", "装饰纹样")],
    )
    assert [c["field"] for c in conflicts] == ["装饰纹样"]
    assert "暗纹" in conflicts[0]["message"]


def test_conflict_no_pattern_even_when_model_uncertain():
    """纹样“看不出”也代表看不到花纹，客户要暗纹时应提示。"""
    conflicts = reference.detect_conflicts(
        c1_fields(pattern={"value": "看不出", "confidence": "看不出"}),
        [utterance("u4", "要有暗花", "装饰纹样")],
    )
    assert len(conflicts) == 1


def test_no_conflict_when_requirement_is_negated():
    fields = c1_fields(collar="翻领")
    assert reference.detect_conflicts(fields, [utterance("u5", "不要立领")]) == []


def test_no_conflict_when_collar_is_unseen():
    fields = c1_fields(collar="看不出", collar_conf="看不出")
    assert reference.detect_conflicts(fields, [utterance("u1", "脖子短，领子不能太高")]) == []


def test_no_conflict_for_unrelated_utterance():
    assert reference.detect_conflicts(c1_fields(), [utterance("u6", "面料要垂感好", "面料")]) == []


def test_unused_fields_never_produce_conflicts():
    """衣长/廓形/颜色不采用模型结果，因此不对它们报冲突。"""
    fields = c1_fields()
    for rule in reference.load_conflict_rules()["rules"]:
        key = rule["observed"]["key"]
        assert key in {"collar_family", "front_closure", "buttons_visible", "pattern"}, rule["id"]


def test_conflict_rules_have_required_shape():
    data = reference.load_conflict_rules()
    ids = {rule["id"] for rule in data["rules"]}
    assert {"RC-领高", "RC-领型-翻领对立领", "RC-纽扣-不要扣", "RC-纹样-参考图无纹客户要暗纹"} <= ids
    for rule in data["rules"]:
        assert rule["field"] in FIELD_ORDER
        assert rule["requirement"]["keywords"]
        assert "{utterance_text}" in rule["message"]


# ------------------------------------------------------------ 组装与台账


def test_build_reference_analysis_shape():
    analysis = reference.build_reference_analysis(
        image_url="/files/reference/c1.jpeg",
        result=make_result(c1_fields()),
        utterances=[utterance("u1", "脖子短，领子不能太高")],
    )
    assert set(analysis) == {"image_url", "fields", "conflicts", "model"}
    assert analysis["image_url"] == "/files/reference/c1.jpeg"
    assert analysis["model"] == "qwen2.5vl:7b"
    assert analysis["conflicts"][0]["field"] == "领型"


def test_ledger_entry_records_conflicts():
    analysis = reference.build_reference_analysis(
        image_url="/files/reference/c1.jpeg",
        result=make_result(c1_fields()),
        utterances=[utterance("u1", "脖子短，领子不能太高")],
    )
    entry = reference.build_ledger_entry(analysis, version=2, at="2026-11-18T10:02:00+00:00")
    assert entry["action"] == "参考图解析"
    assert entry["version"] == 2
    assert entry["utterance_ids"] == ["u1"]
    assert entry["changed_fields"] == ["领型"]
    assert "1 处与原话冲突" in entry["summary"]
    assert "需人工确认" in entry["detail"]


def test_ledger_entry_without_conflicts():
    analysis = reference.build_reference_analysis(
        image_url="/files/reference/c1.jpeg",
        result=make_result(c1_fields()),
        utterances=[utterance("u6", "面料要垂感好", "面料")],
    )
    entry = reference.build_ledger_entry(analysis, version=1, at="2026-11-18T10:02:00+00:00")
    assert "未发现与原话冲突" in entry["summary"]
    assert entry["changed_fields"] == []
    assert entry["utterance_ids"] == []


# ------------------------------------------------------------ analyze_reference


def test_analyze_reference_writes_reference_and_ledger(monkeypatch):
    session = make_session("脖子短，领子不能太高", [("u1", "脖子短，领子不能太高")])
    monkeypatch.setattr(
        vision_client,
        "deconstruct",
        lambda *a, **k: make_result(c1_fields()),
    )

    result = reference.analyze_reference(
        session,
        "data:image/jpeg;base64,AAAA",
        "/files/reference/c1.jpeg",
        at="2026-11-18T10:02:00+00:00",
    )

    assert result is session
    assert session.reference is not None
    assert session.reference.image_url == "/files/reference/c1.jpeg"
    assert session.reference.fields["领型"].value == "立领"
    assert session.reference.fields["领型"].confidence == "疑似"
    assert session.reference.fields["衣长"].used is False
    assert [c.field for c in session.reference.conflicts] == ["领型"]
    assert len(session.ledger) == 1
    assert session.ledger[0].action == "参考图解析"
    assert session.ledger[0].version == 2
    assert session.ledger[0].utterance_ids == ["u1"]
    # 不改方案版本
    assert len(session.spec_versions) == 1
    assert session.spec_versions[0].version == 2


def test_analyze_reference_propagates_service_unavailable(monkeypatch):
    session = make_session("脖子短，领子不能太高", [("u1", "脖子短，领子不能太高")])

    def boom(*a, **k):
        raise vision_client.VisionServiceUnavailable(vision_client.UNAVAILABLE_MESSAGE)

    monkeypatch.setattr(vision_client, "deconstruct", boom)
    with pytest.raises(vision_client.VisionServiceUnavailable) as exc:
        reference.analyze_reference(session, "data:image/jpeg;base64,AAAA", "/files/x.jpeg")
    assert "视觉服务未启动" in str(exc.value)
    assert session.reference is None and session.ledger == []
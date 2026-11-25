"""设计师事项按实际工作合并，不丢失对应原话。"""

from app.pipeline.questions import build_plan
from app.schemas import FIELD_ORDER, Mapping, SpecField, Utterance


def test_collar_designer_item_combines_split_requirements() -> None:
    utterances = [
        Utterance(
            id=f"u{index}",
            text=text,
            start=0,
            end=len(text),
            type="领型",
            strength="硬约束",
            status="需设计师判断",
            mappings=[Mapping(field="领型", status="需设计师判断", note="核对领高和领宽")],
        )
        for index, text in enumerate(("领子不要太高", "要窄一点", "显得脖子细一些"), 1)
    ]
    fields = {
        field: SpecField(value="青果西装领" if field == "领型" else "", origin="已有方案")
        for field in FIELD_ORDER
    }
    items = build_plan(utterances, fields, {})["designer_items"]
    assert len(items) == 1
    assert items[0].utterance_ids == ["u1", "u2", "u3"]
    assert all(utterance.text in items[0].text for utterance in utterances)
    assert "领高与领宽" in items[0].text


def test_collar_designer_items_keep_garments_separate() -> None:
    utterances = [
        Utterance(
            id=f"u{index}",
            text=text,
            start=0,
            end=len(text),
            type="领型",
            strength="硬约束",
            status="需设计师判断",
            mappings=[
                Mapping(field="领型", garment=garment, status="需设计师判断")
            ],
        )
        for index, (text, garment) in enumerate(
            (("外套领子不要太高", "外套"), ("连衣裙领口要舒服", "连衣裙")), 1
        )
    ]
    fields = {
        field: SpecField(
            value="外套：中式斜襟领；连衣裙：简约小圆领" if field == "领型" else "",
            origin="已有方案",
        )
        for field in FIELD_ORDER
    }
    items = build_plan(utterances, fields, {})["designer_items"]
    assert len(items) == 2
    assert [item.utterance_ids for item in items] == [["u1"], ["u2"]]
    assert "外套的领型" in items[0].text
    assert "连衣裙的领型" in items[1].text

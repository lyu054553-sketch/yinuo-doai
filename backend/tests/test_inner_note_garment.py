"""多件套方案里，内搭说明要写进外套段，不能落到连衣裙段。"""

from app.pipeline.questions import _with_inner_note

TWO_PIECE = "外套：整体围度宽松；袖管放量充足；连衣裙：暂无松量资源出处，需设计师确认"


def test_note_goes_into_jacket_segment() -> None:
    result = _with_inner_note(TWO_PIECE, "为内搭预留放量，具体数值量体后确认")
    jacket, dress = result.split("连衣裙：")
    assert "为内搭预留放量" in jacket
    assert "内搭" not in dress


def test_no_duplicate_when_jacket_already_has_note() -> None:
    base = "外套：整体围度宽松；为内搭预留放量（具体数值量体后确认）；连衣裙：需设计师确认"
    assert _with_inner_note(base, "为内搭预留放量，具体数值量体后确认") == base


def test_single_garment_appends_at_end() -> None:
    assert _with_inner_note("整体围度宽松", "内搭建议另配薄款") == "整体围度宽松；内搭建议另配薄款"

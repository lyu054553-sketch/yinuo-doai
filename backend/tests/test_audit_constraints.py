"""审计程序复核的约束测试：全部用合成口述与假抽取，不调模型、不涉及留出案例。

覆盖：
* 否定极性（不要红色/不要梅花）：缺席不误报，出现在方案里才冲突；
* 方案里的否定写法（不含/未见）算作“方案里没有”；
* 允许替代（或/都可以）满足其一即可，明确组合（和/拼色）两个都要；
* 一句话里的多组纹样要求分别核对，不只看第一组；
* 暗纹≈暗花同义、软偏好被换判被改写、宽松与收腰冲突、领宽规则；
* 材质出处：已知名称/别名不报，未收录的具体材料名提示设计师核实，纯属性描述不报，
  已是冲突/被改写的不降级；
* ensure_mappings 不把否定要求补成正向映射，也不新增原话或改动方案取值。
"""

from __future__ import annotations

from app.pipeline import audit, mock
from app.schemas import CreateSessionRequest, Mapping, SpecField, Utterance

FIELD_NAMES = (
    "品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量",
)


def _fields(**values: str) -> dict[str, SpecField]:
    return {
        name: SpecField(
            value=values.get(name, ""),
            resource_ids=[],
            utterance_ids=[],
            origin="已有方案" if values.get(name) else "无出处",
        )
        for name in FIELD_NAMES
    }


def _utterance(
    text: str,
    mappings: list[tuple[str, str]] | None = None,
    *,
    strength: str = "软偏好",
) -> Utterance:
    pairs = [("装饰纹样", "已落实")] if mappings is None else mappings
    return Utterance(
        id="u1",
        text=text,
        start=0,
        end=len(text),
        type="纹样",
        strength=strength,
        status="已落实",
        mappings=[Mapping(field=field, status=status, note="") for field, status in pairs],
    )


def _mapping(utterance: Utterance, field: str) -> Mapping:
    return next(mapping for mapping in utterance.mappings if mapping.field == field)


# ---------------------------------------------------------------- 颜色


def test_negated_color_absent_is_not_flagged() -> None:
    utterance = _utterance("颜色要克制，不要红色", [("色彩", "已落实")])
    fields = _fields(色彩="米白底＋亮粉滚边")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "色彩").status == "已落实"
    assert fields["色彩"].value == "米白底＋亮粉滚边"


def test_negated_color_present_is_conflict() -> None:
    utterance = _utterance("不要红色", [("色彩", "已落实")])
    fields = _fields(色彩="正红色配金色滚边")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "色彩").status == "有冲突"


def test_plan_negated_color_counts_as_absent() -> None:
    utterance = _utterance("想要红色", [("色彩", "已落实")], strength="硬约束")
    fields = _fields(色彩="不含红色，以米白为主")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "色彩").status in ("被改写", "未落实")


def test_alternative_colors_satisfied_by_one() -> None:
    utterance = _utterance("米白或浅灰都可以", [("色彩", "已落实")])
    fields = _fields(色彩="米白底＋亮粉滚边")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "色彩").status == "已落实"


def test_alternative_colors_all_missing() -> None:
    utterance = _utterance("米白或浅灰都可以", [("色彩", "已落实")])
    fields = _fields(色彩="炭黑")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "色彩").status == "被改写"


def test_combo_colors_require_both() -> None:
    partial = _utterance("米白和深灰拼色", [("色彩", "已落实")])
    audit.apply_program_rules([partial], _fields(色彩="米白底"))
    assert _mapping(partial, "色彩").status == "被改写"

    complete = _utterance("米白和深灰拼色", [("色彩", "已落实")])
    audit.apply_program_rules([complete], _fields(色彩="米白与深灰拼色"))
    assert _mapping(complete, "色彩").status == "已落实"


def test_garment_mapping_only_checks_its_own_spec_segment() -> None:
    jacket = _utterance("外套要米白和深灰拼色", [("色彩", "已落实")])
    jacket.mappings[0].garment = "外套"
    fields = _fields(色彩="外套：米白和深灰拼色；连衣裙：亮粉色")
    audit.apply_program_rules([jacket], fields)
    assert _mapping(jacket, "色彩").status == "已落实"

    dress = _utterance("连衣裙要米白和深灰拼色", [("色彩", "已落实")])
    dress.mappings[0].garment = "连衣裙"
    audit.apply_program_rules([dress], fields)
    assert _mapping(dress, "色彩").status == "被改写"


def test_garment_waist_rule_does_not_borrow_other_garment() -> None:
    jacket = _utterance("外套整体宽松，不收腰", [("版型结构", "已落实")])
    jacket.mappings[0].garment = "外套"
    fields = _fields(版型结构="外套：H 型直身，无收腰；连衣裙：收腰大 A 摆")
    audit.apply_program_rules([jacket], fields)
    assert _mapping(jacket, "版型结构").status == "已落实"


# ---------------------------------------------------------------- 纹样


def test_multiple_pattern_groups_are_checked() -> None:
    ok = _utterance("不要梅花，四瓣花也可以", [("装饰纹样", "已落实")])
    audit.apply_program_rules([ok], _fields(装饰纹样="衣身四瓣花散点，低调含蓄"))
    assert _mapping(ok, "装饰纹样").status == "已落实"

    bad = _utterance("不要梅花，四瓣花也可以", [("装饰纹样", "已落实")])
    audit.apply_program_rules([bad], _fields(装饰纹样="衣身梅花点缀"))
    assert _mapping(bad, "装饰纹样").status == "有冲突"


def test_negated_pattern_absent_is_not_flagged() -> None:
    utterance = _utterance("不要梅花", [("装饰纹样", "已落实")])
    fields = _fields(装饰纹样="衣身植物暗花纹样，点缀细碎点状星光")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "装饰纹样").status == "已落实"


def test_plan_negated_pattern_counts_as_absent() -> None:
    utterance = _utterance("想要梅花暗纹", [("装饰纹样", "已落实")], strength="硬约束")
    fields = _fields(装饰纹样="未见可辨认的梅花，只有植物暗花")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "装饰纹样").status in ("被改写", "未落实")


def test_dark_pattern_synonym_is_not_false_positive() -> None:
    utterance = _utterance("面料要带一些暗纹", [("装饰纹样", "已落实")], strength="硬约束")
    fields = _fields(装饰纹样="衣身植物暗花纹样，点缀细碎点状星光")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "装饰纹样").status == "已落实"


def test_soft_preference_replaced_is_rewritten() -> None:
    utterance = _utterance("最好是格纹", [("装饰纹样", "已落实")])
    fields = _fields(装饰纹样="衣身植物暗花")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "装饰纹样").status == "被改写"


# ---------------------------------------------------------------- 原有规则保留


def test_hard_conflict_loose_versus_waist_tuck() -> None:
    utterance = _utterance(
        "整体宽松舒展一点", [("版型结构", "已落实")], strength="硬约束"
    )
    fields = _fields(版型结构="H 型直身结构，微微收腰")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "版型结构").status == "有冲突"


def test_negated_waist_tuck_is_not_conflict() -> None:
    utterance = _utterance("整体宽松一点，不收腰", [("版型结构", "已落实")])
    fields = _fields(版型结构="H 型直身结构，无收腰")
    audit.apply_program_rules([utterance], fields)
    assert _mapping(utterance, "版型结构").status == "已落实"
    assert audit.has_waist_tuck("H 型直身结构，无收腰") is False
    assert audit.has_waist_tuck("H 型直身结构，微微收腰") is True


def test_narrow_lead_upgraded_to_designer() -> None:
    utterance = _utterance(
        "要窄一点，显得脖子细一些", [("领型", "已落实")], strength="硬约束"
    )
    fields = _fields(领型="青果西装领，领型线条柔和舒展")
    audit.apply_program_rules([utterance], fields)
    mapping = _mapping(utterance, "领型")
    assert mapping.status == "需设计师判断"
    assert "领宽" in mapping.note


# ---------------------------------------------------------------- 材质出处


def _fabric_case(value: str, status: str = "已落实") -> tuple[Utterance, Mapping]:
    utterance = _utterance("这个面料要挺括一些", [("面料", status)])
    audit.apply_program_rules([utterance], _fields(面料=value))
    return utterance, _mapping(utterance, "面料")


def test_known_fabric_name_is_not_flagged() -> None:
    _, mapping = _fabric_case("提花宋锦")
    assert mapping.status == "已落实"


def test_fabric_alias_is_not_flagged() -> None:
    _, alias = _fabric_case("宋锦")
    assert alias.status == "已落实"
    _, keyword = _fabric_case("醋酸")
    assert keyword.status == "已落实"


def test_unknown_concrete_material_asks_designer() -> None:
    _, mapping = _fabric_case("意大利进口羊毛呢")
    assert mapping.status == "需设计师判断"
    assert "资源库暂无对应参考" in mapping.note
    assert "不存在" not in mapping.note
    assert "虚构" not in mapping.note


def test_pure_property_words_are_not_flagged() -> None:
    _, mapping = _fabric_case("哑光、有肌理、挺括")
    assert mapping.status == "已落实"


def test_unknown_fabric_only_marks_its_garment() -> None:
    dress = _utterance("连衣裙用亮面缎面", [("面料", "已落实")])
    dress.mappings[0].garment = "连衣裙"
    fields = _fields(面料="外套：意大利进口羊毛呢；连衣裙：高垂感亮面缎面")
    audit.apply_program_rules([dress], fields)
    assert _mapping(dress, "面料").status == "已落实"


def test_problem_fabric_mapping_is_not_downgraded() -> None:
    _, conflict = _fabric_case("意大利进口羊毛呢", status="有冲突")
    assert conflict.status == "有冲突"

    _, rewritten = _fabric_case("意大利进口羊毛呢", status="被改写")
    assert rewritten.status == "被改写"

    _, missing = _fabric_case("意大利进口羊毛呢", status="未落实")
    assert missing.status == "未落实"


# ---------------------------------------------------------------- 补映射与示例模式


def test_ensure_mappings_skips_negated_pattern() -> None:
    negated = _utterance("不要梅花", [])
    audit.ensure_mappings([negated], {"装饰纹样": "衣身植物暗花"})
    assert negated.mappings == []

    positive = _utterance("面料要带一些暗纹", [])
    audit.ensure_mappings([positive], {"装饰纹样": "衣身植物暗花"})
    assert [mapping.field for mapping in positive.mappings] == ["装饰纹样"]


def test_rules_do_not_touch_utterances_or_spec_values() -> None:
    utterance = _utterance("不要红色，米白或浅灰都可以", [("色彩", "已落实")])
    before_text = utterance.text
    fields = _fields(色彩="正红色")
    audit.apply_program_rules([utterance], fields)
    assert utterance.text == before_text
    assert fields["色彩"].value == "正红色"
    assert [mapping.field for mapping in utterance.mappings] == ["色彩"]


def test_mock_session_does_not_run_program_rules() -> None:
    """示例模式照抄标注：案例二的梅花仍是被改写，但不会被程序规则改成别的结论。"""
    session = mock.build_mock_session(CreateSessionRequest(sample_id="c2"))
    plum = next(u for u in session.utterances if u.text == "最好是梅花")
    plum_mapping = next(m for m in plum.mappings if m.field == "装饰纹样")
    assert plum_mapping.status == "被改写"
    assert "植物暗花" in plum_mapping.note
    assert session.source == "mock"

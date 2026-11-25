"""生成客户问题、设计师事项和“不问清单”（纯程序规则，不调大模型）。

原则：只问会改变当前方案的问题；所有选项都改不动方案的问题直接丢弃。
"""

from __future__ import annotations

import re

from .. import knowledge
from ..schemas import (
    DesignerItem,
    Option,
    Question,
    SkipItem,
    SpecField,
    Utterance,
)

FIELD_ORDER = knowledge.FIELD_ORDER
ASKABLE_FIELDS = ("装饰纹样", "色彩", "版型结构", "松弛余量", "面料")
PATTERN_TOKENS = (
    "梅花", "四瓣花", "格纹", "条纹", "波点", "星光", "暗花", "暗纹",
    "云纹", "祥云", "回纹", "牡丹", "竹叶", "仙鹤", "莲花", "荷花",
)
COLOR_TOKENS = (
    "亮粉", "粉色", "米白", "黑色", "白色", "红色", "蓝色", "绿色",
    "灰色", "银灰", "金色", "紫色", "黄色", "橙色", "咖色",
)

SKIP_REASONS = {
    "色彩": "客户没有提到颜色，就不写色彩要求，也不硬套资源库里唯一的色彩参考，追问不会改变方案。",
    "长度比例": "没有原话涉及衣长；资源库里有出处的案例都是“衣长至臀中”，按资源库里最常见的取值沿用，不追问。",
    "品类": "客户已经明确品类，没有可选项，不追问。",
}


# ---------------------------------------------------------------- 候选问题


def _collect_candidates(utterances: list[Utterance]) -> list[dict]:
    items: list[dict] = []
    taken_fields: set[str] = set()

    for order, utterance in enumerate(utterances):
        if utterance.resolved_by:
            continue
        for mapping in utterance.mappings:
            if mapping.status in ("有冲突", "被改写", "未落实") and mapping.field in ASKABLE_FIELDS:
                if mapping.field in taken_fields:
                    continue
                taken_fields.add(mapping.field)
                priority = 0 if mapping.status in ("被改写", "未落实") else 1
                items.append(
                    {
                        "utterance": utterance,
                        "mapping": mapping,
                        "field": mapping.field,
                        "priority": priority,
                        "order": order,
                    }
                )

    # 内搭不对应资源库，用规则模板生成问题
    for order, utterance in enumerate(utterances):
        if utterance.resolved_by:
            continue
        if not any(word in utterance.text for word in ("内搭", "里面要", "打底")):
            continue
        if "松弛余量" in taken_fields:
            continue
        taken_fields.add("松弛余量")
        items.append(
            {
                "utterance": utterance,
                "mapping": None,
                "field": "松弛余量",
                "priority": 2,
                "order": order,
            }
        )

    items.sort(key=lambda item: (item["priority"], item["order"]))
    return items


# ---------------------------------------------------------------- 选项


def _shoulder_tail(value: str) -> str:
    if "；肩型" in value:
        return "；肩型" + value.split("；肩型", 1)[1]
    if "肩型" in value:
        return "；" + value[value.index("肩型"):]
    return ""


def _compose_value(field: str, resource: dict, current: str) -> str:
    name = resource.get("name", "")
    if field == "装饰纹样":
        return f"衣身{name}纹样"
    if field == "色彩":
        return name
    if field == "面料":
        traits = resource.get("traits") or []
        suffix = "、".join(traits[:3])
        return f"优选{name}，{suffix}" if suffix else f"优选{name}"
    if field == "版型结构":
        return name + _shoulder_tail(current)
    return name


def _resource_options(
    field: str,
    spec_fields: dict[str, SpecField],
    spec_values: dict[str, str],
) -> list[Option]:
    current = spec_fields[field]
    options: list[Option] = []
    for resource in knowledge.resources_for_field(field):
        rid = resource["id"]
        if rid in current.resource_ids:
            continue
        if not knowledge.garment_allowed(resource, spec_values):
            continue
        patch_value = _compose_value(field, resource, current.value)
        if not patch_value or patch_value == current.value:
            continue
        resource_ids = [rid]
        if field == "版型结构":
            resource_ids += [i for i in current.resource_ids if i.startswith("SH")]
        note = (
            f"采用资源 {rid}「{resource.get('name', '')}」"
            f"（参考{knowledge.resource_source_text(rid)}）。"
        )
        if resource.get("caution"):
            note += f"注意：{resource['caution']}"
        options.append(
            Option(
                id="",
                label=resource.get("name", rid),
                detail=f"{rid} · 来源{knowledge.resource_source_text(rid)}",
                resource_ids=resource_ids,
                patch={field: patch_value},
                note=note,
                todo=None,
            )
        )
    return options


def _pattern_token(text: str) -> str | None:
    for token in PATTERN_TOKENS:
        if token in text:
            return token
    return None


def _color_token(text: str) -> str | None:
    for token in COLOR_TOKENS:
        if token in text:
            return token
    return None


def _persist_option(field: str, utterance: Utterance, current: str) -> Option | None:
    """“坚持原话要求”选项：没有资源出处，选中后产生待办。"""
    text = utterance.text
    if field == "装饰纹样":
        token = _pattern_token(text) or "原话中的纹样"
        value = f"衣身{token}纹样，含蓄点缀"
        return Option(
            id="",
            label=f"坚持原话要求：{token}",
            detail="无资源出处",
            resource_ids=[],
            patch={field: value},
            note=f"客户原话提到「{token}」，资源库里找不到可辨认的{token}，坚持原话就要另找面料。",
            todo=f"寻找{token}纹样面料，并与客户确认能否做出可辨认的{token}图案",
        )
    if field == "版型结构":
        value = "整体宽松舒展，腰部不收紧" + _shoulder_tail(current)
        return Option(
            id="",
            label="坚持原话要求：整体宽松、不收紧腰身",
            detail="无资源出处",
            resource_ids=[],
            patch={field: value},
            note="按客户原话保持整体宽松，不在腰部收紧；与案例里的微微收腰做法不同。",
            todo="打样时核对腰部松量是否足够，避免版型又回到收腰",
        )
    if field == "色彩":
        token = _color_token(text) or "原话中的颜色"
        return Option(
            id="",
            label=f"坚持原话要求：{token}",
            detail="无资源出处",
            resource_ids=[],
            patch={field: token},
            note=f"客户原话提到「{token}」，资源库里没有对应色彩参考。",
            todo=f"寻找{token}的面料或染色工艺，确认能否实现",
        )
    if field == "面料":
        return Option(
            id="",
            label="坚持原话要求：按原话的面料要求",
            detail="无资源出处",
            resource_ids=[],
            patch={field: f"按客户原话执行：{text}"},
            note="客户对面料有明确要求，案例资源里没有完全对应的面料。",
            todo="按客户原话另找面料，确认是否有真实样布",
        )
    return None


_GARMENT_HEAD = re.compile(r"(?:^|；)\s*([^；：]{1,6})：")


def _with_inner_note(base: str, addition: str, garment: str = "外套") -> str:
    """把内搭说明写进对应单品那一段。

    多件套方案写成“外套：……；连衣裙：……”，直接拼在末尾会落到最后一件（常是连衣裙）上。
    内搭是穿在外套里面的，所以写进外套段；那一段已经写了同样的话就不重复。
    """
    heads = list(_GARMENT_HEAD.finditer(base))
    target = next((m for m in heads if m.group(1).strip() == garment), None)
    if target is None:
        return f"{base}；{addition}"
    following = next((m for m in heads if m.start() > target.start()), None)
    end = following.start() if following else len(base)
    segment = base[target.end():end]
    if addition.split("，")[0] in segment:
        return base
    return f"{base[:end].rstrip('；')}；{addition}{base[end:]}"


def _inner_options(current: str) -> list[Option]:
    """内搭的处理方式：只写“为内搭预留放量”，具体厘米数量体后确认。

    资源库的身形规则 R8 只写了“整体围度宽松；建议明确写入为内搭预留放量”，
    没有任何胸围加放数值，所以这里不写“+4cm”这类没有出处的具体尺寸。
    """
    base = (current or "整体围度宽松").strip().rstrip("；")
    return [
        Option(
            id="",
            label="为内搭预留放量（具体数值量体后确认）",
            detail="规则模板 · 无资源出处",
            resource_ids=[],
            patch={"松弛余量": _with_inner_note(base, "为内搭预留放量，具体数值量体后确认")},
            note=(
                "客户明确要配内搭，方案只写了整体围度，没有单独写明为内搭预留放量；"
                "资源库里没有记录内搭放量的具体数值，这里只写“预留放量”，数值量体后确认。"
            ),
            todo="量体时按内搭厚度确认放量数值，打样时试穿核对",
        ),
        Option(
            id="",
            label="成衣围度不变，建议内搭另配薄款",
            detail="规则模板 · 无资源出处",
            resource_ids=[],
            patch={"松弛余量": _with_inner_note(base, "内搭建议另配薄款")},
            note="如果客户希望外套更利落，可保持现有围度，内搭改用薄款。",
            todo="为客户另配薄款内搭，确认颜色与领口",
        ),
        Option(
            id="",
            label="坚持原话要求：只写明“搭配内搭”，放量交打样确认",
            detail="无资源出处",
            resource_ids=[],
            patch={"松弛余量": _with_inner_note(base, "内搭放量由打样试穿时确认")},
            note=(
                "客户原话是“里面要搭配一件内搭”，方案应明确写入为内搭留出空间这件事，"
                "具体数值不写死。"
            ),
            todo="打样时按内搭厚度试穿，确认放量后再定纸样",
        ),
    ]


def _question_title(field: str, utterance: Utterance, mapping) -> str:
    if field == "装饰纹样":
        token = _pattern_token(utterance.text)
        return f"{token}暗纹怎么落实？" if token else "纹样选哪一种？"
    if field == "版型结构" and mapping is not None and mapping.status == "有冲突":
        return "腰身：微微收腰还是整体宽松？"
    if field == "色彩":
        return "颜色怎么定？"
    if field == "面料":
        return "面料换成哪一种？"
    if field == "松弛余量" and mapping is None:
        return "内搭要不要预留放量？"
    return f"{field}怎么定？"


def _build_question(item: dict, spec_fields: dict[str, SpecField]) -> Question | None:
    field = item["field"]
    utterance = item["utterance"]
    mapping = item["mapping"]
    current = spec_fields[field]
    spec_values = {name: sf.value for name, sf in spec_fields.items()}

    if mapping is None:
        options = _inner_options(current.value)
    else:
        options = _resource_options(field, spec_fields, spec_values)
        persist = _persist_option(field, utterance, current.value)
        if persist is not None:
            options.append(persist)

    # 只保留会改变当前方案的选项；一个都没有就不问
    options = [
        opt
        for opt in options
        if any(opt.patch.get(name, current.value) != spec_fields[name].value for name in opt.patch)
    ]
    if not options:
        return None
    for index, option in enumerate(options, 1):
        option.id = f"o{index}"

    return Question(
        id="",
        utterance_ids=[utterance.id],
        field=field,
        title=_question_title(field, utterance, mapping),
        context=(
            f"客户原话「{utterance.text}」，当前方案这一栏是"
            f"「{current.value or '未写'}」。"
        ),
        why=f"答案会改变【{field}】这一栏；选“坚持原话要求”还会留下一条待办。",
        options=options,
        answer=None,
    )


# ---------------------------------------------------------------- 设计师事项与不问清单


def _designer_text(field: str, utterance: Utterance, value: str) -> str:
    if field == "领型":
        return (
            f"客户原话「{utterance.text}」，方案只写了「{value or '未写'}」，"
            "没有写领宽。请设计师确定领宽尺寸，并判断是否达到“显脖子细”的效果。"
        )
    return (
        f"客户原话「{utterance.text}」，方案在【{field}】上的处理是「{value or '未写'}」，"
        "是否到位需要设计师判断。"
    )


def _designer_items(
    utterances: list[Utterance], spec_fields: dict[str, SpecField]
) -> list[DesignerItem]:
    items: list[DesignerItem] = []
    collar_groups: dict[str | None, list[Utterance]] = {}
    for utterance in utterances:
        for mapping in utterance.mappings:
            if mapping.status != "需设计师判断":
                continue
            field = mapping.field
            if field == "领型":
                group = collar_groups.setdefault(mapping.garment, [])
                if utterance not in group:
                    group.append(utterance)
                continue
            items.append(
                DesignerItem(
                    id=f"d{len(items) + 1}",
                    utterance_ids=[utterance.id],
                    field=field,
                    text=_designer_text(field, utterance, spec_fields[field].value),
                )
            )
    for garment, group in collar_groups.items():
        original = "、".join(f"「{utterance.text}」" for utterance in group)
        scope = f"{garment}的" if garment else ""
        items.append(
            DesignerItem(
                id=f"d{len(items) + 1}",
                utterance_ids=[utterance.id for utterance in group],
                field="领型",
                text=(
                    f"客户关于{scope}领型的原话{original}，方案只写了"
                    f"「{spec_fields['领型'].value or '未写'}」。请设计师核对领高与领宽，"
                    "并判断是否达到客户要求；具体尺寸由设计师确定。"
                ),
            )
        )
    return items


def _skipped(utterances: list[Utterance]) -> list[SkipItem]:
    mentioned = {m.field for u in utterances for m in u.mappings}
    items: list[SkipItem] = []
    for field in FIELD_ORDER:
        if field in mentioned:
            continue
        items.append(
            SkipItem(
                field=field,
                reason=SKIP_REASONS.get(
                    field, "没有原话单独涉及这一栏，按方案现有取值处理，不追问。"
                ),
            )
        )
    return items


def build_plan(utterances: list[Utterance], spec_fields: dict[str, SpecField], case: dict) -> dict:
    questions: list[Question] = []
    for item in _collect_candidates(utterances):
        question = _build_question(item, spec_fields)
        if question is None:
            continue
        questions.append(question)
    for index, question in enumerate(questions, 1):
        question.id = f"q{index}"

    return {
        "questions": questions,
        "designer_items": _designer_items(utterances, spec_fields),
        "skipped": _skipped(utterances),
    }

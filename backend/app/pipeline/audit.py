"""审计的程序复核：在大模型给出状态之后，用规则再检查一遍，只升级、不降级。

程序规则不替代大模型判断，只做几类“必须有确定结论”的复核：

1. 客户要“宽松/舒展/不收腰”，方案却写了“收腰”，判有冲突；
2. 纹样、色彩按**正负极性**和**组合方式**逐条核对：
   - “不要红色/不要梅花”是否定要求：方案里没有这个颜色/纹样是对的，
     方案里反而出现了才判有冲突；
   - 方案写“无红色/不含梅花”这类否定描述，算作“方案里没有”；
   - “米白或浅灰都可以”是允许替代，满足其中一个就算落实；
   - “米白和深灰拼色”是明确组合，两个都要有才算落实；
   - 一句话里有几组要求就核对几组，不只看第一组；
   - “暗纹”和“暗花”是同一类暗纹表达，互为同义，不能误报；
3. 客户对领宽提了要求（窄、显脖子细），方案只写了领型、没写领宽，判需设计师判断；
4. 已有方案的面料一栏写了具体材料名，却对应不上任何可推荐资源的名称/别名时，
   相关“已落实/间接体现”的面料映射提示需设计师判断“资源库暂无对应参考，请核实来源”。
   只限具体材料名；纯属性描述（哑光、有肌理、挺括等）不报。

所有规则都只把状态往更严重的方向改：已是“有冲突/被改写/未落实”的映射不会被降级，
也不会凭空新增客户原话或改动方案取值。

另外 `ensure_mappings` 会在模型漏掉关键映射时补一条中性映射，交给上面的规则判断，
避免整段结论因为漏了一条 mapping 而消失。这两个函数只服务真实大模型模式，
示例模式（标注即答案）不调用它们。
"""

from __future__ import annotations

import re

from .. import knowledge
from ..schemas import Mapping, SpecField, Status, Utterance, most_severe

LOOSE_WORDS = ("宽松", "松弛", "舒展", "无束缚", "不紧绷", "不收腰", "不要收腰", "别收腰")
WAIST_NEGATIONS = ("没有收腰", "不收腰", "不要收腰", "别收腰", "无收腰", "不收紧腰身")
PATTERN_TOKENS = (
    "梅花", "四瓣花", "格纹", "条纹", "波点", "星光", "点状星光", "暗花", "暗纹",
    "云纹", "祥云", "回纹", "牡丹", "竹叶", "竹纹", "仙鹤", "鹤纹", "莲花", "荷花",
)
# 同义/近义纹样归到一组：客户说组里任一个词，方案写了组里任一个词就算落实。
PATTERN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("梅花",),
    ("四瓣花",),
    ("格纹",),
    ("条纹",),
    ("波点",),
    ("星光", "点状星光"),
    ("暗花", "暗纹"),
    ("云纹", "祥云"),
    ("回纹",),
    ("牡丹",),
    ("竹叶", "竹纹"),
    ("仙鹤", "鹤纹"),
    ("莲花", "荷花"),
)
COLOR_TOKENS = (
    "亮粉", "粉色", "米白", "奶白", "象牙白", "黑色", "白色", "红色", "酒红",
    "蓝色", "深蓝", "浅蓝", "藏青", "绿色", "墨绿", "灰色", "银灰", "深灰",
    "浅灰", "金色", "香槟", "紫色", "黄色", "橙色", "咖色", "驼色", "卡其",
)
COLOR_GROUPS: tuple[tuple[str, ...], ...] = tuple((token,) for token in COLOR_TOKENS)
NARROW_LEAD_WORDS = ("领宽", "要窄", "窄一点", "窄一些", "脖子细", "脖颈细")
INNER_LAYER_WORDS = ("内搭", "打底", "里面要")

# 否定词与否定作用范围：只在一句话（到逗号/分号/句号为止）内生效，
# 避免“不要红色，米白可以”把米白也算成否定。
NEGATION_WORDS = (
    "不要", "不用", "不需要", "不想", "不喜欢", "不接受", "别", "避免",
    "无", "没有", "不含", "未见", "非", "忌", "拒绝", "去掉", "排除", "无需", "不",
)
NEGATION_BOUNDARIES = "。；;，,！？!?\n"
# “或/也可以/都行”是允许替代；“和/与/拼色”是明确组合。
ALT_CONNECTORS = ("或", "还是", "也可以", "都行", "均可", "二选一", "任选", "任一", "随便")
AND_CONNECTORS = ("和", "与", "以及", "＋", "+", "拼色", "拼接", "同色")

STATUS_RANK = {"有冲突": 6, "被改写": 5, "未落实": 4}
# 只把“已落实/间接体现”升级为需设计师判断；已经是冲突/被改写/未落实的绝不降级。
DESIGNER_UPGRADE_FROM = ("已落实", "间接体现")
EMPTY_SPEC_VALUES = ("", "无特殊需求", "无", "未写")

# 材质出处：只有出现“具体材料名”才会提示核实；纯属性描述不报。
MATERIAL_CHARS = frozenset("棉麻丝毛绒呢绸缎纱锦革皮纶纤绢裘毯")
PROPERTY_WORDS = (
    "哑光", "有肌理", "肌理", "挺括", "挺而不硬", "柔软", "柔滑", "垂感", "垂坠",
    "顺滑", "光泽", "亮面", "舒适", "轻薄", "厚实", "透气", "弹性", "手感", "骨感",
    "温润", "低调", "有质感", "纹理", "不贴身", "易打理", "抗皱", "免烫", "自然",
    "柔和", "含蓄", "高级", "精致", "细腻", "爽滑", "亲肤", "悬垂",
)
FABRIC_FILLERS = (
    "优选", "建议", "采用", "使用", "面料为", "面料", "材质", "主料", "里料",
    "质地", "工艺", "一些", "一点", "带", "有", "的",
)


# ---------------------------------------------------------------- 取值判断


def _norm(text: str) -> str:
    return "".join(str(text).split())


def _value(spec_fields: dict[str, SpecField], field: str) -> str:
    item = spec_fields.get(field)
    return item.value if item else ""


def _garment_value(value: str, garment: str | None) -> str:
    """多件套只核对对应单品；没有单品标记的映射仍看整栏。"""
    if not garment:
        return value
    aliases = {
        "外套": ("外套", "上衣"),
        "连衣裙": ("连衣裙", "裙子", "裙"),
    }.get(garment, (garment,))
    segments = [part.strip() for part in re.split(r"[；;\n]+", value or "") if part.strip()]
    labelled = False
    for segment in segments:
        match = re.match(r"^([^：:]{1,12})[：:](.*)$", segment)
        if not match:
            continue
        label, content = match.groups()
        if label.strip() in aliases:
            return content.strip()
        labelled = True
    return "" if labelled else value


def _loose_requirement(text: str) -> bool:
    return any(word in text for word in LOOSE_WORDS)


FITTED_WORDS = ("修身", "收腰", "显腰", "有腰线", "合体")
FITTED_NEGATIONS = ("不要修身", "别修身", "不修身", "不想修身", "不收腰", "不要收腰", "别收腰", "不要合体")


def _fitted_requirement(text: str) -> bool:
    """客户要修身/收腰（先去掉“不要修身/不收腰”这类否定说法再看）。"""
    cleaned = text or ""
    for token in FITTED_NEGATIONS:
        cleaned = cleaned.replace(token, "")
    return any(word in cleaned for word in FITTED_WORDS)


def is_loose_plan(value: str) -> bool:
    """方案明确写了不收腰或整体宽松。"""
    text = value or ""
    return any(token in text for token in WAIST_NEGATIONS) or (
        "宽松" in text and not has_waist_tuck(text)
    )


def has_waist_tuck(value: str) -> bool:
    """方案是不是真的收腰：先把“无收腰/不收腰”这类否定说法去掉再看。"""
    cleaned = value or ""
    for token in WAIST_NEGATIONS:
        cleaned = cleaned.replace(token, "")
    return "收腰" in cleaned


def _missing_target(value: str) -> Status:
    """方案这一栏写了别的内容 → 被改写；根本没写 → 未落实。"""
    text = (value or "").strip()
    if text and text not in EMPTY_SPEC_VALUES:
        return "被改写"
    return "未落实"


# ---------------------------------------------------------------- 否定与出现判断


def _clause_start(text: str, pos: int) -> int:
    for index in range(pos - 1, -1, -1):
        if text[index] in NEGATION_BOUNDARIES:
            return index + 1
    return 0


def _is_negated(text: str, pos: int) -> bool:
    prefix = text[_clause_start(text, pos):pos]
    return any(word in prefix for word in NEGATION_WORDS)


def _value_contains(value: str, token: str) -> bool:
    """方案里是否真的写了这个颜色/纹样；否定写法（不含/未见）算没写。"""
    start = 0
    while True:
        pos = value.find(token, start)
        if pos < 0:
            return False
        if not _is_negated(value, pos):
            return True
        start = pos + len(token)


# ---------------------------------------------------------------- 要求分组


def _occurrences(
    text: str, groups: tuple[tuple[str, ...], ...]
) -> list[tuple[tuple[str, ...], str, int]]:
    """找出这句话里命中哪些词：返回 (同义组, 命中的词, 位置)，长词优先、不重叠。"""
    candidates = [(token, group) for group in groups for token in group]
    matches: list[tuple[tuple[str, ...], str, int]] = []
    occupied: set[int] = set()
    for token, group in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        start = 0
        while True:
            pos = text.find(token, start)
            if pos < 0:
                break
            span = set(range(pos, pos + len(token)))
            if not span & occupied:
                matches.append((group, token, pos))
                occupied |= span
            start = pos + len(token)
    return sorted(matches, key=lambda item: item[2])


def _group_requirements(
    text: str, groups: tuple[tuple[str, ...], ...]
) -> list[dict]:
    """按连接词把一句话里的要求分组，不只看第一组。

    每组记下 {同义组, 命中的词, 是否否定}；“或/也可以/都行”连起来的是允许替代，
    “和/与/拼色”连起来的是明确组合。
    """
    result: list[dict] = []
    current: dict | None = None
    previous: tuple[tuple[str, ...], str, int] | None = None
    for group, token, pos in _occurrences(text, groups):
        if current is None or previous is None:
            current = {"wants": [(group, token, _is_negated(text, pos))], "any": False}
            result.append(current)
        else:
            connector = text[previous[2] + len(previous[1]):pos]
            if any(mark in connector for mark in ALT_CONNECTORS):
                current["wants"].append((group, token, _is_negated(text, pos)))
                current["any"] = True
            elif any(mark in connector for mark in AND_CONNECTORS):
                current["wants"].append((group, token, _is_negated(text, pos)))
            else:
                current = {"wants": [(group, token, _is_negated(text, pos))], "any": False}
                result.append(current)
        previous = (group, token, pos)
    return result


def _evaluate_wants(wants: dict, value: str) -> list[tuple[Status, str]]:
    """把一组要求与方案这一栏的写法对比，返回要升级到的状态和理由。"""
    problems: list[tuple[Status, str]] = []
    negatives = [(group, token) for group, token, negated in wants["wants"] if negated]
    positives = [(group, token) for group, token, negated in wants["wants"] if not negated]

    for group, token in negatives:
        hit = next((item for item in group if _value_contains(value, item)), None)
        if hit:
            problems.append(
                ("有冲突", f"客户明确不要“{token}”，方案「{value}」里却出现了“{hit}”。")
            )

    if not positives:
        return problems

    present = [(group, token) for group, token in positives
               if any(_value_contains(value, item) for item in group)]
    labels = list(dict.fromkeys(token for _, token in positives))
    if wants["any"]:
        if not present:
            problems.append(
                (
                    _missing_target(value),
                    f"客户说“{'或'.join(labels)}都可以”，方案「{value or '未写'}」一种都没有体现。",
                )
            )
        return problems

    missing = [(group, token) for group, token in positives
               if not any(_value_contains(value, item) for item in group)]
    if missing:
        if present:
            kept = "、".join(dict.fromkeys(token for _, token in present))
            lost = "、".join(dict.fromkeys(token for _, token in missing))
            problems.append(
                ("被改写", f"客户要“{'、'.join(labels)}”同时满足，方案「{value}」只体现了“{kept}”，缺了“{lost}”。")
            )
        else:
            problems.append(
                (
                    _missing_target(value),
                    f"客户要“{'、'.join(labels)}”，方案「{value or '未写'}」没有体现。",
                )
            )
    return problems


def pattern_group(text: str) -> tuple[str, ...] | None:
    hits = _occurrences(text, PATTERN_GROUPS)
    return hits[0][0] if hits else None


def pattern_satisfied(group: tuple[str, ...], value: str) -> bool:
    return any(_value_contains(value, token) for token in group)


# ---------------------------------------------------------------- 状态调整


def _upgrade(mapping: Mapping, status: Status, note: str) -> None:
    current = mapping.status
    if STATUS_RANK.get(status, 0) > STATUS_RANK.get(current, 0):
        mapping.status = status
        mapping.note = note


def _upgrade_to_designer(mapping: Mapping, note: str, rule_id: str | None = None) -> None:
    """把“模型以为已落实、其实要人判断”的映射改成需设计师判断。

    只从“已落实/间接体现”升级；已是“有冲突/被改写/未落实”的绝不降级，
    已经是“需设计师判断”的保留原来那条理由。
    """
    if mapping.status in DESIGNER_UPGRADE_FROM:
        mapping.status = "需设计师判断"
        mapping.note = note
        if rule_id and not mapping.rule_id:
            mapping.rule_id = rule_id


# ---------------------------------------------------------------- 材质出处


def _strip_words(text: str, words: tuple[str, ...]) -> str:
    result = text
    for word in sorted(words, key=len, reverse=True):
        result = result.replace(word, " ")
    return result


def _concrete_material(value: str) -> str | None:
    """从面料写法里挑出像“具体材料名”的片段；纯属性描述返回 None。"""
    if not value:
        return None
    cleaned = _strip_words(value, PROPERTY_WORDS + FABRIC_FILLERS)
    for segment in re.split(r"[，,；;、：:/＋+\s（）()\[\]【】]+", cleaned):
        segment = segment.strip()
        if len(segment) >= 2 and any(char in MATERIAL_CHARS for char in segment):
            return segment
    return None


def _fabric_reference_terms() -> set[str]:
    """可推荐面料的名称和别名（不含只有留出来源的条目）。"""
    terms: set[str] = set()
    keywords = knowledge.get_attrs().get("match_keywords", {})
    for item in knowledge.resources_for_field("面料"):
        name = (item.get("name") or "").strip()
        if name:
            terms.add(_norm(name))
        for keyword in keywords.get(item.get("id"), []):
            terms.add(_norm(keyword))
    return terms


def _material_has_reference(candidate: str) -> bool:
    if knowledge.find_resource_ids("面料", candidate):
        return True
    plain = _norm(candidate)
    for term in _fabric_reference_terms():
        if not term:
            continue
        if term in plain or (plain in term and len(plain) >= 2):
            return True
    return False


def _check_fabric_provenance(
    utterances: list[Utterance], spec_fields: dict[str, SpecField]
) -> None:
    """已有方案写了具体材料名、却对应不上可推荐资源时，提示设计师核实来源。

    只说“资源库暂无对应参考”，不断言现实中不存在或虚构；
    纯属性描述不报；已经是有冲突/被改写/未落实的映射不降级。
    """
    fabric_field = spec_fields.get("面料")
    if fabric_field is None or fabric_field.origin != "已有方案":
        return
    for utterance in utterances:
        for mapping in utterance.mappings:
            if mapping.field != "面料":
                continue
            value = _garment_value(fabric_field.value, mapping.garment)
            candidate = _concrete_material(value)
            if candidate is None or _material_has_reference(candidate):
                continue
            _upgrade_to_designer(
                mapping,
                f"方案面料写的是「{value}」，其中「{candidate}」资源库暂无对应参考，"
                "请核实这块面料的来源和可用性。",
            )


# ---------------------------------------------------------------- 补映射


def ensure_mappings(utterances: list[Utterance], spec_values: dict[str, str]) -> None:
    """模型漏掉关键字段的映射时补一条中性映射，交给下面的规则判断。

    只在方案这一栏有写法时补，且补的映射不带任何“结论”，避免凭空下判断。
    否定的要求（“不要梅花”）不补纹样映射，避免把否定当成正向要求。
    """
    pattern_value = (spec_values.get("装饰纹样") or "").strip()
    shape_value = (spec_values.get("版型结构") or "").strip()
    collar_value = (spec_values.get("领型") or "").strip()
    ease_value = (spec_values.get("松弛余量") or "").strip()

    for utterance in utterances:
        text = utterance.text
        fields = {mapping.field for mapping in utterance.mappings}

        positive_patterns = [
            token
            for _group, token, pos in _occurrences(text, PATTERN_GROUPS)
            if not _is_negated(text, pos)
        ]
        if positive_patterns and "装饰纹样" not in fields and pattern_value:
            token = positive_patterns[0]
            utterance.mappings.append(
                Mapping(
                    field="装饰纹样",
                    status="已落实",
                    note=f"程序复核：客户提到“{token}”，方案这一栏有写法，需要核对是否一致。",
                )
            )
            fields.add("装饰纹样")

        if (
            "版型结构" not in fields
            and _loose_requirement(text)
            and shape_value
            and has_waist_tuck(shape_value)
        ):
            utterance.mappings.append(
                Mapping(
                    field="版型结构",
                    status="已落实",
                    note="程序复核：客户要求宽松，方案版型这一栏需要核对是不是收腰。",
                )
            )
            fields.add("版型结构")

        if (
            "领型" not in fields
            and collar_value
            and any(word in text for word in NARROW_LEAD_WORDS)
        ):
            utterance.mappings.append(
                Mapping(
                    field="领型",
                    status="需设计师判断",
                    note="程序复核：客户对领宽有要求，方案没有写领宽，需要设计师判断是否达到效果。",
                    rule_id="R3",
                )
            )
            fields.add("领型")

        if (
            "松弛余量" not in fields
            and ease_value
            and any(word in text for word in INNER_LAYER_WORDS)
        ):
            utterance.mappings.append(
                Mapping(
                    field="松弛余量",
                    status="间接体现",
                    note="程序复核：客户要配内搭，方案只写了整体围度，没有单独写内搭放量。",
                    rule_id="R8",
                )
            )
            fields.add("松弛余量")


# ---------------------------------------------------------------- 规则复核


def _check_text_field(
    text: str, value: str, groups: tuple[tuple[str, ...], ...]
) -> list[tuple[Status, str]]:
    problems: list[tuple[Status, str]] = []
    for wants in _group_requirements(text, groups):
        problems.extend(_evaluate_wants(wants, value))
    return problems


def apply_program_rules(utterances: list[Utterance], spec_fields: dict[str, SpecField]) -> None:
    """规则复核，只把状态往更严重的方向改。"""
    for utterance in utterances:
        text = utterance.text
        for mapping in utterance.mappings:
            field = mapping.field
            value = _garment_value(_value(spec_fields, field), mapping.garment)

            if field == "版型结构" and _loose_requirement(text) and has_waist_tuck(value):
                _upgrade(
                    mapping,
                    "有冲突",
                    f"方案写“{value}”，与客户“{text}”的方向相反，需确认收腰程度。",
                )

            if (
                field in ("版型结构", "松弛余量")
                and _fitted_requirement(text)
                and not _loose_requirement(text)
                and is_loose_plan(value)
            ):
                _upgrade(
                    mapping,
                    "有冲突",
                    f"客户要“{text}”，方案写“{value}”，方向相反，需和客户确认修身程度。",
                )

            if (
                field == "领型"
                and any(word in text for word in NARROW_LEAD_WORDS)
                and "领宽" not in value
                and "窄" not in value
            ):
                _upgrade_to_designer(
                    mapping,
                    "客户对领宽有具体要求，方案只写了领型、没有写领宽，"
                    "是否达到“显脖子细”的效果需要设计师判断。",
                    "R3",
                )

            if field == "装饰纹样" and mapping.status != "有冲突":
                for status, note in _check_text_field(text, value, PATTERN_GROUPS):
                    _upgrade(mapping, status, note)

            if field == "色彩" and mapping.status != "有冲突":
                for status, note in _check_text_field(text, value, COLOR_GROUPS):
                    _upgrade(mapping, status, note)

    _check_fabric_provenance(utterances, spec_fields)


def recompute_statuses(utterances: list[Utterance]) -> None:
    """原话状态取自身映射里最严重的一个；已被客户回答过的原话统一为“已与客户确认”。"""
    for utterance in utterances:
        if utterance.resolved_by:
            utterance.status = "已与客户确认"
        else:
            utterance.status = most_severe([m.status for m in utterance.mappings])


def problem_utterances(utterances: list[Utterance]) -> list[Utterance]:
    return [
        u
        for u in utterances
        if any(m.status in ("有冲突", "被改写", "未落实") for m in u.mappings)
    ]

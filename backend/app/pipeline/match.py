"""从零生成方案：用程序规则从资源库里挑出第 1 版方案（不调大模型）。

处理顺序与约定一致：

1. 原话已经由大模型拆好（每句带它涉及的设计字段），这里只把“每句对某一栏的要求”
   整理成需求（`Want`）；
2. 硬约束是过滤条件，违反就排除；软偏好只用来加分排序；
3. 客户点名了资源词（名称、别名）时：明确说“不要”的资源排除，硬指定时只保留
   能满足的资源；正向的资源词只加分，不会因为出现否定词反而被加分或选中；
4. 命中身形规则 R1–R8 的，把处理写进对应字段，并把 rule_id 记到原话的映射上；
5. 没有任何原话涉及的字段，用这一单品在资源库里最常见的取值并在“不问清单”里说明；
   资源库里没有明显共同取值的，就空着，不硬凑；
6. 客户硬要资源库里没有的东西（例如梅花），这一栏标“无出处”、不写资源 id，
   在台账里留一条待办线索；软偏好则先挑最接近的资源，交给审计判“被改写”；
7. 多件套（外套＋连衣裙）按单品分别匹配，字段值写成“外套：……；连衣裙：……”，
   资源 id 合并但每一项都来自它自己的单品；某个单品匹配不到就明确写无出处，
   不把另一边单品的资源硬拼过去。

所有候选都先经 `knowledge.is_recommendable` 过滤：只看留出案例出处的资源不进候选。
本模块只读 data/ 和 knowledge_attrs.json，不写任何数据文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import knowledge
from ..schemas import Mapping, SpecField, Utterance

FIELD_ORDER = knowledge.FIELD_ORDER

# ---------------------------------------------------------------- 词表

COLOR_TOKENS = (
    "亮粉", "粉色", "米白", "黑色", "白色", "红色", "蓝色", "绿色",
    "灰色", "银灰", "金色", "紫色", "黄色", "橙色", "咖色",
    "酒红", "枣红", "正红", "藏青", "深蓝", "墨绿", "驼色", "卡其",
    "香槟", "深灰", "象牙白", "奶白",
)

# 具体纹样按同义组归并：客户说组里任一个词，资源带组里任一个词就算对得上。
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
PATTERN_HINT_WORDS = ("纹", "花", "格", "条", "波点", "图案", "绣", "肌理")

LOOSE_WORDS = ("宽松", "舒展", "松弛", "无束缚", "不紧绷", "舒服一点")
WAIST_WORDS = ("收腰", "有腰线", "修身", "显腰")
WAIST_NEGATIONS = (
    "不收腰", "不要收腰", "别收腰", "没有收腰", "无收腰",
    "不收紧腰身", "腰部要宽松", "腰部宽松",
)
LOW_COLLAR_WORDS = ("不要太高", "不能太高", "领子低", "低立领", "不要高领")
CHINESE_WORDS = ("中式",)
DARK_WORDS = ("暗纹", "暗花")
MATTE_WORDS = ("哑光", "低调", "内敛")
SHINY_WORDS = ("亮面", "光泽", "上镜")
BONE_WORDS = ("不压肩", "宽厚", "骨感")
NARROW_WORDS = ("要窄", "窄一点", "窄一些", "显脖子细", "脖子细", "领宽")
LENGTH_TOKENS = ("脚踝", "臀中", "过膝", "膝上", "中长", "长款")

PREFERENCE_WORDS = (
    "中式", "正式", "优雅", "端庄", "低调", "内敛", "简约", "明快", "上镜",
    "垂感", "柔和", "挺", "暗纹", "暗花", "提花", "舒适", "气质", "舒展",
    "宽松", "收腰", "腰线", "平肩", "落肩", "不压肩", "领高", "圆润",
    "V形", "翻领", "立领", "斜襟", "交领", "臀中", "脚踝", "光泽", "肌理",
    "修身", "显瘦", "合体", "喜庆", "保暖", "透气", "弹力",
)

# 出现在资源词前面，表示客户“不要这个”。否定词一律不算加分，也不算点名要。
NEGATION_WORDS = (
    "不要", "不想要", "不想", "不用", "不需要", "不必", "不喜欢", "不采用",
    "不喜欢", "别用", "别要", "别", "拒绝", "避免", "不能", "无需", "无须", "不",
)

# 里料类资源（例如真丝里布）只能做里子，不能当外套主面料，除非原话明确要里料。
LINING_WORDS = ("里料", "里布", "内衬", "衬里", "里子")

# ---------------------------------------------------------------- 身形规则

RULE_DEFS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("R1", ("肚腩", "小肚", "肚子")),
    ("R2", LOW_COLLAR_WORDS),
    ("R3", NARROW_WORDS),
    ("R4", ("肩背", "宽厚", "不压肩", "不能压肩")),
    ("R5", ("抬手", "不卡手臂", "不卡", "手臂")),
    ("R6", ("坐着", "坐奏", "坐姿")),
    ("R7", ("溜肩",)),
    ("R8", ("内搭", "打底", "里面要")),
)

RULE_FIELDS: dict[str, tuple[str, ...]] = {
    "R1": ("松弛余量", "版型结构"),
    "R2": ("领型",),
    "R3": ("领型",),
    "R4": ("松弛余量", "面料"),
    "R5": ("松弛余量",),
    "R6": ("松弛余量",),
    "R7": ("版型结构",),
    "R8": ("松弛余量",),
}

# 只写资源库口径里说得出的处理，不写任何具体厘米数（数值要量体后确认）。
RULE_EASE_TEXT: dict[str, str] = {
    "R1": "前片腹部预留充足松量",
    "R4": "肩线、袖笼额外放量",
    "R5": "袖管放量充足",
    "R6": "裙摆放量大",
    "R8": "为内搭预留放量（具体数值量体后确认）",
}

R7_NOTE = (
    "客户提到溜肩；资源库里的案例只写了自然落肩，没有针对溜肩的处理，"
    "是否到位需要设计师判断。"
)


# ---------------------------------------------------------------- 需求


@dataclass
class Want:
    """一句原话对某一个设计字段的要求。"""

    field: str
    text: str
    strength: str
    utterance: Utterance
    garment: str | None = None

    @property
    def hard(self) -> bool:
        return self.strength == "硬约束"


@dataclass
class Mention:
    """原话里点到了某个资源的名称/别名，以及是不是被否定了。"""

    token: str
    positive: bool
    hard: bool


def _collect_wants(utterances: list[Utterance]) -> dict[str, list[Want]]:
    wants: dict[str, list[Want]] = {}
    for utterance in utterances:
        for mapping in utterance.mappings:
            wants.setdefault(mapping.field, []).append(
                Want(
                    field=mapping.field,
                    text=utterance.text,
                    strength=utterance.strength,
                    utterance=utterance,
                    garment=mapping.garment,
                )
            )
    return wants


def _joined(wants: list[Want]) -> str:
    return " ".join(want.text for want in wants)


# ---------------------------------------------------------------- 资源候选与打分


def _candidates(kind: str) -> list[dict]:
    """某一类资源（silhouettes/shoulders/...），已排除只有留出案例出处的条目。"""
    items = knowledge.get_resources().get(kind, [])
    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if not knowledge.is_recommendable(item):
            continue
        result.append(item)
    return result


def _haystack(resource: dict) -> str:
    """资源能用来比对的一整段文字：名称、特征，以及属性里为正的枚举值。"""
    parts = [str(resource.get("name", ""))]
    parts += [str(trait) for trait in (resource.get("traits") or [])]
    for key, value in knowledge.attr(resource["id"]).items():
        if value is True:
            parts.append(str(key))
        elif isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


def _dev_count(resource: dict) -> int:
    dev = knowledge.dev_case_ids()
    return sum(1 for source in (resource.get("sources") or []) if source in dev)


def _match_keywords(resource_id: str) -> list[str]:
    """复用 knowledge_attrs.json 里给资源维护的别名，不按某个案例硬编码。"""
    return list(knowledge.get_attrs().get("match_keywords", {}).get(resource_id, []))


_SPLIT_NAME = re.compile(r"[（(，,；;、]")


def _aliases(resource: dict) -> list[str]:
    """资源能被客户原话点到的名字：完整名称、名称的第一段，以及维护好的别名。"""
    rid = str(resource.get("id", ""))
    raw = [str(resource.get("name", ""))] + _match_keywords(rid)
    aliases: list[str] = []
    for item in raw:
        text = str(item).strip()
        head = _SPLIT_NAME.split(text)[0].strip()
        for candidate in (text, head):
            if len(candidate) >= 2 and candidate not in aliases:
                aliases.append(candidate)
    return aliases


def _negated(text: str, start: int) -> bool:
    before = text[max(0, start - 6):start]
    return any(before.endswith(word) for word in NEGATION_WORDS)


def _positive_in(text: str, token: str) -> bool:
    """词在原文里是不是以肯定的意思出现（“不要梅花”里的“梅花”不算）。"""
    start = text.find(token)
    while start >= 0:
        if not _negated(text, start):
            return True
        start = text.find(token, start + len(token))
    return False


def _mentions(resource: dict, wants: list[Want]) -> list[Mention]:
    aliases = _aliases(resource)
    found: list[Mention] = []
    for want in wants:
        text = want.text
        for alias in aliases:
            start = text.find(alias)
            while start >= 0:
                found.append(
                    Mention(
                        token=alias,
                        positive=not _negated(text, start),
                        hard=want.hard,
                    )
                )
                start = text.find(alias, start + len(alias))
    return found


def _wanted_filter(pool: list[dict], wants: list[Want]) -> list[dict]:
    """按原话里点名的资源词过滤。

    * 被否定的资源（“不要醋酸”“不要交领”）直接排除，也不会反向加分；
    * 客户硬点名了可满足的资源时，只保留能满足的资源，避免换成别的材料。
    """
    if not wants or not pool:
        return pool
    mentions = {item["id"]: _mentions(item, wants) for item in pool}
    kept: list[dict] = []
    has_hard_positive = False
    for item in pool:
        found = mentions[item["id"]]
        if any(not mention.positive for mention in found):
            continue
        kept.append(item)
        if any(mention.positive and mention.hard for mention in found):
            has_hard_positive = True
    if has_hard_positive:
        kept = [
            item
            for item in kept
            if any(
                mention.positive and mention.hard for mention in mentions[item["id"]]
            )
        ]
    return kept


def _score(resource: dict, wants: list[Want], extra_tokens: tuple[str, ...] = ()) -> int:
    texts = _joined(wants)
    haystack = _haystack(resource)
    name = str(resource.get("name", ""))
    score = 0
    for word in PREFERENCE_WORDS:
        if word not in texts:
            continue
        if word in name:
            score += 3
        elif word in haystack:
            score += 1
    # 客户点到了资源的名称/别名（例如“面料想用醋酸”）：正向点名才加分。
    seen: set[str] = set()
    for mention in _mentions(resource, wants):
        if not mention.positive or mention.token in seen:
            continue
        seen.add(mention.token)
        score += 3 if mention.token in name else 2
        if mention.hard:
            score += 2
    if "不压肩" in texts and knowledge.attr(resource["id"]).get("压肩") is False:
        score += 3
    # 客户嫌肩宽、想显窄：优先“显肩窄”的肩型。
    if "肩" in texts and "宽" in texts and "窄" in texts and "显肩窄" in haystack:
        score += 3
    # 别的栏点名的纹样（例如“最好有云纹”），面料本身就带这个纹样时加分，方便直接找到现货。
    for token in extra_tokens:
        if token in haystack:
            score += 2
    return score


def _best(
    pool: list[dict], wants: list[Want], extra_tokens: tuple[str, ...] = ()
) -> dict | None:
    if not pool:
        return None
    ranked = sorted(
        pool,
        key=lambda item: (-_score(item, wants, extra_tokens), -_dev_count(item), item["id"]),
    )
    return ranked[0]


def _dominant(
    kind: str,
    scope: str | None,
    spec_ctx: dict[str, str],
    allow=None,
) -> dict | None:
    """这一单品在资源库里最常见的取值。

    外套这类通用字段至少要有 2 个开发集案例出处才默认；多件套里按单品限定的
    字段（例如连衣裙裙长）只有一个单品可选，至少 1 个出处即可，没有就留空。
    """
    pool = [
        item
        for item in _candidates(kind)
        if _scope_allows(item, scope, spec_ctx)
        and (allow is None or allow(item, ""))
    ]
    ranked = sorted(pool, key=lambda item: (-_dev_count(item), item["id"]))
    if not ranked:
        return None
    threshold = 1 if scope is not None else 2
    if _dev_count(ranked[0]) < threshold:
        return None
    return ranked[0]


# ---------------------------------------------------------------- 各字段的过滤条件


def _silhouette_allow(resource: dict, texts: str) -> bool:
    tuck = knowledge.attr(resource["id"]).get("收腰")
    if not isinstance(tuck, str):
        return True
    wants_waist = any(word in texts for word in WAIST_WORDS) and not any(
        word in texts for word in WAIST_NEGATIONS
    )
    wants_loose = any(word in texts for word in LOOSE_WORDS) or any(
        word in texts for word in WAIST_NEGATIONS
    )
    if wants_waist and tuck == "无":
        return False
    if wants_loose and not wants_waist and tuck in ("微", "收"):
        return False
    return True


def _shoulder_allow(resource: dict, texts: str) -> bool:
    """客户要求“不压肩”时，只保留压肩属性明确为 False 的肩型。"""
    if any(
        word in texts for word in ("不压肩", "不能压肩", "不要压肩", "别压肩")
    ):
        return knowledge.attr(resource["id"]).get("压肩") is False
    return True


def _collar_allow(resource: dict, texts: str) -> bool:
    attrs = knowledge.attr(resource["id"])
    if any(word in texts for word in LOW_COLLAR_WORDS):
        if resource.get("family") == "立领" and attrs.get("领高") != "低":
            return False
    if any(word in texts for word in CHINESE_WORDS) and attrs.get("风格") == "偏西式":
        return False
    return True


def _fabric_allow(resource: dict, texts: str) -> bool:
    haystack = _haystack(resource)
    attrs = knowledge.attr(resource["id"])
    if attrs.get("用途") == "里料" and not any(word in texts for word in LINING_WORDS):
        return False
    if any(word in texts for word in DARK_WORDS):
        if not any(word in haystack for word in ("暗纹", "暗花", "织花", "提花")):
            return False
    if any(word in texts for word in MATTE_WORDS):
        if attrs.get("哑光") is not True and "哑光" not in haystack:
            return False
    if any(word in texts for word in SHINY_WORDS):
        if not any(word in haystack for word in ("亮面", "光泽", "上镜")):
            return False
    if any(word in texts for word in BONE_WORDS):
        if not any(word in haystack for word in ("骨感", "挺")):
            return False
    return True


def _pattern_matches(resource: dict, token: str) -> bool:
    if knowledge.attr(resource["id"]).get(token) is False:
        return False
    haystack = _haystack(resource)
    if token in haystack:
        return True
    group = next((group for group in PATTERN_GROUPS if token in group), None)
    if group is not None and any(word in haystack for word in group):
        return True
    return False


def _pattern_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for group in PATTERN_GROUPS:
        for token in group:
            if token not in tokens and _positive_in(text, token):
                tokens.append(token)
    return tokens


def _length_allow(resource: dict, texts: str) -> bool:
    tokens = [token for token in LENGTH_TOKENS if _positive_in(texts, token)]
    if not tokens:
        return True
    return all(token in _haystack(resource) for token in tokens)


# ---------------------------------------------------------------- 规则挂接


def _attach_rules(utterances: list[Utterance]) -> dict[str, list[str]]:
    """命中的身形规则标到原话映射上；R7（溜肩）一律落到“需设计师判断”。"""
    hits: dict[str, list[str]] = {}
    for rule_id, words in RULE_DEFS:
        for utterance in utterances:
            if not any(word in utterance.text for word in words):
                continue
            fields = RULE_FIELDS[rule_id]
            for mapping in utterance.mappings:
                if mapping.field in fields and not mapping.rule_id:
                    mapping.rule_id = rule_id
            if rule_id == "R7":
                target = next(
                    (m for m in utterance.mappings if m.field == "版型结构"), None
                )
                if target is None:
                    utterance.mappings.append(
                        Mapping(
                            field="版型结构",
                            status="需设计师判断",
                            note=R7_NOTE,
                            rule_id="R7",
                        )
                    )
                else:
                    if target.status != "有冲突":
                        target.status = "需设计师判断"
                    target.note = R7_NOTE
                    target.rule_id = "R7"
            hits.setdefault(rule_id, []).append(utterance.id)
    return hits


# ---------------------------------------------------------------- 单品范围


def detect_category(text: str) -> str:
    if "外套" in text and ("连衣裙" in text or "一件裙子" in text):
        return "两件式套装（外套＋连衣裙）"
    if "套装" in text:
        return "两件式套装（外套＋连衣裙）"
    if "连衣裙" in text:
        return "连衣裙"
    if "外套" in text:
        return "外套"
    if "上衣" in text:
        return "上衣"
    return ""


def _garment_scopes(category: str, wants: dict[str, list[Want]]) -> list[str | None]:
    """要分别匹配的单品；不是多件套时返回 [None]，沿用单份写法。"""
    if "两件式" in category or "套装" in category:
        return ["外套", "连衣裙"]
    mentioned: list[str] = []
    for items in wants.values():
        for want in items:
            if want.garment and want.garment not in mentioned:
                mentioned.append(want.garment)
    if len(mentioned) > 1:
        ordered = [name for name in ("外套", "连衣裙") if name in mentioned]
        return ordered + [name for name in mentioned if name not in ordered]
    return [None]


def _scope_wants(wants: list[Want], scope: str | None) -> list[Want]:
    """这一单品要遵守的要求：客户没指定单品的共享要求对每个单品都生效。"""
    if scope is None:
        return wants
    return [want for want in wants if want.garment in (None, scope)]


def _scope_allows(resource: dict, scope: str | None, spec_ctx: dict[str, str]) -> bool:
    if scope is None:
        return knowledge.garment_allowed(resource, spec_ctx)
    garment = resource.get("garment")
    return garment is None or garment == scope


def _choose_one(
    kind: str,
    wants: list[Want],
    scope: str | None,
    spec_ctx: dict[str, str],
    allow=None,
    extra_tokens: tuple[str, ...] = (),
) -> tuple[dict | None, bool]:
    """返回 (这一单品选中的资源, 是否用了“没有原话时的最常见取值”)。"""
    texts = _joined(wants)
    if wants:
        pool = [
            item
            for item in _candidates(kind)
            if _scope_allows(item, scope, spec_ctx) and (allow is None or allow(item, texts))
        ]
        pool = _wanted_filter(pool, wants)
        return _best(pool, wants, extra_tokens), False
    return _dominant(kind, scope, spec_ctx, allow), True


# ---------------------------------------------------------------- 门襟做法

# 客户对门襟、扣子的要求 → 工艺资源。先看案例资源，再看模拟库；都按资源名去库里找，不写死 id。
CLOSURE_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("不要扣子", "不要纽扣", "无扣", "不要扣", "没有扣子", "别有扣子"), ("无扣开襟", "隐形暗扣门襟")),
    (("盘扣",), ("手工盘扣",)),
    (("一字扣",), ("一字扣",)),
    (("暗扣",), ("隐形暗扣门襟",)),
    (("拉链",), ("前中隐形拉链",)),
    (("系带",), ("腰部同料系带",)),
)


def _attach_closure(field: SpecField, wants: list[Want], meta: dict) -> None:
    """客户提了门襟要求时，把对应工艺写进版型结构，并记资源编号。"""
    texts = _joined(wants)
    if not texts:
        return
    crafts = {str(item.get("name", "")): item for item in _candidates("crafts")}
    for triggers, names in CLOSURE_RULES:
        if not any(word in texts for word in triggers):
            continue
        chosen = next((crafts[name] for name in names if name in crafts), None)
        if chosen is None:
            meta["unmatched"].append("版型结构：客户对门襟的要求在资源库里找不到对应工艺")
            return
        name = str(chosen.get("name", ""))
        if name and name not in field.value:
            field.value = f"{field.value}；门襟{name}" if field.value else f"门襟{name}"
        if chosen["id"] not in field.resource_ids:
            field.resource_ids.append(chosen["id"])
        if field.origin == "无出处" and field.resource_ids:
            field.origin = "资源库"
        return


# ---------------------------------------------------------------- 拼字段值


def _spec_field(
    value: str, resource_ids: list[str], wants: list[Want], origin: str
) -> SpecField:
    utterance_ids: list[str] = []
    for want in wants:
        if want.utterance.id not in utterance_ids:
            utterance_ids.append(want.utterance.id)
    return SpecField(
        value=value,
        resource_ids=list(resource_ids),
        utterance_ids=utterance_ids,
        origin=origin,
        confirmed=False,
    )


def _add_id(resource_ids: list[str], resource_id: str) -> None:
    """同一个资源被两个单品共用时只记一次，避免方案里出现重复 id。"""
    if resource_id not in resource_ids:
        resource_ids.append(resource_id)


def _compose_parts(parts: list[tuple[str | None, str]], label: str) -> str:
    """单份方案直接写值；多件套写成“外套：……；连衣裙：……”。"""
    if len(parts) == 1 and parts[0][0] is None:
        return parts[0][1]
    segments: list[str] = []
    for scope, value in parts:
        text = value or f"暂无{label}资源出处，需设计师确认"
        segments.append(f"{scope}：{text}")
    return "；".join(segments)


def _field_from_parts(
    field: str,
    parts: list[tuple[str | None, str]],
    resource_ids: list[str],
    wants: list[Want],
    label: str,
    meta: dict,
) -> SpecField:
    value = _compose_parts(parts, label)
    missing = [scope for scope, text in parts if not text]
    origin = "资源库" if resource_ids else "无出处"
    if missing and resource_ids:
        # 有的单品有出处、有的没有：整栏不能只写“资源库”，把缺的那个写明白。
        origin = "无出处"
        meta["unmatched"].append(
            f"{field}：{'、'.join(str(name) for name in missing)}没有匹配到资源，已明确标为无出处"
        )
    elif missing and not resource_ids:
        meta["unmatched"].append(f"{field}：没有满足客户要求的资源，需要人工确认")
    return _spec_field(value, resource_ids, wants, origin)


# ---------------------------------------------------------------- 主流程


def build_initial_spec(
    utterances: list[Utterance], text: str
) -> tuple[dict[str, SpecField], dict]:
    """按原话匹配出第 1 版方案，返回 (八栏方案, 匹配说明)。"""
    wants = _collect_wants(utterances)
    rule_hits = _attach_rules(utterances)

    category = detect_category(text)
    scopes = _garment_scopes(category, wants)
    spec_ctx = {"品类": category}
    fields: dict[str, SpecField] = {}
    meta: dict = {
        "category": category,
        "garments": [scope for scope in scopes if scope],
        "unmatched": [],
        "defaults": [],
        "rule_hits": rule_hits,
    }

    # 品类：客户自己说的，直接从原话取。资源库里没有独立的品类项，
    # 所以不写资源 id，也不能未经客户问答就标“客户确认”（原地改为“无出处”）。
    category_wants = wants.get("品类", [])
    fields["品类"] = _spec_field(category, [], category_wants, "无出处")
    if category_wants and not category:
        meta["unmatched"].append("品类：识别不出资源库认识的衣种，先请设计师确认")

    # 版型结构：廓形 + 肩型，按单品分别匹配
    shape_wants = wants.get("版型结构", [])
    shape_parts: list[tuple[str | None, str]] = []
    shape_ids: list[str] = []
    silhouettes: dict[str | None, dict | None] = {}
    for scope in scopes:
        scoped = _scope_wants(shape_wants, scope)
        silhouette, silhouette_default = _choose_one(
            "silhouettes", scoped, scope, spec_ctx, _silhouette_allow
        )
        has_dress_shoulder_evidence = any(
            want.garment == "连衣裙" for want in shape_wants
        )
        shoulder = None
        if scope != "连衣裙" or has_dress_shoulder_evidence:
            shoulder, _ = _choose_one(
                "shoulders", scoped, scope, spec_ctx, _shoulder_allow
            )
        silhouettes[scope] = silhouette
        value = ""
        if silhouette is not None:
            value = str(silhouette.get("name", ""))
            _add_id(shape_ids, silhouette["id"])
            if silhouette_default and "版型结构" not in meta["defaults"]:
                meta["defaults"].append("版型结构")
        # 肩型跟着廓形一起成立：这个单品连廓形都匹配不到时，不单独拼一个肩型充数。
        if shoulder is not None and silhouette is not None:
            value = (value + "；肩型" if value else "肩型") + str(shoulder.get("name", ""))
            _add_id(shape_ids, shoulder["id"])
        shape_parts.append((scope, value))
    fields["版型结构"] = _field_from_parts(
        "版型结构", shape_parts, shape_ids, shape_wants, "版型", meta
    )
    _attach_closure(fields["版型结构"], shape_wants, meta)

    # 领型
    collar_wants = wants.get("领型", [])
    collar_parts: list[tuple[str | None, str]] = []
    collar_ids: list[str] = []
    for scope in scopes:
        scoped = _scope_wants(collar_wants, scope)
        collar, collar_default = _choose_one(
            "collars", scoped, scope, spec_ctx, _collar_allow
        )
        if collar is not None:
            collar_parts.append((scope, str(collar.get("name", ""))))
            _add_id(collar_ids, collar["id"])
            if collar_default and "领型" not in meta["defaults"]:
                meta["defaults"].append("领型")
        else:
            collar_parts.append((scope, ""))
    fields["领型"] = _field_from_parts(
        "领型", collar_parts, collar_ids, collar_wants, "领型", meta
    )

    # 面料：里料类资源只有客户明确要里料时才用
    fabric_wants = wants.get("面料", [])
    # 客户在纹样那句里点名的纹样（含同义词），用来给自带该纹样的面料加分
    pattern_hint: tuple[str, ...] = tuple(
        word
        for want in wants.get("装饰纹样", [])
        for token in _pattern_tokens(want.text)
        for group in PATTERN_GROUPS
        if token in group
        for word in group
        if token not in ("暗花", "暗纹")
    )
    fabric_parts: list[tuple[str | None, str]] = []
    fabric_ids: list[str] = []
    for scope in scopes:
        scoped = _scope_wants(fabric_wants, scope)
        fabric, fabric_default = _choose_one(
            "fabrics", scoped, scope, spec_ctx, _fabric_allow, pattern_hint
        )
        if fabric is not None:
            fabric_parts.append((scope, str(fabric.get("name", ""))))
            _add_id(fabric_ids, fabric["id"])
            if fabric_default and "面料" not in meta["defaults"]:
                meta["defaults"].append("面料")
        else:
            fabric_parts.append((scope, ""))
    fields["面料"] = _field_from_parts(
        "面料", fabric_parts, fabric_ids, fabric_wants, "面料", meta
    )

    # 装饰纹样：硬要的具体纹样资源库没有 → 标无出处；软偏好先挑最接近的，交给审计
    pattern_wants = wants.get("装饰纹样", [])
    tokens: list[str] = []
    for want in pattern_wants:
        for token in _pattern_tokens(want.text):
            if token not in tokens:
                tokens.append(token)
    pool = _wanted_filter(
        [
            item
            for item in _candidates("patterns")
            if knowledge.garment_allowed(item, spec_ctx)
        ],
        pattern_wants,
    )
    hard_tokens = [
        token
        for token in tokens
        if any(want.hard and _positive_in(want.text, token) for want in pattern_wants)
    ]
    pattern_field = _spec_field("", [], pattern_wants, "无出处")
    if hard_tokens:
        matched = [
            item for item in pool if all(_pattern_matches(item, token) for token in hard_tokens)
        ]
        chosen = _best(matched, pattern_wants)
        if chosen is not None:
            pattern_field = _spec_field(
                f"衣身{chosen.get('name', '')}", [chosen["id"]], pattern_wants, "资源库"
            )
        else:
            token = hard_tokens[0]
            pattern_field = _spec_field(
                f"衣身{token}纹样（资源库中暂无对应参考，需另找面料）",
                [],
                pattern_wants,
                "无出处",
            )
            meta["unmatched"].append(f"装饰纹样：客户要的“{token}”在资源库里找不到对应参考")
    elif pattern_wants and any(
        any(word in want.text for word in PATTERN_HINT_WORDS)
        for want in pattern_wants
    ):
        chosen = _best(pool, pattern_wants)
        if chosen is not None:
            pattern_field = _spec_field(
                f"衣身{chosen.get('name', '')}", [chosen["id"]], pattern_wants, "资源库"
            )
            meta["unmatched"].append(
                f"装饰纹样：暂时按资源 {chosen['id']}「{chosen.get('name', '')}」处理，"
                "是否等于客户要的纹样由审计判断"
            )
    elif pattern_wants:
        meta["unmatched"].append(
            "装饰纹样：客户只表达了整体风格，没有明确纹样或图案；初稿不自动套用具体花型"
        )
    fields["装饰纹样"] = pattern_field

    # 色彩：资源库只有一个色彩参考，没有原话时留空、不硬套
    color_wants = wants.get("色彩", [])
    color_texts = _joined(color_wants)
    color_tokens = [token for token in COLOR_TOKENS if _positive_in(color_texts, token)]
    color_pool = [
        item
        for item in _candidates("colors")
        if knowledge.garment_allowed(item, spec_ctx)
    ]
    color_field = _spec_field("", [], color_wants, "无出处")
    if color_wants and color_tokens:
        matched = [
            item
            for item in color_pool
            if all(token in _haystack(item) for token in color_tokens)
        ]
        chosen = _best(matched, color_wants)
        if chosen is not None:
            name = str(chosen.get("name", ""))
            # 资源名不含客户说的颜色词时（例如客户说“红色”，选中“枣红”），把客户的词带上，
            # 方案读起来对得上原话，审计也不会误判成“被改写”。
            missing = [token for token in color_tokens if token not in name]
            value = f"{'、'.join(missing)}：{name}" if missing else name
            color_field = _spec_field(value, [chosen["id"]], color_wants, "资源库")
        elif any(want.hard for want in color_wants):
            token_text = "或".join(color_tokens)
            color_field = _spec_field(
                f"{token_text}（资源库中暂无对应色彩参考，需客户确认并另找面料）",
                [],
                color_wants,
                "无出处",
            )
            meta["unmatched"].append(
                f"色彩：客户提出的“{token_text}”在资源库里找不到对应参考"
            )
    fields["色彩"] = color_field

    # 长度比例：没有原话时用这一单品在资源库里最常见的取值
    length_wants = wants.get("长度比例", [])
    length_parts: list[tuple[str | None, str]] = []
    length_ids: list[str] = []
    for scope in scopes:
        scoped = _scope_wants(length_wants, scope)
        length, length_default = _choose_one(
            "lengths", scoped, scope, spec_ctx, _length_allow
        )
        if length is not None:
            length_parts.append((scope, str(length.get("name", ""))))
            _add_id(length_ids, length["id"])
            if length_default and "长度比例" not in meta["defaults"]:
                meta["defaults"].append("长度比例")
        else:
            length_parts.append((scope, ""))
    fields["长度比例"] = _field_from_parts(
        "长度比例", length_parts, length_ids, length_wants, "衣长", meta
    )

    # 松弛余量：没有单独资源，取这一单品廓形的松量属性，再把身形规则的处理写进去
    ease_wants = wants.get("松弛余量", [])
    # 每条规则是被哪句原话触发的；这句原话如果只针对某个单品，规则也只作用于那个单品。
    rule_utterance_garments: dict[str, set[str | None]] = {}
    for utterance in utterances:
        rule_utterance_garments[utterance.id] = {
            mapping.garment for mapping in utterance.mappings
        }

    def _rule_applies(rule_id: str, scope: str | None) -> bool:
        if rule_id not in rule_hits:
            return False
        if rule_id == "R5" and scope == "连衣裙":
            # “抬手方便”在多件套里首先约束外套袖笼；连衣裙没有明确袖型时
            # 不能凭空写成“袖管放量”。
            return False
        if rule_id == "R8" and scope == "连衣裙" and "两件式" in category:
            # “里面搭配内搭”默认约束外套；只有模型明确把该要求映射到
            # 连衣裙时才需要处理裙装内层。
            return any(
                mapping.garment == "连衣裙"
                for utterance in utterances
                if utterance.id in rule_hits.get(rule_id, [])
                for mapping in utterance.mappings
                if mapping.rule_id == rule_id
            )
        if rule_id == "R6":
            # R6 的做法是“裙摆放量大”，只对连衣裙说。
            if scope is not None:
                return scope == "连衣裙"
            return "连衣裙" in category
        if scope is None:
            return True
        for utterance_id in rule_hits.get(rule_id, []):
            garments = rule_utterance_garments.get(utterance_id, set())
            if None in garments or scope in garments:
                return True
        return False

    ease_parts: list[tuple[str | None, str]] = []
    ease_ids: list[str] = []
    for scope in scopes:
        segments: list[str] = []
        silhouette = silhouettes.get(scope)
        if silhouette is not None:
            ease_attr = knowledge.attr(silhouette["id"]).get("松量")
            if isinstance(ease_attr, str) and ease_attr:
                segments.append(f"整体围度{ease_attr}")
                _add_id(ease_ids, silhouette["id"])
        for rule_id in ("R1", "R4", "R5", "R6", "R8"):
            if not _rule_applies(rule_id, scope):
                continue
            text_rule = RULE_EASE_TEXT.get(rule_id)
            if text_rule and text_rule not in segments:
                segments.append(text_rule)
        ease_parts.append((scope, "；".join(segments)))
    fields["松弛余量"] = _field_from_parts(
        "松弛余量", ease_parts, ease_ids, ease_wants, "松量", meta
    )

    # 约定第 5 节：八个字段顺序固定，返回时严格按 FIELD_ORDER 排列。
    ordered = {field: fields[field] for field in FIELD_ORDER}
    return ordered, meta

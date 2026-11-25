"""拆句与抽取（真实大模型模式）。

只负责一件事：把客户口述拆成原话，并给出每句对八个设计字段的要求和在已有方案里的
落实情况。位置（start/end）由程序在原文里查，模型不给位置、也不能改字。

两种口径：

* 审计已有方案（默认）：判断每句在已有方案里落实得怎么样；
* 从零生成方案（`from_zero=True`）：还没有方案，只拆句、指出每句涉及哪些字段，
  落实情况一律填“已落实”，等程序匹配出初稿之后再统一复核。

少样本示例只用案例一和案例三，**不使用案例五**，也不使用本阶段要自检的案例二、四、六，
避免把答案直接喂给模型。
"""

from __future__ import annotations

import json

from .. import knowledge, llm
from ..llm import LLMClient
from ..llm_models import ExtractionResult
from ..schemas import FIELD_ORDER, Mapping, Utterance, most_severe

SYSTEM_PROMPT = (
    "你是上服集团定制门店的需求解构助手，服务对象是定制业务专员。"
    "你的工作是：把客户口述逐句拆开，判断每句话在已有设计方案里落实得怎么样。"
    "只能依据客户口述和给出的已有方案，不许编造客户没说过的内容，不许改写客户的原话，"
    "不许引用方案和口述之外的案例或资源编号。"
    "拆句时以“可独立审计的一项要求”为单位：不要把整段口述合成一句，也不要机械按逗号拆碎；"
    "否定意思不能被改写成相反的正向要求。"
    "你只能输出一个 JSON 对象，不能输出解释、Markdown 或任何多余文字。"
)

SYSTEM_PROMPT_FROM_ZERO = (
    "你是上服集团定制门店的需求解构助手，服务对象是定制业务专员。"
    "你的工作是：把客户口述逐句拆开，指出每句话对哪些设计字段提出了要求。"
    "这次还没有设计方案，不要判断“落实得怎么样”。"
    "只能依据客户口述，不许编造客户没说过的内容，不许改写客户的原话，"
    "不许引用案例或资源编号。"
    "拆句时以“可独立审计的一项要求”为单位：不要把整段口述合成一句，也不要机械按逗号拆碎；"
    "否定意思不能被改写成相反的正向要求。"
    "你只能输出一个 JSON 对象，不能输出解释、Markdown 或任何多余文字。"
)

EXAMPLE_CASES = ("c1", "c3")
EXAMPLE_LABELS = ("一", "二")
# 从零生成方案的示例只用案例一，不涉及案例五。
FROM_ZERO_EXAMPLE_CASE = "c1"


def _status_lines() -> str:
    definitions = knowledge.get_cases_doc().get("status_definitions", {})
    # 顺序必须与约定第 4 节一致：严重程度从高到低（有冲突最重，已落实最轻）。
    # “已与客户确认”是系统追加的状态，不让模型输出，所以不出现在提示词里。
    order = ("有冲突", "被改写", "未落实", "需设计师判断", "间接体现", "已落实")
    lines = [f"- {name}：{definitions.get(name, '')}" for name in order if name in definitions]
    return "\n".join(lines)


def _strength_lines() -> str:
    definitions = knowledge.get_cases_doc().get("strength_definitions", {})
    order = ("硬约束", "软偏好", "目标", "描述")
    lines = [f"- {name}：{definitions.get(name, '')}" for name in order if name in definitions]
    return "\n".join(lines)


def _example_block(case_id: str) -> str:
    case = knowledge.get_case(case_id)
    if case is None:
        return ""
    spec = knowledge.case_spec(case)
    utterances = []
    for item in case.get("utterances", []):
        utterances.append(
            {
                "text": item["text"],
                "type": item.get("type", "描述"),
                "strength": item.get("strength", "描述"),
                "mappings": [
                    {
                        "field": mapping["field"],
                        "garment": mapping.get("garment"),
                        "status": mapping["status"],
                        "note": mapping.get("note", ""),
                        "rule_id": mapping.get("rule"),
                    }
                    for mapping in item.get("mappings", [])
                ],
            }
        )
    return (
        "客户口述："
        + case.get("customer_text", "")
        + "\n已有方案："
        + json.dumps(spec, ensure_ascii=False)
        + "\n正确输出："
        + json.dumps({"utterances": utterances}, ensure_ascii=False)
    )


def build_system_prompt(from_zero: bool = False) -> str:
    return SYSTEM_PROMPT_FROM_ZERO if from_zero else SYSTEM_PROMPT


def _semantic_unit_lines() -> str:
    """审计与从零两种提示共用的拆句口径：一句原话 = 一项可独立审计的要求。

    这里只写提示词要求，不做任何代码层面的句子拼接或按长度切分；
    原话本身仍必须是客户口述的连续原文片段，由 `_locate_spans` 严格校验。
    """
    return "\n".join(
        [
            "1. 一句原话 = 一项可独立审计的设计要求，不是一个标点，也不是整段口述。",
            "2. 不要机械按逗号拆。紧密相连的原因、条件要跟它修饰的要求留在同一段连续原文里，",
            "   例如“因为脖子短，领口开低一点”；提出要求后紧跟的允许替代项（“或……”“任意一种都行”",
            "   “二选一”）也要和这条要求留在同一段，例如“面料要回纹或水波纹，二选一”。",
            "3. 不要把整段口述合成一句。不同设计参数、不同单品要分开：例如“领高低一点、领宽窄一点”",
            "   是两项要求（即使都属于领型）；“外套收腰、裙子宽松”也要分成外套和裙子两项，",
            "   并在 garment 里分别注明单品。",
            "4. 区分允许替代与组合：“或／任意／二选一”是在给出的范围里允许任选其一，属于同一项要求的",
            "   允许替代；“和／拼色／搭配”表示要同时满足的组合，不能当成允许替代。",
            "5. 否定就是否定：“不收腰”“领子不要太高”就是不要，不能被改写成相反的正向要求；",
            "   text 必须原样包含否定词，不许删掉或反义改写。",
            "6. 口述里可能出现没见过的材料名，不能仅凭名字陌生就断言“现实中不存在／做不出来”，",
            "   只能照原样记录客户的说法。",
        ]
    )


def _from_zero_example_block(case_id: str) -> str:
    """从零模式的示例：只示范“拆句 + 指出涉及哪些字段”，不示范审计结论。"""
    case = knowledge.get_case(case_id)
    if case is None:
        return ""
    utterances = []
    for item in case.get("utterances", []):
        mappings = []
        for mapping in item.get("mappings", []):
            mappings.append(
                {
                    "field": mapping["field"],
                    "garment": mapping.get("garment"),
                    "status": "已落实",
                    "note": f"客户在这句里对【{mapping['field']}】提出了要求",
                    "rule_id": mapping.get("rule"),
                }
            )
        utterances.append(
            {
                "text": item["text"],
                "type": item.get("type", "描述"),
                "strength": item.get("strength", "描述"),
                "mappings": mappings,
            }
        )
    return (
        "客户口述："
        + case.get("customer_text", "")
        + "\n正在生成的初稿：还没有（本次从零生成）"
        + "\n正确输出："
        + json.dumps({"utterances": utterances}, ensure_ascii=False)
    )


def build_from_zero_prompt(text: str) -> str:
    """从零生成方案时的抽取提示：只拆句、指出每句涉及哪些字段。"""
    examples = _from_zero_example_block(FROM_ZERO_EXAMPLE_CASE)
    return f"""【客户口述】（原文，一个字都不能改）
{text}

【本次任务】还没有设计方案，这次是先从口述生成初稿。所以不要判断“在方案里落实得怎么样”，
只做两件事：把口述拆成原话；指出每句原话对哪些设计字段提出了要求。

【设计字段】只能取这八个，顺序固定：{'、'.join(FIELD_ORDER)}

【原话强度】只能取这些值：
{_strength_lines()}

【拆句单位：可独立审计的一项要求】（审计与从零两种模式同一口径）
{_semantic_unit_lines()}

【拆句要求】
1. 把口述拆成若干句原话，每句是上述一项可独立审计的要求，尽量就是原文的连续片段；
   text 必须能在【客户口述】里一字不差地找到，
   不要改字、不要补字、不要用省略号拼接、不要调换顺序。
2. 每句给一个 type，例如 品类／风格／场景／身形／领型／松量／纹样／活动／描述。
3. 每句列出它对哪些设计字段提出了要求，每个字段一条 mapping，至少一条。
4. garment 只在多件套时写单品名（如“外套”“连衣裙”），否则填 null。
5. note 用一句中文写清楚：这句原话对这个字段提出了什么要求。
6. rule_id 只在原话明确命中身形规则（肚腩、脖子短、领子窄、肩背宽厚、抬手、坐着、溜肩、内搭）时
   写 R1–R8，否则填 null，不要编造规则编号。
7. status 一律填「已落实」：初稿还没有生成，落实情况由程序在匹配出方案之后统一复核，
   你不要猜“落实”或“未落实”。

【输出格式】只输出这个 JSON，不要任何解释：
{{"utterances":[{{"text":"原话","type":"类型","strength":"强度","mappings":[{{"field":"字段","garment":null,"status":"已落实","note":"这句对字段的要求","rule_id":null}}]}}]}}

【参考示例】（只示范格式，不要照抄内容，也不要引用示例里的案例）
{examples}
"""


def build_user_prompt(text: str, spec_values: dict[str, str]) -> str:
    spec_lines = "\n".join(
        f"- {field}：{(spec_values.get(field) or '').strip() or '（未写）'}"
        for field in FIELD_ORDER
    )
    examples = "\n\n".join(
        f"示例{EXAMPLE_LABELS[index] if index < len(EXAMPLE_LABELS) else index + 1}：\n"
        f"{_example_block(case_id)}"
        for index, case_id in enumerate(EXAMPLE_CASES)
    )
    return f"""【客户口述】（原文，一个字都不能改）
{text}

【已有设计方案】（判断状态的唯一依据；每栏是方案现在的写法）
{spec_lines}

【设计字段】只能取这八个，顺序固定：{'、'.join(FIELD_ORDER)}

【原话状态】只能取这些值，严重程度从高到低：
{_status_lines()}

【原话强度】只能取这些值：
{_strength_lines()}

【拆句单位：可独立审计的一项要求】（审计与从零两种模式同一口径）
{_semantic_unit_lines()}

【拆句要求】
1. 把口述拆成若干句原话，每句是上述一项可独立审计的要求，尽量就是原文的连续片段；
   text 必须能在【客户口述】里一字不差地找到，
   不要改字、不要补字、不要用省略号拼接、不要调换顺序。
2. 每句给一个 type，例如 品类／风格／场景／身形／领型／松量／纹样／活动／描述。
3. 每句列出它对哪些设计字段提出了要求，每个字段一条 mapping，至少一条。
4. garment 只在多件套时写单品名（如“外套”“连衣裙”），否则填 null。
5. note 用一句中文写清楚：方案哪一处落实了、哪一处没有落实。
6. rule_id 只在明确命中身形规则时写 R1–R8，否则填 null，不要编造规则编号。

【判断口径】
- 方案里写法与客户要求方向一致 → 已落实；没有专门字段但能从其他字段看出来 → 间接体现；
  方案换成了别的内容 → 被改写；方向相反 → 有冲突；方案里找不到 → 未落实；
  有对应处理但写到不到位要专业人员定（例如方案没写领宽）→ 需设计师判断。
- 客户明说的具体纹样、颜色没有出现在方案里，不能判“已落实”。
- “暗纹”和“暗花”是同一类含蓄纹样：方案写“植物暗花”，就算落实了客户说的“带一些暗纹”。
- 客户说“不收腰”“不要收腰”，意思是不要腰部收紧，不能反过来当成“要收腰”。
- 客户只要求“宽松/舒展/松弛”，而方案写“收腰/微微收腰”，判有冲突。
- 口述里出现没见过的材料名时，不能仅凭名字陌生就断言“现实中不存在／做不出来”；
  只说明方案里有没有对应的写法，现实可行性留给设计师判断。

【输出格式】只输出这个 JSON，不要任何解释：
{{"utterances":[{{"text":"原话","type":"类型","strength":"强度","mappings":[{{"field":"字段","garment":null,"status":"状态","note":"理由","rule_id":null}}]}}]}}

【参考示例】（只示范格式和判断口径，不要照抄内容，也不要引用示例里的案例）
{examples}
"""


def _locate_spans(text: str, result: ExtractionResult) -> list[tuple[int, int]]:
    """在原文里按顺序定位每句原话；定位不到就是不合格输出（会触发一次重试）。

    三条硬约束：

    1. 每句原话必须是客户口述的连续原文片段；
    2. 所有原话必须按原文顺序出现、互不重叠；
    3. 只在上一句结束位置之后往后找，不做“回退到原文前面再找一遍”的兜底。

    因此，同一片段在原文只出现一次时，不能被两条原话分别定位（否则同一处会重复渲染）；
    但同一短语在原文真实出现两次时，顺序往后查找仍能分别定位。
    找不到就报清楚的中文原因：原文里根本没有这句话，还是位置重叠/顺序颠倒。
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for item in result.utterances:
        piece = item.text
        start = text.find(piece, cursor)
        if start < 0:
            if piece in text:
                raise ValueError(
                    f"原话「{piece}」在客户口述里的位置与前文重叠或顺序颠倒，"
                    "同一片段不能重复定位，请按原文顺序、互不重叠地拆分"
                )
            raise ValueError(f"原话「{piece}」不是客户口述的原文片段")
        end = start + len(piece)
        spans.append((start, end))
        cursor = end
    return spans


def locate_utterances(text: str, result: ExtractionResult) -> list[tuple[int, int]]:
    return _locate_spans(text, result)


def extract_utterances(
    text: str,
    spec_values: dict[str, str],
    *,
    client: LLMClient | None = None,
    from_zero: bool = False,
) -> list[Utterance]:
    """调用大模型拆句，返回带位置的原话列表。失败时抛 `llm.LLMError`。

    `from_zero=True` 时用于从零生成方案：只拆句、指出涉及的字段，落实情况由程序
    在匹配出初稿之后复核，所以这里把每句的映射状态统一重置为“已落实”，不采信模型猜的结论。
    """
    client = client or llm.get_client()
    user_prompt = build_from_zero_prompt(text) if from_zero else build_user_prompt(text, spec_values)
    result = client.request_json(
        build_system_prompt(from_zero),
        user_prompt,
        ExtractionResult,
        post_validate=lambda doc: _locate_spans(text, doc),
    )
    spans = _locate_spans(text, result)

    utterances: list[Utterance] = []
    for index, (item, (start, end)) in enumerate(zip(result.utterances, spans), 1):
        mappings = [
            Mapping(
                field=mapping.field,
                garment=mapping.garment,
                status="已落实" if from_zero else mapping.status,
                note=mapping.note,
                rule_id=mapping.rule_id,
            )
            for mapping in item.mappings
        ]
        utterances.append(
            Utterance(
                id=f"u{index}",
                text=text[start:end],
                start=start,
                end=end,
                type=item.type,
                strength=item.strength,
                status=most_severe([m.status for m in mappings]),
                mappings=mappings,
                resolved_by=None,
            )
        )
    return utterances

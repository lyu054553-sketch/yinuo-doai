"""工作台辅助功能：相似旧单、面料备选、复述确认。

- 相似旧单、面料备选全部是程序规则，不调大模型：用本单客户原话去核对旧单做法 / 候选面料，
  能不能用由现有审计规则判断，不让模型拍板；
- 复述确认只让大模型做一件事：把八栏专业参数改写成客户听得懂的话、把“可能听错”的几处
  问成是非题。模型不可用或输出不合格时退回模板，照样能用；
- 每次改方案都升一个版本、写一条台账；替换后相关原话一律交给设计师复核，不冒充客户已确认。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from .. import knowledge
from ..llm import LLMClient, LLMError
from ..schemas import (
    DesignerItem,
    LedgerEntry,
    Restatement,
    RestatementPoint,
    Session,
    SpecField,
    SpecVersion,
    Utterance,
    most_severe,
)
from ..store import now_iso
from . import audit, match

FIELD_ORDER = knowledge.FIELD_ORDER
PROBLEM_STATUSES = ("有冲突", "被改写", "未落实")
EMPTY_VALUES = ("", "无特殊需求", "无", "未写")

# 用来判断“两单是不是同一类需求”的词：偏好词 + 身形/场景/穿着词。只看是否同时出现，不做语义猜测。
VOCAB: tuple[str, ...] = tuple(
    dict.fromkeys(
        match.PREFERENCE_WORDS
        + match.LOW_COLLAR_WORDS
        + match.NARROW_WORDS
        + match.LOOSE_WORDS
        + (
            "肚", "腩", "肩", "脖子", "溜肩", "宽厚", "身形", "偏高", "内搭", "抬手", "坐",
            "演出", "舞台", "上镜", "商务", "休闲", "梅花", "暗纹", "暗花", "提花", "外套",
            "连衣裙", "两件", "套装", "紧绷", "束缚", "压肩", "卡", "活动", "领子", "气质",
        )
    )
)
RULE_LABELS = {
    "R1": "有小肚子",
    "R2": "领子不能高",
    "R3": "要显脖子细",
    "R4": "肩背宽厚",
    "R5": "抬手方便",
    "R6": "坐着方便",
    "R7": "溜肩",
    "R8": "要搭内搭",
}


def _profile(texts: list[str]) -> set[str]:
    joined = " ".join(texts)
    words = {word for word in VOCAB if word and word in joined}
    for rule_id, triggers in match.RULE_DEFS:
        if any(word in joined for word in triggers):
            words.add(rule_id)
    return words


def _labels(words: set[str]) -> list[str]:
    rules = [RULE_LABELS[w] for w in sorted(words) if w in RULE_LABELS]
    plain = [w for w in sorted(words, key=len, reverse=True) if w not in RULE_LABELS]
    # 去掉被更长词包含的短词（“领子不要太高”已包含“领子”）
    kept: list[str] = []
    for word in plain:
        if not any(word in other for other in kept + rules):
            kept.append(word)
    return (rules + kept)[:8]


def _related(session: Session, field: str) -> list[Utterance]:
    return [u for u in session.utterances if any(m.field == field for m in u.mappings)]


def _norm(text: str) -> str:
    return "".join(str(text).split())


def _rule_check(session: Session, field: str, value: str) -> list[tuple[str, str]]:
    """拿本单客户原话去核对某一栏的候选做法，返回规则判出的问题（状态, 说明）。"""
    current = session.spec_versions[-1]
    clones: list[Utterance] = []
    for utterance in _related(session, field):
        clone = utterance.model_copy(deep=True)
        clone.mappings = [m for m in clone.mappings if m.field == field]
        for mapping in clone.mappings:
            mapping.status = "已落实"
            mapping.note = ""
        clones.append(clone)
    fields = {name: item.model_copy(deep=True) for name, item in current.fields.items()}
    fields[field] = SpecField(
        value=value, resource_ids=[], utterance_ids=[], origin="资源库", confirmed=False
    )
    audit.apply_program_rules(clones, fields)
    return [(m.status, m.note) for clone in clones for m in clone.mappings if m.status != "已落实"]


# ---------------------------------------------------------------- 相似旧单


def _case_label(case: dict) -> str:
    return knowledge.CASE_LABELS.get(case["id"], case["id"])


def _verdict(session: Session, case: dict, field: str, value: str) -> tuple[str, str]:
    current_value = session.spec_versions[-1].fields[field].value
    if _norm(value) == _norm(current_value):
        return "和本单一样", "旧单和本单这一栏写法相同。"
    related = _related(session, field)
    if field == "品类":
        return "要按本单改", f"单品不同：旧单是「{value}」，本单是「{current_value}」。"
    if value.strip() in EMPTY_VALUES:
        return ("旧单没要求", "旧单这一栏没有具体做法。")
    if not related:
        return "本单没提", "客户没说这一栏，旧单做法可作参考。"
    problems = _rule_check(session, field, value)
    blocking = [note for status, note in problems if status in PROBLEM_STATUSES]
    if blocking:
        return "不能照搬", blocking[0]
    case_texts = [
        u.get("text", "")
        for u in case.get("utterances", [])
        if any(m.get("field") == field for m in u.get("mappings", []))
    ]
    common = _profile([u.text for u in related]) & _profile(case_texts + [value])
    if any(status == "需设计师判断" for status, _ in problems):
        return "要按本单改", problems[0][1]
    if common:
        return "可直接用", f"本单和旧单在这一栏都提到：{'、'.join(_labels(common))}。"
    return "要按本单改", "旧单这一栏针对的要求和本单不同，需要按本单的话调整。"


def similar_cases(session: Session, limit: int = 3) -> list[dict]:
    """按客户原话里共同出现的需求词和身形规则，找最像的旧单，并逐栏判断能不能用。"""
    own = session.input.sample_id
    mine = _profile([session.input.text])
    results: list[dict] = []
    for case in knowledge.get_cases():
        if knowledge.is_blocked_sample(case) or case.get("id") == own:
            continue
        # 原话和旧单一字不差，就是同一单，不算“相似旧单”
        if _norm(case.get("customer_text", "")) == _norm(session.input.text):
            continue
        theirs = _profile([case.get("customer_text", "")])
        shared = mine & theirs
        if not shared:
            continue
        spec = knowledge.case_spec(case)
        fields = []
        for field in FIELD_ORDER:
            value = spec.get(field, "")
            verdict, reason = _verdict(session, case, field, value)
            fields.append(
                {
                    "field": field,
                    "value": value,
                    "resource_ids": knowledge.find_resource_ids(field, value),
                    "verdict": verdict,
                    "reason": reason,
                }
            )
        results.append(
            {
                "case_id": case["id"],
                "label": _case_label(case),
                "title": case.get("title", ""),
                "customer_text": case.get("customer_text", ""),
                "shared": _labels(shared),
                "score": len(shared),
                "reusable": sum(1 for f in fields if f["verdict"] == "可直接用"),
                "fields": fields,
            }
        )
    results.sort(key=lambda item: (-item["score"], -item["reusable"], item["case_id"]))
    return results[:limit]


# ---------------------------------------------------------------- 改一栏（共用）


def _replace_field(
    session: Session,
    field: str,
    value: str,
    resource_ids: list[str],
    *,
    action: str,
    reason: str,
    summary: str,
    detail: str,
    basis: str,
) -> Session:
    """把一栏换成新做法：升版本、写台账；这一栏原有出入的原话交给设计师复核。"""
    current = session.spec_versions[-1]
    if _norm(current.fields[field].value) == _norm(value):
        return session
    new = session.model_copy(deep=True)
    fields = {name: item.model_copy(deep=True) for name, item in current.fields.items()}
    fields[field] = SpecField(
        value=value,
        resource_ids=list(resource_ids),
        utterance_ids=list(current.fields[field].utterance_ids),
        origin="资源库" if resource_ids else "人工修改",
        confirmed=False,
    )
    version = current.version + 1
    new.spec_versions = list(new.spec_versions) + [
        SpecVersion(version=version, created_at=now_iso(), reason=reason, fields=fields)
    ]

    problems = {note for status, note in _rule_check(new, field, value) if status in PROBLEM_STATUSES}
    handed: list[str] = []
    for utterance in new.utterances:
        touched = False
        for mapping in utterance.mappings:
            if mapping.field != field or mapping.status not in PROBLEM_STATUSES:
                continue
            if problems:
                mapping.note = f"换成「{value}」后程序复核仍有出入：{next(iter(problems))}"
                continue
            mapping.status = "需设计师判断"
            mapping.note = f"{basis}，是否满足这句原话由设计师确认。"
            touched = True
        if touched:
            handed.append(utterance.id)
        utterance.status = (
            "已与客户确认" if utterance.resolved_by else most_severe([m.status for m in utterance.mappings])
        )
    if handed:
        by_id = {u.id: u for u in new.utterances}
        quotes = "、".join(f"「{by_id[uid].text}」" for uid in handed)
        new.designer_items = list(new.designer_items) + [
            DesignerItem(
                id=f"d{len(new.designer_items) + 1}",
                utterance_ids=handed,
                field=field,
                text=f"{basis}（{value}）。请确认是否满足客户原话 {quotes}。",
            )
        ]
    new.ledger = list(new.ledger) + [
        LedgerEntry(
            version=version,
            at=now_iso(),
            action=action,
            summary=summary,
            detail=detail,
            utterance_ids=list(current.fields[field].utterance_ids),
            changed_fields=[field],
        )
    ]
    return new


def reuse_case_field(session: Session, case_id: str, field: str) -> Session:
    if field not in FIELD_ORDER:
        raise ValueError("设计字段不正确")
    case = knowledge.get_case(case_id)
    if case is None or knowledge.is_blocked_sample(case):
        raise ValueError("找不到这个旧单")
    value = knowledge.case_spec(case).get(field, "").strip()
    if value in EMPTY_VALUES:
        raise ValueError("旧单这一栏没有具体做法，不能引用")
    verdict, why = _verdict(session, case, field, value)
    if verdict == "不能照搬":
        raise ValueError(f"这一栏和本单客户原话有冲突，不能照搬：{why}")
    label = _case_label(case)
    old = session.spec_versions[-1].fields[field].value
    return _replace_field(
        session,
        field,
        value,
        knowledge.find_resource_ids(field, value),
        action="引用旧案",
        reason=f"引用{label}的【{field}】做法",
        summary=f"【{field}】引用{label}的做法",
        detail=f"原来：{old or '（空）'}；改为{label}的做法：{value}。判断：{verdict}，{why}",
        basis=f"业务员引用了{label}的做法",
    )


# ---------------------------------------------------------------- 面料备选

FABRIC_REASON_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "风格适配",
        ("中式", "正式", "优雅", "端庄", "气质", "低调", "内敛", "简约", "大气", "喜庆", "见家长", "过年", "场合", "商务", "有质感", "高级"),
        ("中式", "正式", "优雅", "端庄", "含蓄", "低调", "高级", "喜庆", "华丽", "稳重", "暗纹", "提花", "云纹", "光泽"),
    ),
    (
        "体感适配",
        ("舒服", "舒适", "宽松", "紧绷", "束缚", "活动", "光泽", "质感", "垂", "软", "透气", "保暖", "抬手", "方便"),
        ("垂感", "柔和", "柔软", "手感", "挺而不硬", "不易贴身", "透气", "弹力", "活动方便", "柔滑", "顺滑", "光泽", "哑光", "保暖", "舒适"),
    ),
    (
        "体型适配",
        ("肚", "腩", "肩", "身形", "背", "显瘦", "修身", "偏高", "身高", "胖"),
        ("挺括", "骨感", "不易贴身", "挺而不硬", "垂感", "显瘦"),
    ),
)


def fabric_reasons(session: Session, traits: list[str]) -> list[dict]:
    """按上服选面料的三条依据写理由：只引用客户原话和资源库记录的面料特点。"""
    reasons = []
    for label, asks, wanted in FABRIC_REASON_RULES:
        quotes = [u.text for u in session.utterances if any(word in u.text for word in asks)][:3]
        hits = [t for t in traits if any(word in t for word in wanted)]
        if not quotes:
            text = "客户没有提到这方面的要求。"
        elif not hits:
            text = f"客户：{''.join(f'「{q}」' for q in quotes)}；资源库里没有记录这款面料在这方面的特点，需设计师判断。"
        else:
            text = f"客户：{''.join(f'「{q}」' for q in quotes)}；面料特点：{'、'.join(hits)}。"
        reasons.append({"label": label, "text": text, "matched": bool(quotes and hits)})
    return reasons


def _fabric_traits(item: dict) -> list[str]:
    traits = [str(t) for t in (item.get("traits") or [])]
    for key, value in knowledge.attr(item["id"]).items():
        if value is True:
            traits.append(str(key))
    return list(dict.fromkeys(traits))


def fabric_options(session: Session, limit: int = 3) -> list[dict]:
    """当前面料 + 按本单原话排出的候选面料，每块带三条依据和（模拟）库存。"""
    wants = match._collect_wants(session.utterances)
    fabric_wants = wants.get("面料", [])
    texts = match._joined(fabric_wants)
    hint = tuple(
        word
        for want in wants.get("装饰纹样", [])
        for token in match._pattern_tokens(want.text)
        for group in match.PATTERN_GROUPS
        if token in group
        for word in group
        if token not in ("暗花", "暗纹")
    )
    pool = [item for item in match._candidates("fabrics") if match._fabric_allow(item, texts)]
    pool = match._wanted_filter(pool, fabric_wants)
    ranked = sorted(
        pool,
        key=lambda item: (-match._score(item, fabric_wants, hint), -match._dev_count(item), item["id"]),
    )
    current = session.spec_versions[-1].fields
    current_ids = list(current["面料"].resource_ids)
    plan_ids = [rid for f in ("装饰纹样", "色彩") for rid in current[f].resource_ids]

    chosen: list[dict] = [knowledge.resource_by_id(rid) for rid in current_ids if knowledge.resource_by_id(rid)]
    for item in ranked:
        if len(chosen) >= limit + len(current_ids):
            break
        if item["id"] not in current_ids:
            chosen.append(item)

    options = []
    for item in chosen:
        traits = _fabric_traits(item)
        stock = knowledge.stock_for([item["id"], *plan_ids])[:1]
        options.append(
            {
                "id": item["id"],
                "name": item.get("name", ""),
                "source": knowledge.resource_source_text(item["id"]),
                "simulated": knowledge.SIM_SOURCE in (item.get("sources") or []),
                "traits": traits,
                "current": item["id"] in current_ids,
                "reasons": fabric_reasons(session, traits),
                "stock": stock[0] if stock else None,
            }
        )
    return options


def choose_fabric(session: Session, resource_id: str) -> Session:
    item = knowledge.resource_by_id(resource_id)
    if item is None or item not in match._candidates("fabrics"):
        raise ValueError("这块面料不在可推荐的面料库里")
    name = str(item.get("name", ""))
    old = session.spec_versions[-1].fields["面料"].value
    reasons = fabric_reasons(session, _fabric_traits(item))
    plan = session.spec_versions[-1].fields
    stock = knowledge.stock_for([resource_id] + [rid for f in ("装饰纹样", "色彩") for rid in plan[f].resource_ids])
    stock_text = (
        f"；仓库（模拟）：{stock[0]['sku']} {stock[0]['colorway']} {stock[0]['status']}"
        if stock
        else "；仓库（模拟）暂无对得上的货"
    )
    return _replace_field(
        session,
        "面料",
        name,
        [resource_id],
        action="换面料",
        reason=f"换面料：{name}",
        summary=f"【面料】换成 {resource_id} {name}",
        detail=f"原来：{old or '（空）'}。" + "；".join(f"{r['label']}：{r['text']}" for r in reasons) + stock_text,
        basis=f"业务员把面料换成了 {resource_id} {name}",
    )


# ---------------------------------------------------------------- 复述确认

POINT_PRIORITY = ("有冲突", "被改写", "未落实", "需设计师判断", "间接体现")


def _points(session: Session, limit: int = 3) -> list[tuple[Utterance, str]]:
    """挑出最可能听错的几句：先看有出入的，再看软偏好。每句只取最严重的那一栏。"""
    scored: list[tuple[int, Utterance, str]] = []
    for utterance in session.utterances:
        if utterance.resolved_by:
            continue
        for rank, status in enumerate(POINT_PRIORITY):
            mapping = next((m for m in utterance.mappings if m.status == status), None)
            if mapping is not None:
                scored.append((rank, utterance, mapping.field))
                break
        else:
            if utterance.strength == "软偏好" and utterance.mappings:
                scored.append((len(POINT_PRIORITY), utterance, utterance.mappings[0].field))
    scored.sort(key=lambda row: row[0])
    picked: list[tuple[Utterance, str]] = []
    for _, utterance, field in scored:
        if len(picked) >= limit:
            break
        picked.append((utterance, field))
    return picked


def _plain(value: str) -> str:
    return re.sub(r"[A-Z]+\d+", "", value).strip("；; ")


def _template(session: Session, points: list[tuple[Utterance, str]]) -> tuple[str, list[str]]:
    fields = session.spec_versions[-1].fields
    parts = [
        f"{name}：{_plain(fields[name].value)}"
        for name in FIELD_ORDER
        if fields[name].value and fields[name].value not in EMPTY_VALUES
    ]
    text = "跟您确认一下我们记下的要求——" + "；".join(parts) + "。"
    questions = [
        f"您说「{u.text}」，我们现在写的是「{_plain(fields[field].value) or '还没写'}」，这样理解对吗？"
        for u, field in points
    ]
    return text, questions


class _LLMPoint(BaseModel):
    id: str
    question: str


class _LLMRestatement(BaseModel):
    text: str
    points: list[_LLMPoint]


SYSTEM = (
    "你是上海一家海派定制服装店的资深业务员，正在当面跟客户复述需求。"
    "规则：1. 只能复述方案里已经写的内容，不能新增、不能承诺价格和工期；"
    "2. 用客户听得懂的大白话，不出现资源编号、英文代号和专业术语堆砌，150 字以内；"
    "3. 每个“可能听错”的点改写成一句是非题，必须用「」原样引用客户那句原话，再说我们的理解，以“对吗？”结尾；"
    "4. 只输出 JSON：{\"text\": \"...\", \"points\": [{\"id\": \"p1\", \"question\": \"...\"}]}。"
)


def build_restatement(session: Session, client: LLMClient | None) -> Restatement:
    points = _points(session)
    ids = [f"p{index + 1}" for index in range(len(points))]
    fields = session.spec_versions[-1].fields
    text, questions = _template(session, points)
    source = "template"
    if client is not None:
        plan = "\n".join(f"- {name}：{_plain(fields[name].value) or '（空）'}" for name in FIELD_ORDER)
        asks = "\n".join(
            f"- {pid}：客户原话「{u.text}」；方案【{field}】写的是「{_plain(fields[field].value) or '还没写'}」"
            for pid, (u, field) in zip(ids, points)
        )
        user = f"客户原话：{session.input.text}\n\n当前方案：\n{plan}\n\n可能听错的点：\n{asks or '（无）'}"

        def check(result: _LLMRestatement) -> None:
            if [p.id for p in result.points] != ids:
                raise ValueError(f"points 的 id 必须依次是 {ids}")
            for point, (utterance, _) in zip(result.points, points):
                if utterance.text[:4] not in point.question:
                    raise ValueError(f"{point.id} 必须用「」引用客户原话「{utterance.text}」")
            if not result.text.strip():
                raise ValueError("text 不能为空")

        try:
            result = client.request_json(SYSTEM, user, _LLMRestatement, post_validate=check)
            text = result.text.strip()
            questions = [p.question.strip() for p in result.points]
            source = "llm"
        except LLMError:
            pass
    return Restatement(
        version=session.spec_versions[-1].version,
        created_at=now_iso(),
        text=text,
        points=[
            RestatementPoint(id=pid, field=field, utterance_ids=[u.id], question=question)
            for pid, (u, field), question in zip(ids, points, questions)
        ],
        source=source,
    )


def save_restatement(session: Session, restatement: Restatement) -> Session:
    new = session.model_copy(deep=True)
    new.restatement = restatement
    new.ledger = list(new.ledger) + [
        LedgerEntry(
            version=restatement.version,
            at=now_iso(),
            action="复述确认",
            summary=f"生成复述，列出 {len(restatement.points)} 处可能听错的地方，待客户逐条确认",
            detail=("复述由大模型改写成口语" if restatement.source == "llm" else "复述按方案模板生成")
            + "：" + restatement.text,
            utterance_ids=[uid for p in restatement.points for uid in p.utterance_ids],
            changed_fields=[],
        )
    ]
    return new


def answer_restatement(session: Session, point_id: str, answer: str, note: str = "") -> Session:
    if session.restatement is None:
        raise ValueError("还没有生成复述")
    if answer not in ("对", "不对"):
        raise ValueError("只能回答“对”或“不对”")
    note = (note or "").strip()
    if answer == "不对" and not note:
        raise ValueError("客户说不对时，请记下客户的原意，交给设计师")
    new = session.model_copy(deep=True)
    point = next((p for p in new.restatement.points if p.id == point_id), None)
    if point is None:
        raise ValueError("找不到这条确认项")
    point.answer = answer
    point.note = note
    by_id = {u.id: u for u in new.utterances}
    quote = "、".join(f"「{by_id[uid].text}」" for uid in point.utterance_ids if uid in by_id)
    if answer == "不对":
        new.designer_items = list(new.designer_items) + [
            DesignerItem(
                id=f"d{len(new.designer_items) + 1}",
                utterance_ids=list(point.utterance_ids),
                field=point.field,
                text=f"复述确认时客户指出理解有误：{quote}，客户原意：{note}。",
            )
        ]
    new.ledger = list(new.ledger) + [
        LedgerEntry(
            version=new.spec_versions[-1].version,
            at=now_iso(),
            action="复述确认",
            summary=f"客户对 {quote} 的复述：{answer}" + (f"（客户原意：{note}）" if note else ""),
            detail=point.question,
            utterance_ids=list(point.utterance_ids),
            changed_fields=[],
        )
    ]
    return new

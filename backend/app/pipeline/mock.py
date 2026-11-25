"""按照 data/cases.json 的标注构造第一个会话（示例模式，不调大模型）。

示例模式的定位是“忠实回放”：拆句、映射状态、原话状态一律照抄标注，
不再套用任何程序规则去改写标注结论。程序复核（audit.apply_program_rules）
只用于真实大模型解构的结果，避免标注答案被规则“抢跑”。
"""

from __future__ import annotations

from .. import knowledge
from ..schemas import (
    CreateSessionRequest,
    LedgerEntry,
    Mapping,
    Session,
    SessionInput,
    SpecField,
    SpecVersion,
    Utterance,
)
from ..store import new_session_id, now_iso
from . import audit
from .questions import build_plan

FIELD_ORDER = knowledge.FIELD_ORDER


def _locate(text: str, annotations: list[dict]) -> list[tuple[int, int]]:
    """把标注的每句原话在原文里定位，得到 start/end（end 不含）。"""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for annotation in annotations:
        piece = annotation.get("text", "")
        if not piece:
            raise ValueError("示例标注缺少原话文字")
        start = text.find(piece, cursor)
        if start < 0:
            start = text.find(piece)
        if start < 0:
            raise ValueError(f"示例标注里的原话「{piece}」不在原文中")
        end = start + len(piece)
        spans.append((start, end))
        cursor = end
    return spans


def _build_utterances(text: str, annotations: list[dict]) -> list[Utterance]:
    spans = _locate(text, annotations)
    utterances: list[Utterance] = []
    for index, (annotation, (start, end)) in enumerate(zip(annotations, spans), 1):
        mappings = []
        for item in annotation.get("mappings", []):
            mappings.append(
                Mapping(
                    field=item["field"],
                    garment=item.get("garment"),
                    status=item["status"],
                    note=item.get("note", ""),
                    rule_id=item.get("rule"),
                )
            )
        if not mappings:
            raise ValueError(f"示例标注里的原话「{annotation['text']}」没有对应的设计字段")
        utterance = Utterance(
            id=f"u{index}",
            text=annotation["text"],
            start=start,
            end=end,
            type=annotation.get("type", "描述"),
            strength=annotation.get("strength", "描述"),
            status=audit.most_severe([m.status for m in mappings]),
            mappings=mappings,
            resolved_by=None,
        )
        utterances.append(utterance)
    return utterances


def _build_spec_fields(
    spec_values: dict[str, str], utterances: list[Utterance]
) -> dict[str, SpecField]:
    fields: dict[str, SpecField] = {}
    for field in FIELD_ORDER:
        value = (spec_values.get(field) or "").strip()
        fields[field] = SpecField(
            value=value,
            resource_ids=knowledge.find_resource_ids(field, value),
            utterance_ids=[u.id for u in utterances if any(m.field == field for m in u.mappings)],
            origin="已有方案" if value else "无出处",
            confirmed=False,
        )
    return fields


def _audit_ledger(version: int, utterances: list[Utterance]) -> LedgerEntry:
    problems = audit.problem_utterances(utterances)
    counts = {"有冲突": 0, "被改写": 0, "未落实": 0, "需设计师判断": 0}
    details = []
    changed_fields: list[str] = []
    for utterance in problems:
        for mapping in utterance.mappings:
            if mapping.status in counts:
                counts[mapping.status] += 1
                if mapping.field not in changed_fields:
                    changed_fields.append(mapping.field)
                details.append(
                    f"「{utterance.text}」→【{mapping.field}】{mapping.status}：{mapping.note}"
                )
    parts = [f"{counts[key]} 处{key}" for key in ("有冲突", "被改写", "未落实") if counts[key]]
    for utterance in utterances:
        if any(m.status == "需设计师判断" for m in utterance.mappings):
            counts["需设计师判断"] += 1
    if counts["需设计师判断"]:
        parts.append(f"{counts['需设计师判断']} 处需设计师判断")
    summary = "审计：逐句核对客户原话与已有方案，" + ("，".join(parts) if parts else "没有发现问题") + "。"
    return LedgerEntry(
        version=version,
        at=now_iso(),
        action="审计",
        summary=summary,
        detail="\n".join(details) if details else "所有原话都能在方案里找到对应处理。",
        utterance_ids=[u.id for u in problems],
        changed_fields=changed_fields,
    )


def build_mock_session(request: CreateSessionRequest) -> Session:
    if not request.sample_id:
        raise ValueError("示例模式只支持示例案例")
    case = knowledge.get_case(request.sample_id)
    if case is None:
        raise ValueError("找不到这个示例案例")
    if knowledge.is_blocked_sample(case):
        raise ValueError("这个案例是留出评测集，不开放演示。")
    if request.text and request.text.strip():
        text = request.text.strip()
    else:
        text = case.get("customer_text", "")

    annotations = case.get("utterances", [])
    utterances = _build_utterances(text, annotations)

    spec_values = knowledge.case_spec(case)
    if request.existing_spec:
        for field in FIELD_ORDER:
            if field in request.existing_spec:
                spec_values[field] = knowledge.normalize_spec_value(
                    request.existing_spec[field]
                )

    fields = _build_spec_fields(spec_values, utterances)
    # 示例模式下标注即答案：不调用 audit.apply_program_rules，
    # 否则程序规则会把标注里“已落实”的暗纹改成“未落实”，抢占“梅花”的追问。
    # 这里只按标注重新汇总一次原话状态（不改变任何映射的 status）。
    audit.recompute_statuses(utterances)

    plan = build_plan(utterances, fields, case)

    created_at = now_iso()
    version_one = SpecVersion(
        version=1,
        created_at=created_at,
        reason="导入已有方案",
        fields=fields,
    )
    import_ledger = LedgerEntry(
        version=1,
        at=created_at,
        action="导入",
        summary=f"导入{case.get('title', '示例案例')}的客户口述和已有方案。",
        detail=(
            f"拆出 {len(utterances)} 句原话，覆盖 "
            f"{len({m.field for u in utterances for m in u.mappings})} 个设计字段。"
        ),
        utterance_ids=[u.id for u in utterances],
        changed_fields=list(FIELD_ORDER),
    )

    return Session(
        id=new_session_id(),
        title=request.title or case.get("title", "示例案例"),
        mode="审计已有方案",
        source="mock",
        created_at=created_at,
        input=SessionInput(
            text=text,
            existing_spec={k: v for k, v in spec_values.items()},
            sample_id=request.sample_id,
        ),
        utterances=utterances,
        spec_versions=[version_one],
        questions=plan["questions"],
        designer_items=plan["designer_items"],
        skipped=plan["skipped"],
        ledger=[import_ledger, _audit_ledger(1, utterances)],
        reference=None,
        images=[],
    )

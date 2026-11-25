"""真实大模型模式的会话构造。

两条路径：

* 审计已有方案：客户口述 + 已有方案 → 拆句抽取（大模型）→ 补足关键映射 →
  方案第 1 版（用已有方案）→ 程序规则复核 → 生成问题；
* 从零生成方案：只给客户口述 → 拆句抽取（大模型，只拆句和指出涉及字段）→
  程序资源匹配（`pipeline/match.py`）生成第 1 版 → **对这份初稿**做程序复核 →
  生成问题。

“从零”只在明确没有给方案时走：只要请求里带了非空的 `existing_spec` 就走审计路径；
只给 `text` 时一律从零，不会因为带了 `sample_id` 就偷偷拿案例的旧方案去审计。
两套路径共用问题、改版、交接单这些纯程序逻辑，示例模式（`pipeline/mock.py`）不经过这里。
"""

from __future__ import annotations

from .. import knowledge
from ..llm import LLMClient, get_client
from ..schemas import (
    CreateSessionRequest,
    LedgerEntry,
    Session,
    SessionInput,
    SpecVersion,
)
from ..store import new_session_id, now_iso
from . import audit, match
from .extract import extract_utterances
from .mock import _audit_ledger, _build_spec_fields
from .questions import build_plan

FIELD_ORDER = knowledge.FIELD_ORDER


def _empty_spec() -> dict[str, str]:
    return {field: "" for field in FIELD_ORDER}


def _explicit_spec_values(request: CreateSessionRequest) -> dict[str, str] | None:
    """请求里明确给了方案（至少一栏非空）时，返回归一化后的八栏；否则返回 None。"""
    if not request.existing_spec:
        return None
    values = _empty_spec()
    has_value = False
    for field in FIELD_ORDER:
        raw = request.existing_spec.get(field)
        if raw is None:
            continue
        values[field] = knowledge.normalize_spec_value(raw)
        if values[field]:
            has_value = True
    return values if has_value else None


def _resolve_input(
    request: CreateSessionRequest,
) -> tuple[dict | None, str, dict[str, str], bool]:
    """返回 (案例, 口述文字, 第 1 版的方案取值, 是否从零生成)。"""
    case = None
    if request.sample_id:
        case = knowledge.get_case(request.sample_id)
        if case is None:
            raise ValueError("找不到这个示例案例")
        if knowledge.is_blocked_sample(case):
            raise ValueError("这个案例是留出评测集，不开放演示。")

    text = (request.text or "").strip()
    if not text and case is not None:
        text = (case.get("customer_text") or "").strip()
    if not text:
        raise ValueError("客户口述不能为空，请先把客户说的话填进来。")

    explicit = _explicit_spec_values(request)
    if explicit is not None:
        # 明确给了方案：走审计，方案只用请求里给的，不额外拼案例里的旧方案
        return case, text, explicit, False

    if not (request.text or "").strip():
        # 只给案例编号、没给口述：这是“审计这个示例的已有方案”
        return case, text, knowledge.case_spec(case), False

    # 给了口述、没给方案：从零生成，绝不拿案例旧方案冒充
    return case, text, _empty_spec(), True


def build_llm_session(
    request: CreateSessionRequest, *, client: LLMClient | None = None
) -> Session:
    """真实模式：解构口述，生成第 1 版方案（审计或从零），返回会话。"""
    case, text, spec_values, from_zero = _resolve_input(request)
    client = client or get_client()

    if from_zero:
        return _build_from_zero(request, case, text, client=client)

    utterances = extract_utterances(text, spec_values, client=client)
    audit.ensure_mappings(utterances, spec_values)

    fields = _build_spec_fields(spec_values, utterances)
    audit.apply_program_rules(utterances, fields)
    audit.recompute_statuses(utterances)

    plan = build_plan(utterances, fields, case or {})

    created_at = now_iso()
    version_one = SpecVersion(
        version=1,
        created_at=created_at,
        reason="导入已有方案",
        fields=fields,
    )

    covered = {mapping.field for utterance in utterances for mapping in utterance.mappings}
    import_ledger = LedgerEntry(
        version=1,
        at=created_at,
        action="导入",
        summary="导入客户口述和已有方案，由大模型拆分原话。",
        detail=f"拆出 {len(utterances)} 句原话，覆盖 {len(covered)} 个设计字段。",
        utterance_ids=[utterance.id for utterance in utterances],
        changed_fields=list(FIELD_ORDER),
    )

    title = (request.title or "").strip()
    if not title and case is not None:
        title = case.get("title", "")

    return Session(
        id=new_session_id(),
        title=title or "客户口述审计",
        mode="审计已有方案",
        source="llm",
        created_at=created_at,
        input=SessionInput(
            text=text,
            existing_spec={field: spec_values.get(field, "") for field in FIELD_ORDER},
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


def _build_from_zero(
    request: CreateSessionRequest,
    case: dict | None,
    text: str,
    *,
    client: LLMClient,
) -> Session:
    """从零生成：拆句 → 程序匹配出初稿 → 对初稿做程序复核 → 生成问题。"""
    utterances = extract_utterances(text, _empty_spec(), client=client, from_zero=True)

    fields, meta = match.build_initial_spec(utterances, text)

    # 审计的对象是刚匹配出来的初稿，不能拿空方案的结果当最终状态
    spec_values = {field: fields[field].value for field in FIELD_ORDER}
    audit.ensure_mappings(utterances, spec_values)
    audit.apply_program_rules(utterances, fields)
    audit.recompute_statuses(utterances)

    plan = build_plan(utterances, fields, case or {})

    created_at = now_iso()
    version_one = SpecVersion(
        version=1,
        created_at=created_at,
        reason="从零生成方案：按客户口述匹配资源库",
        fields=fields,
    )

    with_resource = [field for field in FIELD_ORDER if fields[field].resource_ids]
    without_resource = [
        field
        for field in FIELD_ORDER
        if not fields[field].resource_ids and fields[field].value and field != "品类"
    ]
    rule_ids = sorted({rule for rule in meta.get("rule_hits", {})})
    detail_parts = [
        f"拆出 {len(utterances)} 句原话；{len(with_resource)} 个字段有资源出处："
        + ("、".join(with_resource) or "（无）"),
    ]
    if fields["品类"].value:
        detail_parts.append(
            f"【品类】{fields['品类'].value}直接取自客户口述；资源库没有独立的品类项，不需要找品类样布。"
        )
    if without_resource:
        detail_parts.append(
            "以下字段没有资源出处，需要人工找样：" + "、".join(without_resource)
        )
    if meta.get("unmatched"):
        detail_parts.append("；".join(meta["unmatched"]))
    if meta.get("defaults"):
        detail_parts.append(
            "客户没有单独要求、按资源库最常见取值沿用：" + "、".join(meta["defaults"])
        )
    if rule_ids:
        detail_parts.append("命中的身形规则：" + "、".join(rule_ids))

    import_ledger = LedgerEntry(
        version=1,
        at=created_at,
        action="导入",
        summary="从零生成方案第 1 版：按客户口述从资源库匹配资源，每一项都注明出处。",
        detail="\n".join(detail_parts),
        utterance_ids=[utterance.id for utterance in utterances],
        changed_fields=list(FIELD_ORDER),
    )

    title = (request.title or "").strip()
    if not title:
        head = text[:16] + ("…" if len(text) > 16 else "")
        title = f"从零生成：{head}"

    return Session(
        id=new_session_id(),
        title=title,
        mode="从零生成方案",
        source="llm",
        created_at=created_at,
        input=SessionInput(text=text, existing_spec=None, sample_id=request.sample_id),
        utterances=utterances,
        spec_versions=[version_one],
        questions=plan["questions"],
        designer_items=plan["designer_items"],
        skipped=plan["skipped"],
        ledger=[import_ledger, _audit_ledger(1, utterances)],
        reference=None,
        images=[],
    )

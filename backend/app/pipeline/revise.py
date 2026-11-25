"""回答问题或人工修改 → 方案升一个版本，并追加台账。"""

from __future__ import annotations

from .. import knowledge
from ..schemas import (
    AnswerRequest,
    LedgerEntry,
    Question,
    Session,
    SpecField,
    SpecVersion,
    most_severe,
)
from ..store import now_iso

FIELD_ORDER = knowledge.FIELD_ORDER

# 人工改掉一栏，能替客户解决的出入：有冲突、被改写、未落实。
RESOLVABLE_STATUSES = ("有冲突", "被改写", "未落实")
# 仍算“这句原话还有问题”的状态；只要还剩一个，这句原话就不能整体标为已确认。
BLOCKING_STATUSES = ("有冲突", "被改写", "未落实", "需设计师判断")


def _copy_fields(fields: dict[str, SpecField]) -> dict[str, SpecField]:
    return {name: item.model_copy(deep=True) for name, item in fields.items()}


def _utterance_texts(session: Session, utterance_ids: list[str]) -> str:
    by_id = {u.id: u for u in session.utterances}
    return "、".join(f"「{by_id[uid].text}」" for uid in utterance_ids if uid in by_id)


def _untouched_note(changed: list[str]) -> str:
    others = [field for field in FIELD_ORDER if field not in changed]
    return f"其余设计字段没有改动：{'、'.join(others)}。"


def _summarize_changes(changes: dict[str, tuple[str, str]]) -> str:
    return "；".join(f"【{field}】{old or '未写'} → {new}" for field, (old, new) in changes.items())


def _find_question(session: Session, question_id: str) -> Question:
    for question in session.questions:
        if question.id == question_id:
            return question
    raise ValueError("找不到这个问题")


def apply_answer(session: Session, request: AnswerRequest) -> Session:
    question = _find_question(session, request.question_id)
    option = next((o for o in question.options if o.id == request.option_id), None)
    if option is None:
        raise ValueError("这个问题里没有这个选项")

    # 同一问题重复选同一个选项：不升版本、不记台账
    if question.answer == request.option_id:
        return session

    new_session = session.model_copy(deep=True)
    new_question = _find_question(new_session, request.question_id)
    current = new_session.spec_versions[-1]
    new_session.spec_versions = list(new_session.spec_versions)

    changes: dict[str, tuple[str, str]] = {}
    for field, value in option.patch.items():
        if field not in FIELD_ORDER:
            raise ValueError("选项里出现了不认识的设计字段")
        old_value = current.fields[field].value
        if old_value != value:
            changes[field] = (old_value, value)

    new_question.answer = option.id
    if not changes:
        # 方案没有实际变化，只记录答案，不升版本
        return new_session

    version_number = current.version + 1
    fields = _copy_fields(current.fields)
    for field, (_, value) in changes.items():
        fields[field] = SpecField(
            value=value,
            resource_ids=list(option.resource_ids),
            utterance_ids=list(
                dict.fromkeys(current.fields[field].utterance_ids + new_question.utterance_ids)
            ),
            origin="客户确认",
            confirmed=True,
        )
    new_session.spec_versions.append(
        SpecVersion(
            version=version_number,
            created_at=now_iso(),
            reason=f"客户确认：{new_question.title}",
            fields=fields,
        )
    )

    for utterance in new_session.utterances:
        if utterance.id not in new_question.utterance_ids:
            continue
        for mapping in utterance.mappings:
            if mapping.field == new_question.field:
                mapping.status = "已与客户确认"
                mapping.note = f"客户确认：{option.label}"
        utterance.status = most_severe([m.status for m in utterance.mappings])
        # resolved_by 记录解决整句的问题；其他栏仍有阻塞时清空旧标记。
        utterance.resolved_by = None if utterance.status in BLOCKING_STATUSES else new_question.id

    texts = _utterance_texts(new_session, new_question.utterance_ids)
    new_session.ledger.append(
        LedgerEntry(
            version=version_number,
            at=now_iso(),
            action="客户确认",
            summary=_summarize_changes(changes),
            detail=(
                f"客户回答了「{new_question.title}」，选择「{option.label}」。"
                f"{option.note} 依据原话：{texts or '（无）'}"
                f"{_untouched_note(list(changes.keys()))}"
            ),
            utterance_ids=list(new_question.utterance_ids),
            changed_fields=list(changes.keys()),
        )
    )
    return new_session


def _confirm_manual_resolution(
    session: Session, field: str, reason: str
) -> tuple[list[str], set[str]]:
    """人工改完一栏后，替客户把相关原话处理掉。

    只处理“这一栏原来有出入（有冲突、被改写、未落实）”的原话：
    * 这一栏的映射改记为“已与客户确认”，并在理由里写明是人工修改；
    * 这句原话如果没有别的字段还有问题，其他已正常的映射一并改记为“已与客户确认”，
      这样原话整体才能落到“已与客户确认”；
    * 只要还有别的字段有冲突、被改写、未落实或需设计师判断，就原样保留，
      原话状态仍是其中最严重的一个，对应追问也继续留着。

    不设 resolved_by：这是业务员人工改的，没有对应的客户问题 id，不能编造。

    返回（被处理的原话 id 列表，处理前这些原话在这一栏的状态集合），用于写台账。
    """
    resolved_ids: list[str] = []
    before: set[str] = set()

    for utterance in session.utterances:
        targets = [m for m in utterance.mappings if m.field == field]
        if not any(m.status in RESOLVABLE_STATUSES for m in targets):
            continue

        before.update(m.status for m in targets)
        for mapping in targets:
            mapping.status = "已与客户确认"
            mapping.note = f"业务员按客户要求人工修改了【{field}】（原因：{reason}），改记为已与客户确认。"

        if not any(m.status in BLOCKING_STATUSES for m in utterance.mappings):
            for mapping in utterance.mappings:
                if mapping.status in ("已落实", "间接体现"):
                    mapping.status = "已与客户确认"
                    tail = f"业务员人工修改【{field}】后，这句原话已没有遗留问题。"
                    mapping.note = f"{mapping.note}；{tail}" if mapping.note else tail

        utterance.status = most_severe([m.status for m in utterance.mappings])
        resolved_ids.append(utterance.id)

    return resolved_ids, before


def apply_manual_patch(session: Session, field: str, value: str, reason: str = "") -> Session:
    if field not in FIELD_ORDER:
        raise ValueError("设计字段不正确，只能是八个设计字段之一")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("请填写人工修改的原因，交接单要靠它说明为什么改这一栏")
    value = (value or "").strip()
    if not value:
        raise ValueError("方案内容不能为空")

    current = session.spec_versions[-1]
    old_value = current.fields[field].value
    if old_value == value:
        return session

    new_session = session.model_copy(deep=True)
    new_session.spec_versions = list(new_session.spec_versions)
    fields = _copy_fields(current.fields)
    fields[field] = SpecField(
        value=value,
        resource_ids=knowledge.find_resource_ids(field, value),
        utterance_ids=list(current.fields[field].utterance_ids),
        origin="人工修改",
        confirmed=False,
    )
    version_number = current.version + 1
    new_session.spec_versions.append(
        SpecVersion(
            version=version_number,
            created_at=now_iso(),
            reason=f"人工修改：{reason}",
            fields=fields,
        )
    )

    # 人工改的这一栏已替客户解决，相关原话随之确认；其他字段的出入保持原样。
    resolved_ids, before = _confirm_manual_resolution(new_session, field, reason)

    # 这一栏还没回答的追问不再列出来；已经答过的问题记录保留。
    pending_before = len(new_session.questions)
    new_session.questions = [
        question
        for question in new_session.questions
        if not (question.field == field and question.answer is None)
    ]
    dropped = pending_before - len(new_session.questions)

    detail_parts = [f"人工原因：{reason}"]
    if resolved_ids:
        by_id = {u.id: u for u in new_session.utterances}
        texts = "、".join(f"「{by_id[uid].text}」" for uid in resolved_ids if uid in by_id)
        old_statuses = "、".join(sorted(before))
        detail_parts.append(
            f"这一栏原来的出入（{old_statuses}）已由业务员改掉，"
            f"相关原话 {texts} 中【{field}】的映射改记为已与客户确认。"
        )
    if dropped:
        detail_parts.append(f"【{field}】还没回答的追问（{dropped} 个）不再列出，已回答的问题记录保留。")
    detail = "。".join(detail_parts) + _untouched_note([field])

    new_session.ledger.append(
        LedgerEntry(
            version=version_number,
            at=now_iso(),
            action="人工修改",
            summary=_summarize_changes({field: (old_value, value)}),
            detail=detail,
            utterance_ids=list(current.fields[field].utterance_ids),
            changed_fields=[field],
        )
    )
    return new_session

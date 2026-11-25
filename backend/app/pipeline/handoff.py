"""交接单 Markdown：当前方案、客户确认结果、设计师事项、待办、打样核对项、不由系统决定的事项。"""

from __future__ import annotations

from .. import knowledge
from ..schemas import Session
from ..store import now_iso

FIELD_ORDER = knowledge.FIELD_ORDER

SYSTEM_LIMITS = [
    "资源分两类：上服案例里出现过的参考，以及标注“模拟库”的演示做法；都不代表集团现有库存或可采购面料。",
    "领料建议里的货号、米数、交期来自模拟仓库，仅演示“方案 → 领料”的衔接，落地时替换为上服仓库数据。",
    "价格、工期和交付时间由业务与生产部门确认，系统不报价、不排期。",
    "量体数据由量体师现场采集，系统只记录客户口述和方案。",
    "版型纸样与具体工艺参数由制版师、样衣工决定，系统只做提醒。",
    "面料实样需要客户当面确认，色差以实物为准。",
]


def _resource_text(resource_ids: list[str]) -> str:
    if not resource_ids:
        return "库中暂无"
    parts = []
    for rid in resource_ids:
        item = knowledge.resource_by_id(rid)
        if not item:
            continue
        parts.append(f"{rid} {item.get('name', '')}（{knowledge.resource_source_text(rid)}）")
    return "；".join(parts) if parts else "库中暂无"


def _answered_questions(session: Session) -> list[tuple]:
    items = []
    for question in session.questions:
        if not question.answer:
            continue
        option = next((o for o in question.options if o.id == question.answer), None)
        if option is not None:
            items.append((question, option))
    return items


def _todos(session: Session) -> list[str]:
    todos: list[str] = []
    for question, option in _answered_questions(session):
        if option.todo:
            todos.append(f"【{question.field}】{option.todo}")
    current = session.spec_versions[-1]
    for field in FIELD_ORDER:
        item = current.fields[field]
        if item.origin != "无出处" or not item.value:
            continue
        if field == "品类":
            # 品类来自客户口述，资源库没有独立品类项：不要让人去找品类样布。
            todos.append(
                f"【品类】{item.value}直接取自客户口述；资源库没有独立的品类项，"
                "不需要另找品类样布，照常按品类打版即可。"
            )
            continue
        todos.append(f"【{field}】库中暂无对应做法，需要人工找样确认。")
    return todos


def _stock_lines(session: Session) -> list[str]:
    """按方案用到的面料、纹样、色彩编号，从（模拟）仓库里列出对得上的货。"""
    current = session.spec_versions[-1]
    ids = [
        rid
        for field in ("面料", "装饰纹样", "色彩")
        for rid in current.fields[field].resource_ids
    ]
    lines: list[str] = []
    for entry in knowledge.stock_for(ids)[:3]:
        lead = f"，约 {entry['lead_days']} 天到货" if entry.get("lead_days") else ""
        lines.append(
            f"- {entry['sku']} {entry['name']} · {entry['colorway']}："
            f"{entry['status']}（{entry['stock_m']} 米{lead}，{entry['location']}）"
        )
    return lines


def _sampling_checks(session: Session) -> list[str]:
    checks = list(knowledge.workflow_checks(5))
    current = session.spec_versions[-1]
    extra: list[str] = []
    if any(item.field == "领型" for item in session.designer_items):
        extra.append("领宽按设计师确认后的尺寸放样")
    pattern_ids = current.fields["装饰纹样"].resource_ids
    if pattern_ids:
        extra.append(f"纹样对位与呈现效果（{_resource_text(pattern_ids)}）")
    if "内搭" in current.fields["松弛余量"].value:
        extra.append("内搭放量是否足够，抬手和坐姿是否紧绷")
    if current.fields["色彩"].value and current.fields["色彩"].value != "无特殊需求":
        extra.append("颜色与滚边、袖口的对位和色差")
    for item in extra:
        if item not in checks:
            checks.append(item)
    return checks


def build_handoff(session: Session) -> str:
    current = session.spec_versions[-1]
    lines: list[str] = [
        "# 衣诺 · 交接单",
        "",
        f"- 会话：{session.title}（{session.id}）",
        f"- 模式：{session.mode}",
        f"- 当前版本：第 {current.version} 版",
        f"- 生成时间：{now_iso()}",
        "",
        f"## 一、当前方案（第 {current.version} 版）",
        "",
        "| 设计字段 | 方案 | 出处 | 客户已确认 |",
        "| --- | --- | --- | --- |",
    ]
    for field in FIELD_ORDER:
        item = current.fields[field]
        origin = item.origin
        source = _resource_text(item.resource_ids)
        if not item.resource_ids and item.origin != "无出处":
            source = (
                "—" if item.value in ("", "无特殊需求")
                else "客户原话" if field == "品类"
                else "未对上资源库编号"
            )
        if item.origin == "无出处":
            source = "库中暂无，需人工找样"
            if field == "品类":
                source = "客户原话（资源库无独立品类项，无需找样布）"
        confirmed = "是" if item.confirmed else "否"
        lines.append(
            f"| {field} | {item.value or '未写'} | {origin}；{source} | {confirmed} |"
        )

    lines += ["", "## 二、和客户确认的结果", ""]
    answered = _answered_questions(session)
    if answered:
        for index, (question, option) in enumerate(answered, 1):
            texts = "、".join(
                f"「{u.text}」" for u in session.utterances if u.id in question.utterance_ids
            )
            lines.append(f"{index}. 【{question.field}】{question.title}")
            lines.append(f"   - 客户选择：{option.label}")
            lines.append(f"   - 依据原话：{texts or '（无）'}")
            if option.note:
                lines.append(f"   - 说明：{option.note}")
    else:
        lines.append("还没有和客户确认的事项。")

    if session.restatement and any(p.answer for p in session.restatement.points):
        lines += ["", "复述确认（念给客户听后逐条确认）：", ""]
        for point in session.restatement.points:
            if point.answer:
                extra = f"，客户原意：{point.note}" if point.note else ""
                lines.append(f"- {point.question} —— 客户：{point.answer}{extra}")

    lines += ["", "## 三、请设计师判断", ""]
    if session.designer_items:
        for item in session.designer_items:
            lines.append(f"- 【{item.field}】{item.text}")
    else:
        lines.append("- 暂无。")

    lines += ["", "## 四、待办", ""]
    todos = _todos(session)
    if todos:
        lines.extend(f"- {todo}" for todo in todos)
    else:
        lines.append("- 暂无。")

    lines += ["", "## 五、打样核对项（按上服定制流程第 5 步“打样”）", ""]
    lines.extend(f"- {check}" for check in _sampling_checks(session))

    lines += ["", "## 六、领料建议（模拟仓库，演示数据）", ""]
    stock = _stock_lines(session)
    if stock:
        lines.extend(stock)
        lines.append("- 以上货号与库存为模拟数据，下单前以仓库实盘为准。")
    else:
        lines.append("- 仓库里暂时没有和方案面料对得上的货，需要人工找样或订货。")

    lines += ["", "## 七、不由系统决定的事项", ""]
    lines.extend(f"- {item}" for item in SYSTEM_LIMITS)

    lines += ["", "---", "本交接单由衣诺生成，原话和资源都注明出处；需要人判断的地方已单独列出。", ""]
    return "\n".join(lines)
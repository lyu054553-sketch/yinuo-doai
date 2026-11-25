"""C1：参考图解析的冻结策略与冲突检测（纯程序）。

依据《接口约定》第 7 节与 `vision/results/benchmark_v2_report.md`：

- 采用：领型 `collar_family`、前襟闭合 `front_closure`、纽扣 `buttons_visible`、纹样 `pattern`，
  **把握程度最高只到“疑似”**。
- 不采用：衣长 `length`、廓形 `silhouette`、颜色 `colors`，`used: false`，
  值一律写“看不出”，由界面提示“请人工确认”。

冲突检测是数据表驱动的程序规则（`conflict_rules.json`），不经过任何模型。
只往 `Session.reference`（约定 `ReferenceAnalysis`）和 `Session.ledger`（`参考图解析`）写结构。

调用方式（C3 路由用）：

    from app.media import reference
    session = reference.analyze_reference(session, image_data_url, "/files/reference/xxx.jpeg")

函数不落盘，返回同一个 `Session` 对象；保存由路由调用 `store.save_session()`。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import vision_client
from .prompts import load_mappings

RULES_PATH = Path(__file__).with_name("conflict_rules.json")

CONFIDENCE_ORDER = {"确定": 0, "疑似": 1, "看不出": 2}

USED_MESSAGE = "由视觉模型给出，把握最高只到“疑似”，请人再看一眼。"
UNUSED_MESSAGE = "视觉模型对该项不可靠，请人工确认。"


@lru_cache(maxsize=1)
def load_conflict_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 冻结策略


def _cap_confidence(confidence: str, cap: str) -> str:
    """把握程度只降不升：确定 → 疑似（cap=疑似），疑似保持，看不出保持。"""
    if CONFIDENCE_ORDER[confidence] < CONFIDENCE_ORDER[cap]:
        return cap
    return confidence


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "有纽扣" if value else "无纽扣"
    if isinstance(value, Sequence):
        return "、".join(str(v) for v in value)
    return str(value)


def build_reference_fields(
    fields: Mapping[str, Mapping[str, Any]],
    mappings: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """把视觉原始七字段裁剪成约定 `ReferenceAnalysis.fields`（键用中文标签）。"""
    data = mappings or load_mappings()
    result: dict[str, dict[str, Any]] = {}
    for key, meta in data["vision_keys"].items():
        raw = fields[key]
        label = meta["label"]
        if not meta["used"]:
            result[label] = {"value": "看不出", "confidence": "看不出", "used": False}
            continue
        value = raw["value"]
        confidence = raw["confidence"]
        if confidence == "看不出" or value == "看不出":
            result[label] = {"value": "看不出", "confidence": "看不出", "used": True}
            continue
        cap = meta.get("confidence_cap") or "疑似"
        result[label] = {
            "value": value,
            "confidence": _cap_confidence(confidence, cap),
            "used": True,
        }
    return result


# ---------------------------------------------------------------- 原话匹配


def _is_negated(text: str, index: int, negation: Mapping[str, Any]) -> bool:
    window = int(negation.get("window", 4))
    left = text[max(0, index - window):index]
    return any(left.endswith(prefix) for prefix in negation.get("prefixes", ()))


def _match_requirement(
    text: str, keywords: Sequence[str], negation: Mapping[str, Any]
) -> str | None:
    """在客户原话里找需求关键词；紧挨否定词的命中不算（“不要立领”不是要立领）。"""
    if not text:
        return None
    for keyword in keywords:
        start = 0
        while True:
            index = text.find(keyword, start)
            if index < 0:
                break
            if not _is_negated(text, index, negation):
                return keyword
            start = index + len(keyword)
    return None


def _observed_matches(condition: Mapping[str, Any], raw: Mapping[str, Any]) -> bool:
    value = raw["value"]
    if "values" in condition:
        return any(value == expected for expected in condition["values"])
    text = _text_of(value)
    if "none_markers" in condition:
        if value == "看不出":
            return True
        return any(marker in text for marker in condition["none_markers"])
    if "any_markers_excluding" in condition:
        if value == "看不出" or not str(text).strip():
            return False
        excluded = condition["any_markers_excluding"]
        if any(marker in text for marker in excluded):
            return False
        return True
    return False


def _rule_allows_uncertain(condition: Mapping[str, Any]) -> bool:
    """纹样“看不出”本身就是一种观察（看不到花纹）；领型/闭合/纽扣看不出时不报冲突。"""
    return "none_markers" in condition


# ---------------------------------------------------------------- 冲突检测


def detect_conflicts(
    fields: Mapping[str, Mapping[str, Any]],
    utterances: Sequence[Any],
    rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """拿视觉结果和客户原话比对，返回 `ReferenceAnalysis.conflicts` 结构。

    `utterances` 可以是约定 `Utterance` 的 dict 或 Pydantic 对象，只用 `id` 和 `text`。
    """
    data = rules or load_conflict_rules()
    negation = data["negation"]
    result: list[dict[str, Any]] = []

    for rule in sorted(data["rules"], key=lambda r: r.get("priority", 999)):
        key = rule["observed"]["key"]
        raw = fields.get(key)
        if not isinstance(raw, Mapping):
            continue
        if not _observed_matches(rule["observed"], raw):
            continue
        # 冻结策略之外的字段不参与冲突判断。
        if not load_mappings()["vision_keys"].get(key, {}).get("used"):
            continue
        if raw["confidence"] == "看不出" and not _rule_allows_uncertain(rule["observed"]):
            continue

        matched: list[tuple[str, str]] = []
        for utterance in utterances:
            if isinstance(utterance, Mapping):
                uid, text = utterance.get("id", ""), utterance.get("text", "")
            else:
                uid, text = getattr(utterance, "id", ""), getattr(utterance, "text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            if _match_requirement(text, rule["requirement"]["keywords"], negation):
                matched.append((str(uid), text))
            if len(matched) >= 3:  # 一条规则最多挂三条原话，避免刷屏
                break
        if not matched:
            continue

        message = rule["message"].replace("{utterance_text}", matched[0][1])
        result.append(
            {
                "field": rule["field"],
                "message": message,
                "utterance_ids": [uid for uid, _ in matched],
            }
        )
    return result


# ---------------------------------------------------------------- 汇总


def build_reference_analysis(
    *,
    image_url: str,
    result: vision_client.DeconstructResult,
    utterances: Sequence[Any],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装约定 `ReferenceAnalysis`。"""
    return {
        "image_url": image_url,
        "fields": build_reference_fields(result.fields),
        "conflicts": detect_conflicts(result.fields, utterances, rules),
        "model": result.model,
    }


def _adopted_summary(fields: Mapping[str, Mapping[str, Any]]) -> str:
    parts = []
    for label in ("领型", "前襟闭合", "纽扣", "纹样"):
        item = fields.get(label) or {}
        if not item.get("used") or item.get("value") == "看不出":
            continue
        parts.append(f"{label}={_text_of(item.get('value'))}")
    return "、".join(parts) if parts else "无可用识别结果（均看不出）"


def build_ledger_entry(
    reference: Mapping[str, Any],
    *,
    version: int,
    at: str,
) -> dict[str, Any]:
    """一条 `参考图解析` 台账。"""
    conflicts = reference.get("conflicts") or []
    adopted = _adopted_summary(reference.get("fields") or {})
    if conflicts:
        summary = f"【参考图解析】采用 {adopted}；发现 {len(conflicts)} 处与原话冲突"
    else:
        summary = f"【参考图解析】采用 {adopted}；未发现与原话冲突"
    detail_lines = [f"模型：{reference.get('model', '')}", f"采用字段：{adopted}"]
    unresolved = [
        label
        for label, item in (reference.get("fields") or {}).items()
        if not item.get("used") or item.get("value") == "看不出"
    ]
    if unresolved:
        detail_lines.append("需人工确认：" + "、".join(unresolved))
    for conflict in conflicts:
        detail_lines.append(f"冲突（{conflict['field']}）：{conflict['message']}")
    return {
        "version": version,
        "at": at,
        "action": "参考图解析",
        "summary": summary,
        "detail": "\n".join(detail_lines),
        "utterance_ids": sorted({uid for c in conflicts for uid in c["utterance_ids"]}),
        "changed_fields": sorted({c["field"] for c in conflicts}),
    }


def analyze_reference(
    session: Any,
    image_data_url: str,
    image_url: str,
    *,
    vision_url: str | None = None,
    timeout: float | None = None,
    at: str | None = None,
) -> Any:
    """解析参考图：调视觉服务 → 冻结策略 → 冲突检测 → 写 `reference` 和台账。

    只改 `session.reference` 与 `session.ledger`，不改方案版本，不落盘。
    视觉服务连不上时抛 `vision_client.VisionServiceUnavailable`（路由映射 503）。
    """
    from ..schemas import LedgerEntry, ReferenceAnalysis  # 只在这里引用，便于单独测试
    from ..store import now_iso

    kwargs: dict[str, Any] = {"vision_url": vision_url}
    if timeout is not None:
        kwargs["timeout"] = timeout
    result = vision_client.deconstruct(image_data_url, **kwargs)

    reference = build_reference_analysis(
        image_url=image_url,
        result=result,
        utterances=session.utterances,
    )
    session.reference = ReferenceAnalysis.model_validate(reference)
    version = session.spec_versions[-1].version if session.spec_versions else 1
    entry = build_ledger_entry(reference, version=version, at=at or now_iso())
    session.ledger.append(LedgerEntry.model_validate(entry))
    return session


__all__ = [
    "RULES_PATH", "USED_MESSAGE", "UNUSED_MESSAGE",
    "load_conflict_rules", "build_reference_fields", "detect_conflicts",
    "build_reference_analysis", "build_ledger_entry", "analyze_reference",
]
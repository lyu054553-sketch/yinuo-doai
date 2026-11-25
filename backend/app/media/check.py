"""C2：效果图核对——先解构，再由程序比对。

口径（《接口约定》第 7 节 + `vision/results/benchmark_v2_report.md`）：

1. 只把**图片**交给视觉服务（`vision_client.deconstruct`）。方案规格、字段值、核对清单
   一概不传给模型，避免它照着规格作答；模型必须独立看图。
2. 程序拿模型看出的七字段，与 C0 `prompts.build_check_specs()` 的核对清单逐项比对。
3. 冻结策略允许的四项（领型、前襟闭合、纽扣、纹样）给“符合 / 不符合 / 看不出”，
   把握程度最高只到“疑似”（依据 `mappings.json` 的 `confidence_cap`）。
4. 衣长、廓形、颜色不采用模型结果（`used: false`），一律“看不出 + 需人工确认”。
5. 缺观测、模型说“看不出”、或模型的纹样描述归不到“有纹/无纹” → 一律“看不出”，
   **绝不算“符合”**。

入口（C3 路由用，本模块自身不接路由、不落盘、不调生图）：

    from app.media import check
    payload = check.check_generated_image(session, image_data_url)      # -> CheckResult dict
    session = check.record_check(session, image_id, payload)            # 写 GeneratedImage.check + 核对台账
    session = check.check_and_record(session, image_id, image_data_url) # 上面两步合一

视觉服务连不上时照常抛 `vision_client.VisionServiceUnavailable`（路由映射 503）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import prompts, vision_client
from .reference import CONFIDENCE_ORDER

RULES_PATH = Path(__file__).with_name("check_rules.json")

# 允许给出“符合/不符合”的视觉键（冻结策略允许的四项）。
JUDGED_TARGETS = ("collar_family", "front_closure", "buttons_visible", "pattern")

VERDICTS = ("符合", "不符合", "看不出")


class CheckError(RuntimeError):
    """核对流程自身的错误（找不到方案版本、找不到效果图等），`str(exc)` 是中文。"""


@lru_cache(maxsize=1)
def load_check_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 小工具


def _cap_confidence(confidence: str, cap: str | None) -> str:
    """把握程度只降不升：确定 → 疑似。"""
    if not cap:
        return confidence
    if CONFIDENCE_ORDER.get(confidence, 2) < CONFIDENCE_ORDER.get(cap, 2):
        return cap
    return confidence


def _text_of(value: Any) -> str:
    if isinstance(value, bool):
        return "有外露纽扣" if value else "无外露纽扣"
    if isinstance(value, Sequence) and not isinstance(value, str):
        return "、".join(str(v) for v in value)
    return str(value)


def _is_negated(text: str, index: int, prefixes: Sequence[str], window: int) -> bool:
    """关键词前面紧挨否定词时不算“有这个纹样”。"""
    left = text[max(0, index - window):index]
    return any(prefix and prefix in left for prefix in prefixes)


# ---------------------------------------------------------------- 纹样归一


def observe_pattern(
    raw_value: Any,
    confidence: str,
    rules: dict[str, Any] | None = None,
) -> str | None:
    """把模型给出的纹样自由文本归到“可见纹样 / 无可见纹样”；认不出返回 None。

    先看“无”类标记（它们本身就是否定），再看被否定的“有”类标记：
    “无可见花纹” → 无可见纹样；“暗纹细密” → 可见纹样；“看不出/不能判断” → None。
    """
    data = rules or load_check_rules()
    spec = data["pattern_observation"]
    if confidence == "看不出" or raw_value == "看不出":
        return None
    text = "" if raw_value is None else str(raw_value).strip()
    if not text:
        return None

    for marker in spec["none_markers"]:
        if marker in text:
            return data["pattern_observed"]["none"]

    prefixes = spec["negation_prefixes"]
    window = int(spec.get("negation_window", 6))
    saw_negated = False
    for marker in spec["present_markers"]:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            if not _is_negated(text, index, prefixes, window):
                return data["pattern_observed"]["present"]
            saw_negated = True
            start = index + len(marker)
    # 只提到“看不到花纹 / 不是暗纹”这类说法：算作没看到纹样。
    if saw_negated:
        return data["pattern_observed"]["none"]
    return None


# ---------------------------------------------------------------- 比对


def _item(field: str, expect: str, observed: str, verdict: str, confidence: str) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise CheckError(f"内部错误：未知核对结论 {verdict!r}。")
    return {
        "field": field,
        "expect": expect,
        "observed": observed,
        "verdict": verdict,
        "confidence": confidence,
    }


def _unjudged(spec: Mapping[str, Any], rules: Mapping[str, Any]) -> dict[str, Any]:
    """衣长/廓形/颜色：不采用模型结果，只列清单让人工确认。"""
    return _item(
        spec["field"],
        spec["expect"],
        rules["manual_confirm_observed"],
        rules["verdict_labels"]["unknown"],
        "看不出",
    )


def _compare_one(
    spec: Mapping[str, Any],
    raw: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    labels = rules["verdict_labels"]
    if not spec.get("used"):
        return _unjudged(spec, rules)

    value = raw.get("value")
    confidence = raw.get("confidence", "看不出")
    if confidence == "看不出" or value == "看不出" or value is None:
        # 模型没看见就不下结论，observed 也写“看不出”。
        return _item(spec["field"], spec["expect"], rules["unseen_observed"], labels["unknown"], "看不出")

    judged_confidence = _cap_confidence(confidence, spec.get("confidence_cap"))
    target = spec["target"]
    expected = spec.get("expected_value")
    observed = _text_of(value)

    if expected == "看不出" or expected is None:
        # 方案这一栏没写清，没有可比的预期。
        return _item(spec["field"], spec["expect"], observed, labels["unknown"], "看不出")

    if target == "pattern":
        seen = observe_pattern(value, confidence, rules)
        if seen is None:
            return _item(spec["field"], spec["expect"], observed, labels["unknown"], "看不出")
        observed = seen
        verdict = labels["match"] if seen == expected else labels["mismatch"]
        return _item(spec["field"], spec["expect"], observed, verdict, judged_confidence)

    if target == "buttons_visible":
        verdict = labels["match"] if value == expected else labels["mismatch"]
        return _item(spec["field"], spec["expect"], observed, verdict, judged_confidence)

    if target == "front_closure":
        allowed = [expected, *spec.get("alternatives", [])]
        verdict = labels["match"] if value in allowed else labels["mismatch"]
        return _item(spec["field"], spec["expect"], observed, verdict, judged_confidence)

    # collar_family 及以后可能新增的单值字段：值相等才算符合。
    verdict = labels["match"] if value == expected else labels["mismatch"]
    return _item(spec["field"], spec["expect"], observed, verdict, judged_confidence)


def compare(
    specs: Sequence[Mapping[str, Any]],
    fields: Mapping[str, Mapping[str, Any]],
    *,
    rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """把视觉结果与核对清单逐项比对，返回约定 `CheckResult.items`。

    `specs` 来自 `prompts.build_check_specs()`，`fields` 是 `vision_client.deconstruct()`
    已校验的七字段。顺序与 `specs` 一致。
    """
    data = rules or load_check_rules()
    items: list[dict[str, Any]] = []
    for spec in specs:
        raw = fields.get(spec["target"])
        if not isinstance(raw, Mapping):
            # 服务少给了键（理论上 vision_client 已拦），仍不放行成“符合”。
            items.append(
                _item(
                    spec["field"],
                    spec["expect"],
                    data["unseen_observed"],
                    data["verdict_labels"]["unknown"],
                    "看不出",
                )
            )
            continue
        items.append(_compare_one(spec, raw, data))
    return items


# ---------------------------------------------------------------- 核对一张图


def _validated(items: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """用约定 CheckResult 校验并返回 JSON 安全的 dict（字段不多不少）。"""
    from ..schemas import CheckResult  # 只在运行时引用，模块可单独 import

    return CheckResult.model_validate({"items": items, "model": model}).model_dump(mode="json")


def check_image(
    image_data_url: str,
    spec_version: Any,
    *,
    vision_url: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """核对一张图：只把图片发给视觉服务，再由程序比对。

    `spec_version` 是约定 `SpecVersion`（dict 或 Pydantic 对象）。
    返回 `{"items": [...], "model": "..."}`（已过 CheckResult 校验）。
    """
    specs = prompts.build_check_specs(spec_version)
    kwargs: dict[str, Any] = {}
    if vision_url is not None:
        kwargs["vision_url"] = vision_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    result = vision_client.deconstruct(image_data_url, **kwargs)
    return _validated(compare(specs, result.fields), result.model)


def resolve_spec_version(session: Any, version: int | None = None) -> Any:
    """取会话里的方案版本；默认最后（当前）一个版本。取不到抛 CheckError。"""
    versions = list(getattr(session, "spec_versions", None) or [])
    if not versions:
        raise CheckError("这个会话还没有方案版本，无法核对效果图。")
    if version is None:
        return versions[-1]
    for item in versions:
        if getattr(item, "version", None) == version:
            return item
    raise CheckError(f"会话里没有第 {version} 版方案，无法核对。")


def check_generated_image(
    session: Any,
    image_data_url: str,
    *,
    version: int | None = None,
    vision_url: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """C3 路由入口：按会话的方案版本核对一张效果图（或上传的外部图）。

    只核对，不写会话；写会话用 `record_check()`，或直接用 `check_and_record()`。
    视觉服务连不上时抛 `vision_client.VisionServiceUnavailable`。
    """
    spec_version = resolve_spec_version(session, version)
    return check_image(
        image_data_url, spec_version, vision_url=vision_url, timeout=timeout
    )


# ---------------------------------------------------------------- 写回会话


def needs_redraw(result: Mapping[str, Any] | Any) -> bool:
    """是否出现了“不符合”（C3 路由据此在配置了生图接口时自动重画一次）。

    本模块不做重画：生成要付钱，必须由路由按 `IMAGE_PROVIDER` 决定。
    """
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    target = load_check_rules()["redraw_on_verdict"]
    return any(item.get("verdict") == target for item in payload.get("items", []))


def _find_image(session: Any, image: Any) -> Any:
    images = list(getattr(session, "images", None) or [])
    if not images:
        raise CheckError("这个会话里还没有效果图，先出图或上传一张图再核对。")
    if isinstance(image, str):
        for item in images:
            if getattr(item, "id", None) == image:
                return item
        raise CheckError(f"会话里没有编号为 {image} 的效果图。")
    for item in images:
        if item is image:
            return item
    for item in images:
        if getattr(item, "id", None) == getattr(image, "id", None):
            return item
    raise CheckError("这张效果图不属于当前会话，无法记录核对结果。")


def _item_lines(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for item in result["items"]:
        lines.append(
            f"【{item['field']}】{item['verdict']}（预期：{item['expect']}；看到：{item['observed']}；"
            f"把握：{item['confidence']}）"
        )
    return lines


def build_ledger_entry(
    result: Mapping[str, Any],
    *,
    image: Any,
    at: str,
    utterance_ids: Sequence[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """一条 `核对` 台账（约定 `LedgerEntry`）。"""
    version = int(getattr(image, "version", 1) or 1)
    counts = {name: 0 for name in VERDICTS}
    mismatched: list[str] = []
    for item in result["items"]:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
        if item["verdict"] == load_check_rules()["redraw_on_verdict"] and item["field"] not in mismatched:
            mismatched.append(item["field"])

    kind = getattr(image, "kind", "")
    summary = (
        f"【效果图核对】{kind}：符合 {counts['符合']} 项、不符合 {counts['不符合']} 项、"
        f"看不出 {counts['看不出']} 项"
    )
    if mismatched:
        summary += "；不符合：" + "、".join(mismatched)

    detail_lines = [
        f"图片：{getattr(image, 'id', '')}（依据方案第 {version} 版）",
        f"模型：{result.get('model', '')}",
        "核对方式：视觉模型独立看图，程序与核对清单比对；未把方案规格交给模型。",
        *_item_lines(result),
    ]
    if mismatched:
        detail_lines.append("建议：按“不符合”项重画一次，或请设计师确认差异。")
    if note:
        detail_lines.append(f"备注：{note}")

    changed = [field for field in prompts.FIELD_ORDER if field in mismatched]
    return {
        "version": version,
        "at": at,
        "action": "核对",
        "summary": summary,
        "detail": "\n".join(detail_lines),
        "utterance_ids": list(utterance_ids or []),
        "changed_fields": changed,
    }


def record_check(
    session: Any,
    image: Any,
    result: Mapping[str, Any] | Any,
    *,
    at: str | None = None,
    utterance_ids: Sequence[str] | None = None,
    note: str | None = None,
) -> Any:
    """把核对结果写进 `GeneratedImage.check` 并追加一条 `核对` 台账。

    `image` 可以是效果图对象或它的 id。只改 `session.images` 和 `session.ledger`，
    不改方案版本，不落盘（保存由路由负责）。
    """
    from ..schemas import CheckResult, LedgerEntry  # 只在运行时引用
    from ..store import now_iso

    target = _find_image(session, image)
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    validated = CheckResult.model_validate(payload)
    target.check = validated
    entry = build_ledger_entry(
        validated.model_dump(mode="json"),
        image=target,
        at=at or now_iso(),
        utterance_ids=utterance_ids,
        note=note,
    )
    session.ledger.append(LedgerEntry.model_validate(entry))
    return session


def check_and_record(
    session: Any,
    image: Any,
    image_data_url: str,
    *,
    version: int | None = None,
    vision_url: str | None = None,
    timeout: float | None = None,
    at: str | None = None,
    utterance_ids: Sequence[str] | None = None,
    note: str | None = None,
) -> Any:
    """C3 路由最省事的入口：核对 + 写回会话。"""
    target = _find_image(session, image)
    payload = check_generated_image(
        session,
        image_data_url,
        version=version if version is not None else getattr(target, "version", None),
        vision_url=vision_url,
        timeout=timeout,
    )
    return record_check(
        session,
        target,
        payload,
        at=at,
        utterance_ids=utterance_ids,
        note=note,
    )


__all__ = [
    "RULES_PATH", "JUDGED_TARGETS", "VERDICTS", "CheckError",
    "load_check_rules", "observe_pattern", "compare", "check_image",
    "resolve_spec_version", "check_generated_image", "needs_redraw",
    "build_ledger_entry", "record_check", "check_and_record",
]
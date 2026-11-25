"""C0：确定性效果图提示词与核对清单。

- 不调用任何模型、生图或视觉服务，纯程序拼接。
- 不依赖 app.schemas，可单独 import；只依赖同目录 mappings.json。
- 同一份方案永远生成完全相同的提示词（字符串按固定顺序拼接，无时间戳、无随机数）。
- 入口：build_prompts(spec_version) -> {version, items[3], checks[]}
- 输入可以是约定 SpecVersion 的 dict，也可以是任何带 model_dump() 的对象
  （例如后端核心的 Pydantic SpecVersion），本模块不 import app.schemas。
- 辅助：build_check_specs(spec_version) 给出 C2 核对要用的结构化预期（含“需人工确认”标记）。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping

MAPPINGS_PATH = Path(__file__).with_name("mappings.json")

FIELD_ORDER = (
    "品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量",
)

KINDS = ("成衣挂拍", "面料细节", "上身效果")

HEADERS = {
    "成衣挂拍": "成衣挂拍照片：单件上装正面展示，纯白无缝背景，无模特、无衣架以外的道具。",
    "面料细节": "面料细节照片：近景特写上装的面料、织纹与纹样，均匀柔光，能看清肌理与光泽。",
    "上身效果": "上身效果照片：模特自然站立，正面全身/半身，室内素色背景，服装轮廓完整露出。",
}

SET_HEADERS = {
    "成衣挂拍": "成衣挂拍照片：成套服装正面展示，所有件同框，纯白无缝背景，无模特、无衣架以外的道具。",
    "面料细节": "面料细节照片：近景特写成套服装的面料、织纹与纹样，均匀柔光，能看清肌理与光泽。",
    "上身效果": "上身效果照片：模特穿着成套服装自然站立，正面全身/半身，室内素色背景，服装轮廓完整露出。",
}

FOOTERS = {
    "成衣挂拍": "画面要求：成衣轮廓、结构与缝线清晰；不出现文字、水印、标志与背景杂物。",
    "面料细节": "画面要求：焦平面覆盖面料表面，不出现人物、文字、水印与标志。",
    "上身效果": "画面要求：不出现文字、水印、标志；配饰不得遮挡服装结构与纹样。",
}

_WHITESPACE = re.compile(r"\s+")
_FLAT = re.compile(r"[\s。.;；，,、]+")

TYPE_ERROR_MESSAGE = (
    "build_prompts 需要约定的 SpecVersion（dict 或其 Pydantic 对象）"
)


@lru_cache(maxsize=1)
def load_mappings() -> dict[str, Any]:
    """读取独立映射数据表（缓存一份，只读）。"""
    with MAPPINGS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _flat_tokens() -> frozenset[str]:
    return frozenset(_FLAT.sub("", t) for t in load_mappings()["unspecified_tokens"])


def is_unspecified(text: str) -> bool:
    """判断一栏是不是“没有需求”，例如“无特殊需求”。"""
    flat = _FLAT.sub("", text or "")
    return flat == "" or flat in _flat_tokens()


def field_text(value: Any) -> str:
    """把一栏的原始值压成一段文字。

    支持：纯字符串；SpecField（{"value": "..."} 或其 Pydantic 对象）；
    多件套（{"外套": "...", "连衣裙": "..."}）。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return _WHITESPACE.sub(" ", value).strip()
    if isinstance(value, Mapping):
        inner = value.get("value")
        if isinstance(inner, str):
            return field_text(inner)
        parts = []
        for garment, sub in value.items():
            text = field_text(sub)
            if text:
                parts.append(f"{garment}：{text}")
        return "；".join(parts)
    # 单栏的 Pydantic SpecField 对象：有 value 属性就取文字。
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return field_text(inner)
    return _WHITESPACE.sub(" ", str(value)).strip()


def _as_mapping(spec_version: Any) -> Mapping[str, Any]:
    """把 SpecVersion 统一成 Mapping。

    先认 dict（含 Mapping 子类）；否则看对象有没有 model_dump()，
    用它把 Pydantic 模型转成 dict。不 import app.schemas，也不假设字段类型。
    """
    if isinstance(spec_version, Mapping):
        return spec_version
    dump = getattr(spec_version, "model_dump", None)
    if callable(dump):
        data = dump()
        if isinstance(data, Mapping):
            return data
    raise TypeError(TYPE_ERROR_MESSAGE)


def resolve_spec_version(spec_version: Any) -> tuple[int, dict[str, str]]:
    """接受约定的 SpecVersion（dict 或其 Pydantic 对象），返回版本号和八栏文字。"""
    spec = _as_mapping(spec_version)
    raw = spec.get("fields")
    if not isinstance(raw, Mapping):
        raw = spec
    fields = {field: field_text(raw.get(field)) for field in FIELD_ORDER}
    try:
        version = int(spec.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    return (version if version >= 1 else 1), fields


def _sentence(field: str, text: str) -> str:
    value = text.rstrip("。").strip()
    return f"【{field}】{value}。"


def _headers(fields: dict[str, str]) -> dict[str, str]:
    """多件套要求所有件同框，避免把成套方案写成单件上装。"""
    category = fields.get("品类", "")
    return SET_HEADERS if any(marker in category for marker in ("两件", "套装")) else HEADERS


def build_prompt_items(spec_version: Any) -> list[dict[str, str]]:
    """三类提示词：成衣挂拍、面料细节、上身效果。"""
    _, fields = resolve_spec_version(spec_version)
    headers = _headers(fields)
    body = [
        _sentence(field, fields[field])
        for field in FIELD_ORDER
        if not is_unspecified(fields[field])
    ]
    return [
        {"kind": kind, "text": "\n".join([headers[kind], *body, FOOTERS[kind]])}
        for kind in KINDS
    ]


def _hits(text: str, keyword: str) -> Iterator[int]:
    start = 0
    while True:
        index = text.find(keyword, start)
        if index < 0:
            return
        yield index
        start = index + len(keyword)


def _is_negated(text: str, index: int, negation: Mapping[str, Any]) -> bool:
    window = int(negation.get("window", 2))
    left = text[max(0, index - window):index]
    return any(left.endswith(prefix) for prefix in negation.get("prefixes", ()))


def _match_rule(text: str, rules: list[dict[str, Any]], negation: Mapping[str, Any]):
    for rule in rules:
        for keyword in rule.get("keywords", ()):
            for index in _hits(text, keyword):
                if not _is_negated(text, index, negation):
                    return rule
    return None


def _match_markers(text: str, markers: list[str], negation: Mapping[str, Any]) -> str | None:
    for marker in markers:
        for index in _hits(text, marker):
            if not _is_negated(text, index, negation):
                return marker
    return None


def _source_text(fields: dict[str, str], meta: Mapping[str, Any]) -> str:
    texts = (fields[f] for f in meta.get("source_fields", ()))
    return "；".join(t for t in texts if not is_unspecified(t))


def build_check_specs(spec_version: Any, mappings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """生成结构化核对清单（C2 用）。只使用八字段词表；衣长、廓形、颜色一律标“需人工确认”。"""
    data = mappings or load_mappings()
    _, fields = resolve_spec_version(spec_version)
    rules = data["rules"]
    negation = data["negation"]
    vision_keys = data["vision_keys"]
    manual = data["manual_confirm_label"]

    specs: list[dict[str, Any]] = []
    for key in data["check_order"]:
        meta = vision_keys[key]
        text = _source_text(fields, meta)
        item: dict[str, Any] = {
            "target": key,
            "label": meta["label"],
            "field": meta["field"],
            "expected_value": "看不出",
            "alternatives": [],
            "strict": False,
            "used": meta["used"],
            "manual_confirm": meta["manual_confirm"],
            "confidence_cap": meta["confidence_cap"],
            "expect": "",
        }

        if key == "collar_family":
            rule = _match_rule(text, rules["collar_rules"], negation)
            if rule:
                item["expected_value"] = rule["vision"]
                item["strict"] = True
                expect = rule["vision"]
                if rule["vision"] == "立领" and _match_markers(
                    text, rules["collar_low_markers"], {"prefixes": (), "window": 0}
                ):
                    expect = f"立领（领高不超过 3cm，{manual}）"
                item["expect"] = expect
            else:
                item["expect"] = "看不出（方案未写明领型）"

        elif key == "front_closure":
            rule = _match_rule(text, rules["closure_rules"], negation)
            if rule:
                item["expected_value"] = rule["vision"]
                alternatives = list(rule.get("alternatives", ()))
                item["alternatives"] = alternatives
                item["strict"] = not alternatives
                if alternatives:
                    item["expect"] = f"{rule['vision']}（也可能{'、'.join(alternatives)}，{manual}）"
                else:
                    item["expect"] = rule["vision"]
            else:
                item["expect"] = "看不出（方案未写明前襟闭合方式）"

        elif key == "buttons_visible":
            if _match_markers(text, rules["button_rules"]["absent"], negation):
                item["expected_value"] = False
                item["strict"] = True
                item["expect"] = "不应看到纽扣"
            elif _match_markers(text, rules["button_rules"]["present"], negation):
                item["expected_value"] = True
                item["strict"] = True
                item["expect"] = "应能看到纽扣"
            else:
                item["expect"] = "看不出（方案未写明纽扣）"

        elif key == "pattern":
            # 明确“不加独立图案”优先于后文对面料肌理的条件说明。
            if _match_markers(text, rules["pattern_rules"]["none"], negation):
                item["expected_value"] = "无可见纹样"
                item["expect"] = "应无明显纹样"
            elif _match_markers(text, rules["pattern_rules"]["present"], negation):
                item["expected_value"] = "可见纹样"
                item["expect"] = "应有可见纹样（暗纹或花纹）"
            else:
                item["expect"] = "看不出（方案未指定纹样）"

        elif key == "length":
            rule = _match_rule(text, rules["length_rules"], negation)
            if rule:
                item["expected_value"] = rule["vision"]
                item["expect"] = f"{rule['vision']}（{manual}）"
            else:
                item["expect"] = f"看不出（方案未指定长度，{manual}）"

        elif key == "silhouette":
            rule = _match_rule(text, rules["silhouette_rules"], negation)
            if rule:
                item["expected_value"] = rule["vision"]
                item["expect"] = f"{rule['vision']}（{manual}）"
            else:
                item["expect"] = f"看不出（方案未写明廓形，{manual}）"

        elif key == "colors":
            raw = fields["色彩"]
            if is_unspecified(raw):
                item["expect"] = f"看不出（方案未指定色彩，{manual}）"
            else:
                item["expected_value"] = raw
                item["expect"] = f"按方案文字核对：{raw}（{manual}）"

        specs.append(item)
    return specs


def build_prompts(spec_version: Any) -> dict[str, Any]:
    """C0 入口：给定 SpecVersion（dict 或 Pydantic 对象），返回约定 prompts 响应。"""
    version, _ = resolve_spec_version(spec_version)
    checks = [
        {"field": spec["field"], "expect": spec["expect"]}
        for spec in build_check_specs(spec_version)
    ]
    return {"version": version, "items": build_prompt_items(spec_version), "checks": checks}


__all__ = [
    "FIELD_ORDER", "KINDS", "HEADERS", "SET_HEADERS", "FOOTERS", "MAPPINGS_PATH",
    "load_mappings", "is_unspecified", "field_text", "resolve_spec_version",
    "build_prompt_items", "build_check_specs", "build_prompts",
]

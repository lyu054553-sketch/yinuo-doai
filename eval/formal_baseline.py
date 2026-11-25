#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用问法对照组：同一个大模型直接看客户口述和已有方案，列出“没落实或有矛盾”的地方。

刻意**不**使用领域规则、身形规则、少样本示例和标准答案；只给输出格式与八个字段词表。
对照组只用于比较“问题检出”，不比较拆句召回、字段精确率等完整系统指标。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_eval

FIELDS = list(run_eval.FIELDS)
PROBLEM_STATUSES = set(run_eval.PROBLEM_STATUSES)

SYSTEM_PROMPT = (
    "你是定制服装业务助手。用户会给你一段客户口述，以及一份按八个字段写好的已有设计方案。"
    "请只根据这两项材料，找出方案里没有落实客户要求的地方，或与客户要求相互矛盾的地方。\n"
    "输出规则：\n"
    "1. 只输出一个 JSON 对象，不要输出任何解释文字，不要用代码块。\n"
    '2. 格式为 {"issues": [{"text": "...", "field": "...", "reason": "..."}]}。\n'
    "3. text 必须是客户口述中的原文片段，逐字摘录，不得改写、概括或拼接。\n"
    "4. field 只能取以下之一：" + "、".join(FIELDS) + "。\n"
    "5. reason 用一句话说明这处为什么算没落实或有矛盾。\n"
    '6. 没有发现任何问题时，输出 {"issues": []}。\n'
    "7. 不要臆测客户没有说过的信息，也不要提出与这两项材料无关的建议。"
)


def build_messages(customer_text: str, existing_spec: Dict[str, str]) -> List[Dict[str, str]]:
    spec_lines = [f"- {field}：{existing_spec.get(field, '')}" for field in FIELDS]
    user = (
        "客户口述：\n"
        f"{customer_text}\n\n"
        "已有方案（八个字段）：\n"
        + "\n".join(spec_lines)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_request(model: str, customer_text: str, existing_spec: Dict[str, str], max_tokens: int,
                  response_format: bool = False) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": model,
        "messages": build_messages(customer_text, existing_spec),
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format:
        body["response_format"] = {"type": "json_object"}
    return body


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _loads_lenient(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE.sub("", stripped).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    # 退一步：截取第一个 { 到最后一个 }
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except ValueError:
            return None
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except ValueError:
            return None
    return None


def _pick(item: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def parse_baseline_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """从 chat/completions 响应里取出问题列表；解析失败返回 ok=False（不硬凑）。"""
    result: Dict[str, Any] = {
        "ok": False,
        "error": None,
        "items": [],
        "content": "",
        "model": None,
        "usage": None,
        "raw_shape": None,
    }
    if not isinstance(response, dict):
        result["error"] = "响应不是 JSON 对象"
        return result
    result["model"] = response.get("model")
    result["usage"] = response.get("usage")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        result["error"] = "响应缺少 choices"
        return result
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        result["error"] = "响应 content 为空"
        return result
    result["content"] = content

    parsed = _loads_lenient(content)
    if parsed is None:
        result["error"] = "content 不是合法 JSON（已尝试去掉代码块与截取大括号）"
        return result

    raw_items: Any = None
    if isinstance(parsed, dict):
        raw_items = parsed.get("issues")
        if raw_items is None:
            for value in parsed.values():
                if isinstance(value, list):
                    raw_items = value
                    break
    elif isinstance(parsed, list):
        raw_items = parsed
    if not isinstance(raw_items, list):
        result["error"] = "JSON 中没有 issues 数组"
        result["raw_shape"] = type(parsed).__name__
        return result

    items: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            items.append({"index": index, "text": None, "field": None, "reason": None,
                          "error": "条目不是对象"})
            continue
        text = _pick(raw, "text", "原文", "quote", "utterance")
        field = _pick(raw, "field", "字段")
        reason = _pick(raw, "reason", "原因", "note", "why")
        entry = {
            "index": index,
            "text": text if isinstance(text, str) else None,
            "field": field if isinstance(field, str) else None,
            "reason": reason if isinstance(reason, str) else "",
            "error": None,
        }
        if entry["text"] is None or not entry["text"].strip():
            entry["error"] = "条目缺少 text"
        elif entry["field"] not in FIELDS:
            entry["error"] = f"field 不在八个字段内：{entry['field']!r}"
        items.append(entry)

    valid = [it for it in items if it["error"] is None]
    if not valid and items:
        result["error"] = "所有条目都缺少合法的 text/field"
        result["items"] = items
        return result
    result["items"] = items
    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# 打分：只算问题检出
# ---------------------------------------------------------------------------

def evaluate_baseline(case_id: str, items: List[Dict[str, Any]], customer_text: str,
                      gold: Dict[str, Any]) -> Dict[str, Any]:
    """把对照组输出的“问题清单”与标准问题句对齐，只统计问题检出与误报。

    对齐方式复用 D0 的字符重叠配对（≥0.5、一对一贪心）。对照组只列问题，
    因此它天然拿不到拆句召回/精确率——这些指标不参与对照组比较。
    """
    valid_items = [it for it in items if it.get("error") is None]
    preds = [
        {
            "id": f"b{it['index']}",
            "text": it["text"],
            "mappings": [{"field": it["field"]}],
            "_item": it,
        }
        for it in valid_items
    ]
    pairs = run_eval.pair_utterances(preds, gold["utterances"])
    pair_by_gold = {p["gold_index"]: p for p in pairs}
    used_pred = {p["pred_index"] for p in pairs}

    rows: List[Dict[str, Any]] = []
    detected = 0
    field_agree = 0
    for gi, gold_utt in enumerate(gold["utterances"]):
        if gold_utt.get("status") not in PROBLEM_STATUSES:
            continue
        pair = pair_by_gold.get(gi)
        item = pair["pred"]["_item"] if pair else None
        gold_fields = run_eval.field_set(gold_utt.get("mappings") or [])
        field_match = bool(item is not None and item.get("field") in gold_fields)
        if item is not None:
            detected += 1
            if field_match:
                field_agree += 1
        rows.append(
            {
                "case_id": case_id,
                "gold_index": gi,
                "gold_utterance_id": gold_utt.get("id"),
                "gold_text": gold_utt.get("text"),
                "gold_status": gold_utt.get("status"),
                "gold_fields": gold_fields,
                "paired": pair is not None,
                "match_ratio": pair["ratio"] if pair else None,
                "item_index": item.get("index") if item else None,
                "item_text": item.get("text") if item else None,
                "item_field": item.get("field") if item else None,
                "field_match": field_match,
                "detected": pair is not None,
            }
        )

    false_positives: List[Dict[str, Any]] = []
    fp_by_status: Dict[str, int] = {}
    for pair in pairs:
        gold_status = pair["gold"].get("status")
        if gold_status in PROBLEM_STATUSES:
            continue
        item = pair["pred"]["_item"]
        entry = {
            "case_id": case_id,
            "gold_utterance_id": pair["gold"].get("id"),
            "gold_text": pair["gold"].get("text"),
            "gold_status": gold_status,
            "item_index": item.get("index"),
            "item_text": item.get("text"),
            "item_field": item.get("field"),
            "match_ratio": pair["ratio"],
        }
        false_positives.append(entry)
        fp_by_status[gold_status or "缺失"] = fp_by_status.get(gold_status or "缺失", 0) + 1

    claims_unmatched: List[Dict[str, Any]] = []
    fabricated_texts: List[Dict[str, Any]] = []
    schema_errors: List[Dict[str, Any]] = [
        {"case_id": case_id, "item_index": it.get("index"), "error": it.get("error")}
        for it in items
        if it.get("error")
    ]
    for pred_index, pred in enumerate(preds):
        item = pred["_item"]
        text = item.get("text") or ""
        if text and text not in customer_text:
            fabricated_texts.append(
                {
                    "case_id": case_id,
                    "type": "引文不是原文子串（改写或概括）",
                    "item_index": item.get("index"),
                    "text": text,
                }
            )
        if pred_index not in used_pred:
            claims_unmatched.append(
                {
                    "case_id": case_id,
                    "item_index": item.get("index"),
                    "text": item.get("text"),
                    "field": item.get("field"),
                    "reason": item.get("reason"),
                    "substring_ok": bool(text and text in customer_text),
                }
            )

    return {
        "case_id": case_id,
        "items_count": len(items),
        "valid_items": len(valid_items),
        "invalid_items": len(items) - len(valid_items),
        "gold_problem_count": len(rows),
        "detected": detected,
        "rows": rows,
        "field_agree": field_agree,
        "false_positives": false_positives,
        "fp_by_status": fp_by_status,
        "claims_unmatched": claims_unmatched,
        "fabricated_texts": fabricated_texts,
        "schema_errors": schema_errors,
    }
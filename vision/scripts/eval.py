#!/usr/bin/env python3
"""用本地 Ollama 视觉模型评测上服 15 张真实图。

只做推理，不训练，不上传图片。默认调用本机 http://localhost:11434。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

ENUM_FIELDS = [
    "collar_family",
    "front_closure",
    "buttons_visible",
    "length",
    "silhouette",
]
ALL_FIELDS = ENUM_FIELDS + ["pattern", "colors"]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "—"
    return f"{100.0 * numerator / denominator:.1f}%"


def ratio(numerator: int, denominator: int):
    return None if denominator == 0 else round(numerator / denominator, 4)


def canonical_value(field: str, value):
    """把模型常见写法归一化，便于统计；严格格式仍由 schema 校验。"""
    if field == "buttons_visible":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "是", "有", "可见", "看到"}:
                return True
            if text in {"false", "否", "无", "不可见", "没看到"}:
                return False
            if "看不出" in text:
                return "看不出"
        return value
    if isinstance(value, str):
        text = re.sub(r"\s+", "", value)
        text = {
            "小立领": "立领",
            "中式立领": "立领",
            "青果领": "翻领",
            "西装领": "翻领",
            "圆领口": "圆领",
            "无扣": "无扣开襟",
            "对襟": "无扣开襟",
            "交叠": "交叠闭合",
            "系带闭合": "系带",
            "绑带": "系带",
            "及臀": "臀部附近",
            "臀中": "臀部附近",
            "过臀部": "过臀",
            "齐腰": "到腰",
            "直筒": "H 型",
            "H型": "H 型",
            "收腰型": "收腰",
            "A字": "A 摆",
            "A字形": "A 摆",
            "A摆": "A 摆",
        }.get(text, text)
        return text
    return value


def enum_values(schema: dict, field: str):
    def_name = {
        "collar_family": "collar_value",
        "front_closure": "closure_value",
        "buttons_visible": "buttons_value",
        "length": "length_value",
        "silhouette": "silhouette_value",
    }[field]
    return schema["$defs"][def_name]["enum"]


def parse_json_text(text: str):
    if not text or not text.strip():
        raise ValueError("模型返回为空")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            raise
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        return obj


def validate_deconstruct(obj, schema: dict):
    errors = []
    if not isinstance(obj, dict):
        return ["顶层不是 JSON 对象"]
    allowed = set(schema["required"])
    for key in obj:
        if key not in allowed:
            errors.append(f"多余字段：{key}")
    for field in ALL_FIELDS:
        if field not in obj:
            errors.append(f"缺少字段：{field}")
            continue
        item = obj[field]
        if not isinstance(item, dict):
            errors.append(f"{field} 不是对象")
            continue
        for extra in set(item) - {"value", "confidence"}:
            errors.append(f"{field} 有多余键：{extra}")
        if "value" not in item:
            errors.append(f"{field} 缺少 value")
            continue
        if "confidence" not in item:
            errors.append(f"{field} 缺少 confidence")
            continue
        value = item["value"]
        confidence = item["confidence"]
        if confidence not in schema["$defs"]["confidence"]["enum"]:
            errors.append(f"{field}.confidence 不在允许值内：{confidence!r}")
        if field in ENUM_FIELDS:
            allowed_values = enum_values(schema, field)
            if field == "buttons_visible":
                if not isinstance(value, (bool, str)):
                    errors.append(f"{field}.value 类型不对：{type(value).__name__}")
                elif value not in allowed_values:
                    errors.append(f"{field}.value 不在允许值内：{value!r}")
            else:
                if not isinstance(value, str):
                    errors.append(f"{field}.value 不是字符串")
                elif value not in allowed_values:
                    errors.append(f"{field}.value 不在允许值内：{value!r}")
        elif field == "pattern":
            if not isinstance(value, str) or not 1 <= len(value) <= 80:
                errors.append("pattern.value 必须是 1-80 字字符串")
        elif field == "colors":
            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= 3
                or any(not isinstance(v, str) or not 1 <= len(v) <= 20 for v in value)
            ):
                errors.append("colors.value 必须是 1-3 个短字符串")
    return errors


def validate_verify(obj, schema: dict):
    errors = []
    if not isinstance(obj, dict):
        return ["顶层不是 JSON 对象"]
    items = obj.get("items")
    if items is None and any(field in obj for field in ALL_FIELDS):
        errors.append("缺少 items 外层")
        items = obj
    if not isinstance(items, dict):
        return errors + ["items 不是对象"]
    for extra in set(obj) - {"items"}:
        if extra not in ALL_FIELDS:
            errors.append(f"多余顶层字段：{extra}")
    for extra in set(items) - set(ALL_FIELDS):
        errors.append(f"items 有多余字段：{extra}")
    allowed_results = schema["$defs"]["item_result"]["properties"]["result"]["enum"]
    for field in ALL_FIELDS:
        if field not in items:
            errors.append(f"items 缺少字段：{field}")
            continue
        item = items[field]
        if not isinstance(item, dict):
            errors.append(f"items.{field} 不是对象")
            continue
        for extra in set(item) - {"result", "reason"}:
            errors.append(f"items.{field} 有多余键：{extra}")
        if "result" not in item:
            errors.append(f"items.{field} 缺少 result")
        elif item["result"] not in allowed_results:
            errors.append(f"items.{field}.result 不在允许值内：{item['result']!r}")
        if "reason" not in item:
            errors.append(f"items.{field} 缺少 reason")
        elif not isinstance(item["reason"], str) or not 1 <= len(item["reason"]) <= 120:
            errors.append(f"items.{field}.reason 必须是 1-120 字字符串")
    return errors


def ollama_chat(base_url: str, model: str, prompt: str, image_b64: str, args):
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": args.temperature,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
        "keep_alive": args.keep_alive,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1200]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Ollama 请求失败：{exc}") from exc
    if "error" in payload:
        raise RuntimeError(f"Ollama 返回错误：{payload['error']}")
    message = payload.get("message") or {}
    content = message.get("content", "")
    meta = {
        key: payload.get(key)
        for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
        if key in payload
    }
    return content, meta


def call_one(
    item: dict,
    images_root: Path,
    prompt: str,
    args,
    extra: dict | None = None,
):
    image_path = images_root / item["file"]
    if not image_path.exists():
        alt = ROOT / "data" / item["file"]
        if alt.exists():
            image_path = alt
    if not image_path.exists():
        return {
            "id": item["id"],
            "file": item["file"],
            "model": args.model,
            "prompt": prompt,
            "raw_response": None,
            "parsed": None,
            "validation_errors": [],
            "error": f"图片不存在：{image_path}",
            "elapsed_s": 0.0,
            "response_meta": {},
            "gold": item.get("labels"),
            **(extra or {}),
        }
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    started = time.time()
    try:
        content, meta = ollama_chat(args.ollama_url, args.model, prompt, image_b64, args)
        error = None
    except Exception as exc:
        content, meta, error = None, {}, str(exc)
    elapsed = round(time.time() - started, 2)
    parsed = None
    validation_errors = []
    if content is not None:
        try:
            parsed = parse_json_text(content)
        except Exception as exc:
            validation_errors = [f"JSON 解析失败：{exc}"]
    if parsed is not None:
        if (extra or {}).get("variant") == "correct" or (extra or {}).get("variant") == "error":
            validation_errors = validate_verify(parsed, read_json(ROOT / "vision/schema/verify.schema.json"))
        else:
            validation_errors = validate_deconstruct(
                parsed, read_json(ROOT / "vision/schema/deconstruct.schema.json")
            )
    return {
        "id": item["id"],
        "file": item["file"],
        "model": args.model,
        "prompt": prompt,
        "raw_response": content,
        "parsed": parsed,
        "validation_errors": validation_errors,
        "error": error,
        "elapsed_s": elapsed,
        "response_meta": meta,
        "gold": item.get("labels"),
        **(extra or {}),
    }


def summarize_deconstruct(records: list[dict], model: str):
    summary = {
        "model": model,
        "n_images": len(records),
        "format_valid": 0,
        "format_valid_rate": None,
        "enum_correct": 0,
        "enum_total": len(records) * len(ENUM_FIELDS),
        "enum_accuracy": None,
        "certain_total": 0,
        "certain_wrong": 0,
        "certain_wrong_rate": None,
        "unseen_gold_total": 0,
        "unseen_gold_answered_unseen": 0,
        "unseen_recall": None,
        "fields": {},
        "details": [],
        "api_errors": [],
        "free_text": {},
    }
    for record in records:
        if record.get("error"):
            summary["api_errors"].append({"id": record["id"], "error": record["error"]})
        parsed = record.get("parsed")
        valid = isinstance(parsed, dict) and not record.get("validation_errors")
        if valid:
            summary["format_valid"] += 1
        detail = {"id": record["id"], "fields": {}, "wrong_fields": []}
        for field in ENUM_FIELDS:
            gold = canonical_value(field, (record.get("gold") or {}).get(field))
            item = parsed.get(field) if isinstance(parsed, dict) else None
            pred_raw = item.get("value") if isinstance(item, dict) else None
            confidence = item.get("confidence") if isinstance(item, dict) else None
            pred = canonical_value(field, pred_raw)
            correct = pred is not None and pred == gold
            field_stats = summary["fields"].setdefault(
                field,
                {
                    "total": 0,
                    "correct": 0,
                    "certain_total": 0,
                    "certain_wrong": 0,
                    "unseen_gold_total": 0,
                    "unseen_gold_answered_unseen": 0,
                    "pred_unseen": 0,
                },
            )
            field_stats["total"] += 1
            if correct:
                field_stats["correct"] += 1
                summary["enum_correct"] += 1
            else:
                detail["wrong_fields"].append(field)
            if confidence == "确定":
                field_stats["certain_total"] += 1
                summary["certain_total"] += 1
                if not correct:
                    field_stats["certain_wrong"] += 1
                    summary["certain_wrong"] += 1
            if gold == "看不出":
                field_stats["unseen_gold_total"] += 1
                summary["unseen_gold_total"] += 1
                if pred == "看不出":
                    field_stats["unseen_gold_answered_unseen"] += 1
                    summary["unseen_gold_answered_unseen"] += 1
            if pred == "看不出":
                field_stats["pred_unseen"] += 1
            detail["fields"][field] = {
                "gold": gold,
                "pred": pred,
                "pred_raw": pred_raw,
                "confidence": confidence,
                "correct": correct,
            }
        # 自由文本只做宽松统计，不作为主要成功标准
        gold_pattern = str((record.get("gold") or {}).get("pattern", "")).strip()
        pred_pattern = ""
        if isinstance(parsed, dict):
            pred_pattern = str((parsed.get("pattern") or {}).get("value", "")).strip()
        pattern_exact = bool(gold_pattern) and gold_pattern == pred_pattern
        gold_colors = (record.get("gold") or {}).get("colors") or []
        pred_colors = []
        if isinstance(parsed, dict):
            raw_colors = (parsed.get("colors") or {}).get("value")
            if isinstance(raw_colors, list):
                pred_colors = raw_colors
        color_set_match = set(map(str, gold_colors)) == set(map(str, pred_colors))
        detail["pattern"] = {
            "gold": gold_pattern,
            "pred": pred_pattern,
            "exact": pattern_exact,
        }
        detail["colors"] = {
            "gold": gold_colors,
            "pred": pred_colors,
            "set_match": color_set_match,
        }
        summary["details"].append(detail)
    summary["format_valid_rate"] = ratio(summary["format_valid"], len(records))
    summary["enum_accuracy"] = ratio(summary["enum_correct"], summary["enum_total"])
    summary["certain_wrong_rate"] = ratio(summary["certain_wrong"], summary["certain_total"])
    summary["unseen_recall"] = ratio(
        summary["unseen_gold_answered_unseen"], summary["unseen_gold_total"]
    )
    summary["free_text"] = {
        "pattern_exact": sum(1 for d in summary["details"] if d["pattern"]["exact"]),
        "pattern_total": len(summary["details"]),
        "colors_set_match": sum(1 for d in summary["details"] if d["colors"]["set_match"]),
        "colors_total": len(summary["details"]),
    }
    return summary


def render_deconstruct_report(summary: dict, out_dir: Path) -> str:
    lines = [
        "# 看图解构基线评测",
        "",
        f"**模型：** `{summary['model']}`  ",
        f"**测试集：** {summary['n_images']} 张上服真实图（只测试，未训练）  ",
        "**任务：** 看图输出规格 JSON，每个字段附把握程度。",
        "",
        "## 一句话结论",
        "",
        (
            f"格式合法率 {pct(summary['format_valid'], summary['n_images'])}，"
            f"五个枚举字段总体准确率 {pct(summary['enum_correct'], summary['enum_total'])}，"
            f"“确定但错误”率 {pct(summary['certain_wrong'], summary['certain_total'])}。"
        ),
        "",
        "## 总体指标",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 格式合法 | {summary['format_valid']} / {summary['n_images']}（{pct(summary['format_valid'], summary['n_images'])}） |",
        f"| 枚举字段准确 | {summary['enum_correct']} / {summary['enum_total']}（{pct(summary['enum_correct'], summary['enum_total'])}） |",
        f"| 标为“确定”的字段 | {summary['certain_total']} |",
        f"| 确定但错误 | {summary['certain_wrong']}（{pct(summary['certain_wrong'], summary['certain_total'])}） |",
        f"| 金标准为“看不出”时答“看不出” | {summary['unseen_gold_answered_unseen']} / {summary['unseen_gold_total']}（{pct(summary['unseen_gold_answered_unseen'], summary['unseen_gold_total'])}） |",
        "",
        "## 分字段",
        "",
        "| 字段 | 准确 | 标为“确定” | 确定但错 | 金标准“看不出”时答“看不出” | 模型答“看不出” |",
        "|---|---|---|---|---|---|",
    ]
    labels = {
        "collar_family": "领型",
        "front_closure": "闭合方式",
        "buttons_visible": "纽扣可见",
        "length": "衣长",
        "silhouette": "廓形",
    }
    for field in ENUM_FIELDS:
        stats = summary["fields"].get(field, {})
        lines.append(
            "| {label} | {correct}/{total}（{acc}） | {certain} | {wrong}（{wrong_rate}） | {unseen_ok}/{unseen_total}（{unseen_rate}） | {pred_unseen}/{total} |".format(
                label=labels[field],
                correct=stats.get("correct", 0),
                total=stats.get("total", 0),
                acc=pct(stats.get("correct", 0), stats.get("total", 0)),
                certain=stats.get("certain_total", 0),
                wrong=stats.get("certain_wrong", 0),
                wrong_rate=pct(stats.get("certain_wrong", 0), stats.get("certain_total", 0)),
                unseen_ok=stats.get("unseen_gold_answered_unseen", 0),
                unseen_total=stats.get("unseen_gold_total", 0),
                unseen_rate=pct(
                    stats.get("unseen_gold_answered_unseen", 0),
                    stats.get("unseen_gold_total", 0),
                ),
                pred_unseen=stats.get("pred_unseen", 0),
            )
        )
    lines += [
        "",
        "## 逐图结果",
        "",
        "✅ 正确，❌ 错误，括号内是模型自报的把握程度。",
        "",
        "| 图片 | 领型 | 闭合 | 纽扣 | 衣长 | 廓形 | 错误字段 |",
        "|---|---|---|---|---|---|---|",
    ]
    for detail in summary["details"]:
        cells = []
        for field in ENUM_FIELDS:
            item = detail["fields"][field]
            if item["pred"] is None:
                cells.append("—")
            else:
                mark = "✅" if item["correct"] else "❌"
                cells.append(f"{mark} {item['pred']}（{item['confidence'] or '?'}）")
        wrong = "、".join(labels[f] for f in detail["wrong_fields"]) or "—"
        lines.append(f"| `{detail['id']}` | " + " | ".join(cells) + f" | {wrong} |")
    lines += [
        "",
        "## 自由文本",
        "",
        "| 字段 | 完全相同 |",
        "|---|---|",
        f"| 纹样 | {summary['free_text']['pattern_exact']} / {summary['free_text']['pattern_total']} |",
        f"| 颜色（集合完全一致） | {summary['free_text']['colors_set_match']} / {summary['free_text']['colors_total']} |",
        "",
        "纹样和颜色是自由文本，不纳入主要成功标准；逐图原文见原始输出。",
        "",
        "## 典型错误",
        "",
    ]
    shown = 0
    for detail in summary["details"]:
        for field in detail["wrong_fields"]:
            item = detail["fields"][field]
            lines.append(
                f"- `{detail['id']}` / {labels[field]}：金标准 `{item['gold']}`，"
                f"模型 `{item['pred']}`，把握程度 `{item['confidence']}`"
            )
            shown += 1
            if shown >= 15:
                break
        if shown >= 15:
            break
    if shown == 0:
        lines.append("- 没有枚举字段错误。")
    lines += [
        "",
        "## 局限",
        "",
        "- 测试集只有 15 张图；单个字段错 1 张，该字段准确率就变化约 6.7 个百分点。",
        f"- 这是量化后的模型（{summary['model']}），结果不能代表未量化模型或微调后的模型。",
        "- 本次没有训练，也没有把上服图片用于训练或提示词示例。",
        "",
        f"原始逐图输出：`{out_dir / 'deconstruct' / 'raw'}`  ",
        f"机器可读汇总：`{out_dir / 'deconstruct' / 'summary.json'}`",
    ]
    return "\n".join(lines)


def run_deconstruct(items: list[dict], args, out_dir: Path):
    prompt = (ROOT / "vision/schema/deconstruct_prompt.txt").read_text(encoding="utf-8")
    raw_dir = out_dir / "deconstruct" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index, item in enumerate(items, 1):
        raw_path = raw_dir / f"{item['id']}.json"
        if args.resume and raw_path.exists():
            existing = read_json(raw_path)
            if existing.get("parsed") is not None:
                print(f"[{index}/{len(items)}] {item['id']} 已有结果，跳过", flush=True)
                records.append(existing)
                continue
        print(f"[{index}/{len(items)}] {item['id']} 推理中…", flush=True)
        record = call_one(item, Path(args.images_root), prompt, args)
        write_json(raw_path, record)
        records.append(record)
        if record["error"]:
            print(f"  失败：{record['error']}", flush=True)
        else:
            print(
                f"  完成 {record['elapsed_s']}s，格式错误 {len(record['validation_errors'])} 项",
                flush=True,
            )
    summary = summarize_deconstruct(records, args.model)
    write_json(out_dir / "deconstruct" / "summary.json", summary)
    write_text(out_dir / "deconstruct" / "report.md", render_deconstruct_report(summary, out_dir))
    return summary


def next_value(schema: dict, field: str, current):
    values = [v for v in enum_values(schema, field) if v != "看不出"]
    current_canonical = canonical_value(field, current)
    for value in values:
        if canonical_value(field, value) != current_canonical:
            return value
    return values[0]


def make_mutations(labels: dict, index: int, schema: dict):
    concrete = [
        field
        for field in ENUM_FIELDS
        if labels.get(field) not in (None, "看不出")
    ]
    if not concrete:
        return {}
    count = 1 if index % 2 == 0 else 2
    count = min(count, len(concrete))
    start = index % len(concrete)
    ordered = concrete[start:] + concrete[:start]
    mutations = {}
    for field in ordered[:count]:
        mutations[field] = {
            "from": labels.get(field),
            "to": next_value(schema, field, labels.get(field)),
        }
    return mutations


def summarize_verify(records: list[dict], model: str):
    summary = {
        "model": model,
        "n_images": len({r["id"] for r in records}),
        "n_calls": len(records),
        "format_valid": 0,
        "format_valid_rate": None,
        "correct_concrete_total": 0,
        "correct_compliant": 0,
        "correct_false_alarm": 0,
        "correct_abstain": 0,
        "unseen_spec_total": 0,
        "unseen_spec_result": 0,
        "mutated_total": 0,
        "mutated_caught": 0,
        "mutated_missed": 0,
        "mutated_abstain": 0,
        "nonmutated_concrete_total": 0,
        "nonmutated_false_alarm": 0,
        "nonmutated_abstain": 0,
        "catch_rate": None,
        "false_alarm_rate": None,
        "details": [],
        "api_errors": [],
    }
    for record in records:
        if record.get("error"):
            summary["api_errors"].append({"id": record["id"], "error": record["error"]})
        parsed = record.get("parsed")
        valid = isinstance(parsed, dict) and not record.get("validation_errors")
        if valid:
            summary["format_valid"] += 1
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, dict) and isinstance(parsed, dict):
            items = parsed
        items = items if isinstance(items, dict) else {}
        spec = record.get("spec") or {}
        variant = record.get("variant")
        mutations = record.get("mutations") or {}
        detail = {
            "id": record["id"],
            "variant": variant,
            "mutations": mutations,
            "fields": {},
        }
        for field in ENUM_FIELDS:
            result = None
            reason = None
            item = items.get(field)
            if isinstance(item, dict):
                result = item.get("result")
                reason = item.get("reason")
            spec_value = spec.get(field)
            concrete = spec_value not in (None, "看不出")
            detail["fields"][field] = {
                "spec": spec_value,
                "result": result,
                "reason": reason,
            }
            if variant == "correct":
                if concrete:
                    summary["correct_concrete_total"] += 1
                    if result == "符合":
                        summary["correct_compliant"] += 1
                    elif result == "不符合":
                        summary["correct_false_alarm"] += 1
                    else:
                        summary["correct_abstain"] += 1
                else:
                    summary["unseen_spec_total"] += 1
                    if result == "看不出":
                        summary["unseen_spec_result"] += 1
            elif variant == "error":
                if field in mutations:
                    summary["mutated_total"] += 1
                    if result == "不符合":
                        summary["mutated_caught"] += 1
                    elif result == "符合":
                        summary["mutated_missed"] += 1
                    else:
                        summary["mutated_abstain"] += 1
                elif concrete:
                    summary["nonmutated_concrete_total"] += 1
                    if result == "不符合":
                        summary["nonmutated_false_alarm"] += 1
                    elif result != "符合":
                        summary["nonmutated_abstain"] += 1
        summary["details"].append(detail)
    summary["format_valid_rate"] = ratio(summary["format_valid"], len(records))
    summary["catch_rate"] = ratio(summary["mutated_caught"], summary["mutated_total"])
    summary["false_alarm_rate"] = ratio(
        summary["nonmutated_false_alarm"], summary["nonmutated_concrete_total"]
    )
    return summary


def render_verify_report(summary: dict, out_dir: Path) -> str:
    lines = [
        "# 效果图核对基线评测",
        "",
        f"**模型：** `{summary['model']}`  ",
        f"**测试集：** {summary['n_images']} 张上服真实图，共 {summary['n_calls']} 次调用  ",
        "**任务：** 图片 + 规格，逐项判断符合 / 不符合 / 看不出。",
        "",
        "## 一句话结论",
        "",
        (
            f"格式合法率 {pct(summary['format_valid'], summary['n_calls'])}，"
            f"故意改错项抓出率 {pct(summary['mutated_caught'], summary['mutated_total'])}，"
            f"正确项误报率 {pct(summary['nonmutated_false_alarm'], summary['nonmutated_concrete_total'])}。"
        ),
        "",
        "## 总体指标",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 格式合法 | {summary['format_valid']} / {summary['n_calls']}（{pct(summary['format_valid'], summary['n_calls'])}） |",
        f"| 全部正确规格：判“符合” | {summary['correct_compliant']} / {summary['correct_concrete_total']}（{pct(summary['correct_compliant'], summary['correct_concrete_total'])}） |",
        f"| 全部正确规格：误判“不符合” | {summary['correct_false_alarm']} / {summary['correct_concrete_total']}（{pct(summary['correct_false_alarm'], summary['correct_concrete_total'])}） |",
        f"| 规格为“看不出”时也答“看不出” | {summary['unseen_spec_result']} / {summary['unseen_spec_total']}（{pct(summary['unseen_spec_result'], summary['unseen_spec_total'])}） |",
        f"| 改错项抓出 | {summary['mutated_caught']} / {summary['mutated_total']}（{pct(summary['mutated_caught'], summary['mutated_total'])}） |",
        f"| 改错项漏掉 | {summary['mutated_missed']} / {summary['mutated_total']}（{pct(summary['mutated_missed'], summary['mutated_total'])}） |",
        f"| 改错项答“看不出” | {summary['mutated_abstain']} / {summary['mutated_total']}（{pct(summary['mutated_abstain'], summary['mutated_total'])}） |",
        f"| 未改项误报“不符合” | {summary['nonmutated_false_alarm']} / {summary['nonmutated_concrete_total']}（{pct(summary['nonmutated_false_alarm'], summary['nonmutated_concrete_total'])}） |",
        "",
        "## 局限",
        "",
        "- 改错样本是程序按固定规则改 1–2 个枚举字段，不等同于真实客户的全部冲突类型。",
        "- 只有 15 张图；单个结果变化就会造成明显的百分比波动。",
        "- 这是量化后的 3B 模型，不能代表 7B 或微调后的模型。",
        "",
        f"原始逐条输出：`{out_dir / 'verify' / 'raw'}`  ",
        f"机器可读汇总：`{out_dir / 'verify' / 'summary.json'}`",
    ]
    return "\n".join(lines)


def run_verify(items: list[dict], args, out_dir: Path, schema: dict, enum_schema: dict):
    template = (ROOT / "vision/schema/verify_prompt.txt").read_text(encoding="utf-8")
    raw_dir = out_dir / "verify" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    total = len(items) * 2
    done = 0
    for index, item in enumerate(items):
        labels = item["labels"]
        mutations = make_mutations(labels, index, enum_schema)
        variants = [
            ("correct", {field: labels.get(field) for field in ALL_FIELDS}, {}),
            (
                "error",
                {
                    field: (
                        mutations[field]["to"]
                        if field in mutations
                        else labels.get(field)
                    )
                    for field in ALL_FIELDS
                },
                mutations,
            ),
        ]
        for variant, spec, applied in variants:
            done += 1
            raw_path = raw_dir / f"{variant}__{item['id']}.json"
            if args.resume and raw_path.exists():
                existing = read_json(raw_path)
                if existing.get("parsed") is not None:
                    print(f"[{done}/{total}] {variant} {item['id']} 已有结果，跳过", flush=True)
                    records.append(existing)
                    continue
            prompt = template.replace(
                "{{SPEC_JSON}}",
                json.dumps(spec, ensure_ascii=False, indent=2),
            )
            print(f"[{done}/{total}] {variant} {item['id']} 推理中…", flush=True)
            record = call_one(
                item,
                Path(args.images_root),
                prompt,
                args,
                extra={"variant": variant, "spec": spec, "mutations": applied},
            )
            write_json(raw_path, record)
            records.append(record)
            if record["error"]:
                print(f"  失败：{record['error']}", flush=True)
            else:
                print(
                    f"  完成 {record['elapsed_s']}s，格式错误 {len(record['validation_errors'])} 项",
                    flush=True,
                )
    summary = summarize_verify(records, args.model)
    write_json(out_dir / "verify" / "summary.json", summary)
    write_text(out_dir / "verify" / "report.md", render_verify_report(summary, out_dir))
    return summary


def select_items(items: list[dict], args):
    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        items = [item for item in items if item["id"] in wanted]
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default=str(ROOT / "vision/results/gold_labels.json"))
    parser.add_argument("--images-root", default=str(ROOT / "data"))
    parser.add_argument("--out-dir", default=str(ROOT / "vision/results/baseline_qwen2.5vl_3b"))
    parser.add_argument("--model", default="qwen2.5vl:3b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--task", choices=["deconstruct", "verify", "all"], default="deconstruct")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 张，0 表示全部")
    parser.add_argument("--only", default="", help="只跑指定 id，逗号分隔")
    parser.add_argument("--resume", action="store_true", help="跳过已有有效结果的图片")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=768)
    parser.add_argument("--keep-alive", default="10m")
    args = parser.parse_args()

    labels_file = Path(args.labels)
    gold = read_json(labels_file)
    items = select_items(gold["images"], args)
    if not items:
        print("没有选中任何图片。", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    deconstruct_schema = read_json(ROOT / "vision/schema/deconstruct.schema.json")
    verify_schema = read_json(ROOT / "vision/schema/verify.schema.json")
    print(
        f"模型 {args.model}，图片 {len(items)} 张，输出 {out_dir}",
        flush=True,
    )
    if args.task in {"deconstruct", "all"}:
        print("\n=== 看图解构 ===", flush=True)
        summarize = run_deconstruct(items, args, out_dir)
        print(
            "解构完成："
            f"格式 {summarize['format_valid']}/{summarize['n_images']}，"
            f"枚举准确 {summarize['enum_correct']}/{summarize['enum_total']}，"
            f"确定但错 {summarize['certain_wrong']}/{summarize['certain_total']}",
            flush=True,
        )
    if args.task in {"verify", "all"}:
        print("\n=== 效果图核对 ===", flush=True)
        summarize = run_verify(items, args, out_dir, verify_schema, deconstruct_schema)
        print(
            "核对完成："
            f"抓错 {summarize['mutated_caught']}/{summarize['mutated_total']}，"
            f"误报 {summarize['nonmutated_false_alarm']}/{summarize['nonmutated_concrete_total']}",
            flush=True,
        )
    print(f"\n报告：{out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""用已有的独立解构结果核对方案规格，避免模型被规格锚定。

输入：
  --deconstruct-dir  含 raw/<id>.json 的解构结果目录
  --labels           金标准，用来构造“全对”和“改错”两套规格

输出：
  逐项“符合/不符合/看不出”和理由，以及抓错率、误报率。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("eval_lib", HERE / "eval.py")
eval_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_lib)


FIELD_LABELS = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "buttons_visible": "纽扣可见",
    "length": "衣长",
    "silhouette": "廓形",
}


def compare_field(field: str, prediction, spec_value):
    if spec_value is None or spec_value == "看不出":
        return "看不出", "规格未给出具体值"
    if prediction is None or prediction == "看不出":
        return "看不出", "图片无法判断"
    if eval_lib.canonical_value(field, prediction) == eval_lib.canonical_value(
        field, spec_value
    ):
        return "符合", f"图片判断为 {prediction}"
    return "不符合", f"图片判断为 {prediction}，规格为 {spec_value}"


def load_predictions(deconstruct_dir: Path):
    raw_dir = deconstruct_dir / "raw"
    predictions = {}
    for path in sorted(raw_dir.glob("*.json")):
        record = eval_lib.read_json(path)
        if record.get("parsed"):
            predictions[record["id"]] = record
    return predictions


def build_rows(items: list[dict], predictions: dict, enum_schema: dict):
    rows = []
    for index, item in enumerate(items):
        record = predictions.get(item["id"])
        if not record:
            continue
        parsed = record["parsed"]
        labels = item["labels"]
        mutations = eval_lib.make_mutations(labels, index, enum_schema)
        variants = [
            ("correct", {field: labels.get(field) for field in eval_lib.ENUM_FIELDS}, {}),
            (
                "error",
                {
                    field: (
                        mutations[field]["to"]
                        if field in mutations
                        else labels.get(field)
                    )
                    for field in eval_lib.ENUM_FIELDS
                },
                mutations,
            ),
        ]
        for variant, spec_values, applied in variants:
            items_out = {}
            for field in eval_lib.ENUM_FIELDS:
                pred = (parsed.get(field) or {}).get("value")
                confidence = (parsed.get(field) or {}).get("confidence")
                result, reason = compare_field(field, pred, spec_values.get(field))
                items_out[field] = {
                    "spec": spec_values.get(field),
                    "prediction": pred,
                    "prediction_confidence": confidence,
                    "result": result,
                    "reason": reason,
                }
            rows.append(
                {
                    "id": item["id"],
                    "variant": variant,
                    "mutations": applied,
                    "items": items_out,
                }
            )
    return rows


def summarize(rows: list[dict]):
    summary = {
        "n_images": len({row["id"] for row in rows}),
        "n_samples": len(rows),
        "correct_concrete_total": 0,
        "correct_compliant": 0,
        "correct_false_alarm": 0,
        "correct_abstain": 0,
        "mutated_total": 0,
        "mutated_caught": 0,
        "mutated_missed": 0,
        "mutated_abstain": 0,
        "nonmutated_concrete_total": 0,
        "nonmutated_false_alarm": 0,
        "nonmutated_abstain": 0,
        "fields": {},
    }
    for row in rows:
        for field in eval_lib.ENUM_FIELDS:
            item = row["items"][field]
            spec_value = item["spec"]
            concrete = spec_value not in (None, "看不出")
            stats = summary["fields"].setdefault(
                field,
                {
                    "correct_total": 0,
                    "correct_compliant": 0,
                    "correct_false_alarm": 0,
                    "mutated_total": 0,
                    "mutated_caught": 0,
                    "nonmutated_total": 0,
                    "nonmutated_false_alarm": 0,
                },
            )
            if row["variant"] == "correct":
                if concrete:
                    summary["correct_concrete_total"] += 1
                    stats["correct_total"] += 1
                    if item["result"] == "符合":
                        summary["correct_compliant"] += 1
                        stats["correct_compliant"] += 1
                    elif item["result"] == "不符合":
                        summary["correct_false_alarm"] += 1
                        stats["correct_false_alarm"] += 1
                    else:
                        summary["correct_abstain"] += 1
            elif row["variant"] == "error":
                if field in (row["mutations"] or {}):
                    summary["mutated_total"] += 1
                    stats["mutated_total"] += 1
                    if item["result"] == "不符合":
                        summary["mutated_caught"] += 1
                        stats["mutated_caught"] += 1
                    elif item["result"] == "符合":
                        summary["mutated_missed"] += 1
                    else:
                        summary["mutated_abstain"] += 1
                elif concrete:
                    summary["nonmutated_concrete_total"] += 1
                    stats["nonmutated_total"] += 1
                    if item["result"] == "不符合":
                        summary["nonmutated_false_alarm"] += 1
                        stats["nonmutated_false_alarm"] += 1
                    elif item["result"] == "看不出":
                        summary["nonmutated_abstain"] += 1
    summary["catch_rate"] = eval_lib.ratio(
        summary["mutated_caught"], summary["mutated_total"]
    )
    summary["false_alarm_rate"] = eval_lib.ratio(
        summary["nonmutated_false_alarm"], summary["nonmutated_concrete_total"]
    )
    return summary


def render_report(summary: dict, model: str, out_path: Path):
    lines = [
        "# 解构后再核对（避免规格锚定）",
        "",
        f"**来源解构模型：** `{model}`  ",
        f"**样本：** {summary['n_images']} 张图 × 2 套规格 = {summary['n_samples']} 次比对  ",
        "**方法：** 先独立看图解构，再由程序把解构值和规格逐项比较；",
        "不把规格提前给模型，避免模型顺着规格说“符合”。",
        "",
        "## 一句话结论",
        "",
        (
            f"故意改错项抓出率 {eval_lib.pct(summary['mutated_caught'], summary['mutated_total'])}，"
            f"正确项误报率 {eval_lib.pct(summary['nonmutated_false_alarm'], summary['nonmutated_concrete_total'])}。"
        ),
        "",
        "## 总体",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 全对规格：判“符合” | {summary['correct_compliant']} / {summary['correct_concrete_total']}（{eval_lib.pct(summary['correct_compliant'], summary['correct_concrete_total'])}） |",
        f"| 全对规格：误判“不符合” | {summary['correct_false_alarm']} / {summary['correct_concrete_total']}（{eval_lib.pct(summary['correct_false_alarm'], summary['correct_concrete_total'])}） |",
        f"| 改错项抓出 | {summary['mutated_caught']} / {summary['mutated_total']}（{eval_lib.pct(summary['mutated_caught'], summary['mutated_total'])}） |",
        f"| 改错项漏掉 | {summary['mutated_missed']} / {summary['mutated_total']}（{eval_lib.pct(summary['mutated_missed'], summary['mutated_total'])}） |",
        f"| 改错项答“看不出” | {summary['mutated_abstain']} / {summary['mutated_total']}（{eval_lib.pct(summary['mutated_abstain'], summary['mutated_total'])}） |",
        f"| 未改项误报“不符合” | {summary['nonmutated_false_alarm']} / {summary['nonmutated_concrete_total']}（{eval_lib.pct(summary['nonmutated_false_alarm'], summary['nonmutated_concrete_total'])}） |",
        "",
        "## 分字段",
        "",
        "| 字段 | 全对判符合 | 全对误报 | 改错抓出 | 未改误报 |",
        "|---|---|---|---|---|",
    ]
    for field in eval_lib.ENUM_FIELDS:
        stats = summary["fields"][field]
        lines.append(
            f"| {FIELD_LABELS[field]} | {stats['correct_compliant']}/{stats['correct_total']}"
            f"（{eval_lib.pct(stats['correct_compliant'], stats['correct_total'])}）"
            f" | {stats['correct_false_alarm']}/{stats['correct_total']}"
            f"（{eval_lib.pct(stats['correct_false_alarm'], stats['correct_total'])}）"
            f" | {stats['mutated_caught']}/{stats['mutated_total']}"
            f"（{eval_lib.pct(stats['mutated_caught'], stats['mutated_total'])}）"
            f" | {stats['nonmutated_false_alarm']}/{stats['nonmutated_total']}"
            f"（{eval_lib.pct(stats['nonmutated_false_alarm'], stats['nonmutated_total'])}） |"
        )
    lines += [
        "",
        "## 解释",
        "",
        "- 这个方法的抓错能力直接受解构准确率限制：解构错，核对就会误报。",
        "- 但至少不会出现“规格写什么、模型就说图片是什么”的锚定问题。",
        "- 产品里建议显示“图片判断为 X，规格为 Y”，让用户复核，而不是只给一个“不符合”。",
        "",
        f"机器可读汇总：`{out_path}`",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default=str(ROOT / "vision/results/gold_labels.json"))
    parser.add_argument(
        "--deconstruct-dir",
        default=str(ROOT / "vision/results/simple_probe_qwen2.5vl_7b_v1"),
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "vision/results/baseline_qwen2.5vl_7b"),
    )
    args = parser.parse_args()
    gold = eval_lib.read_json(Path(args.labels))
    predictions = load_predictions(Path(args.deconstruct_dir))
    if not predictions:
        raise SystemExit("没有找到可用的解构结果")
    enum_schema = eval_lib.read_json(ROOT / "vision/schema/deconstruct.schema.json")
    rows = build_rows(gold["images"], predictions, enum_schema)
    summary = summarize(rows)
    model = next(iter(predictions.values())).get("model", "unknown")
    out_dir = Path(args.out_dir)
    out_json = out_dir / "verify_compare.json"
    eval_lib.write_json(out_json, {"model": model, "summary": summary, "rows": rows})
    eval_lib.write_text(
        out_dir / "verify_compare_report.md",
        render_report(summary, model, out_json),
    )
    print(
        json.dumps(
            {
                "catch_rate": summary["catch_rate"],
                "false_alarm_rate": summary["false_alarm_rate"],
                "mutated": summary["mutated_total"],
                "nonmutated": summary["nonmutated_concrete_total"],
            },
            ensure_ascii=False,
        )
    )
    print(f"报告：{out_dir / 'verify_compare_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

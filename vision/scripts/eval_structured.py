#!/usr/bin/env python3
"""Evaluate local Qwen2.5-VL models with a fixed JSON schema."""

from __future__ import annotations

import argparse
import base64
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]

PROMPT = """你是服装属性标注员。只描述图片中的上装，不要描述内搭、裤装或背景。
严格按给定 JSON 字段输出，不要解释。看不清时必须回答“看不出”，不要猜。

字段定义：
- 领型：立领=领子向上立起包住或贴近脖子；交领=左右衣襟在胸前交叠；翻领=有明显向外翻折的领片，包括青果领和宽翻领；斜襟=门襟斜向一侧；圆领=无领片的圆弧领口。
- 闭合方式：外露扣=正面能看到扣子；无扣开襟=前襟直接敞开；交叠闭合=衣襟交叠且没有明显系带或外露扣；系带=主要靠带子固定。
- 纽扣：上装正面至少能看到一颗扣子为“可见”，否则为“未见”。
- 衣长：以图中人物的腰线、臀线和大腿位置为参照；“过臀”表示衣摆明显越过臀线。
- 廓形：H 型=肩、腰、下摆外轮廓大体垂直；收腰=腰部明显向内收；A 摆=下摆明显向外扩。
- 花纹：无=基本无纹样；暗纹=同色系提花、压纹、肌理或低对比纹样；明显图案=远看即可辨认的大图案。
- 主色：只取上装面积最大的颜色，不把内搭、皮肤和背景算进去。深灰黑归入“深灰”。
"""

FIELD_LABELS = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "buttons_visible": "纽扣",
    "length": "衣长",
    "silhouette": "廓形",
    "pattern_category": "花纹",
    "primary_color": "主色",
}

TIER_LABELS = {
    "real_gold": "真实回归集（人工确认）",
    "synthetic_prompt": "合成压力集（标签未逐张复核）",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_schema(field_schema: dict):
    properties = {key: {"type": "string", "enum": values} for key, values in field_schema.items()}
    return {
        "type": "object",
        "properties": properties,
        "required": list(field_schema),
        "additionalProperties": False,
    }


def parse_answer(content: str, field_schema: dict):
    data = json.loads(content)
    if set(data) != set(field_schema):
        raise ValueError(f"字段不一致：{sorted(data)}")
    for key, allowed in field_schema.items():
        if data[key] not in allowed:
            raise ValueError(f"{key} 返回了非法值：{data[key]}")
    return data


def call_model(base_url: str, model: str, image_path: Path, schema: dict, field_schema: dict, timeout: int):
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    started = time.time()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": schema,
            "keep_alive": "10m",
            "options": {
                "temperature": 0,
                "num_predict": 256,
                "num_ctx": 8192,
            },
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT,
                    "images": [image_b64],
                }
            ],
        },
        timeout=timeout,
    )
    elapsed = time.time() - started
    response.raise_for_status()
    payload = response.json()
    return payload, parse_answer(payload["message"]["content"], field_schema), elapsed


def metric_for(rows: list[dict], field: str):
    evaluable = [row for row in rows if field in row["labels"]]
    correct = sum(row["predictions"].get(field) == row["labels"][field] for row in evaluable)
    unknown = [row for row in evaluable if row["labels"][field] == "看不出"]
    unknown_correct = sum(row["predictions"].get(field) == "看不出" for row in unknown)
    return {
        "total": len(evaluable),
        "correct": correct,
        "accuracy": correct / len(evaluable) if evaluable else None,
        "unknown_total": len(unknown),
        "unknown_correct": unknown_correct,
        "unknown_recall": unknown_correct / len(unknown) if unknown else None,
    }


def pct(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--labels",
        default=str(ROOT / "vision/data/benchmark_v2/labels.json"),
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--tier",
        action="append",
        choices=["real_gold", "synthetic_prompt"],
        default=[],
        help="只跑某个层级；可重复。默认全部。",
    )
    args = parser.parse_args()

    labels = load_json(Path(args.labels))
    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    field_schema = labels["meta"]["schema"]
    schema = build_schema(field_schema)
    images = labels["images"]
    if args.tier:
        images = [image for image in images if image["tier"] in args.tier]
    if args.limit:
        images = images[: args.limit]

    rows = []
    failures = []
    model_meta = {}
    started_all = time.time()
    for index, image in enumerate(images, start=1):
        image_path = ROOT / image["file"]
        print(
            f"[{index}/{len(images)}] {image['id']} {image_path.name}",
            flush=True,
        )
        record = {
            "id": image["id"],
            "source_id": image["source_id"],
            "tier": image["tier"],
            "file": image["file"],
            "labels": image["labels"],
            "evaluable_fields": image["evaluable_fields"],
        }
        try:
            payload, prediction, elapsed = call_model(
                args.ollama_url,
                args.model,
                image_path,
                schema,
                field_schema,
                args.timeout,
            )
            record.update(
                {
                    "predictions": prediction,
                    "elapsed_seconds": round(elapsed, 2),
                    "raw": payload,
                    "error": None,
                }
            )
            model_meta = {
                "model": payload.get("model"),
                "done_reason": payload.get("done_reason"),
                "total_duration_ns": payload.get("total_duration"),
                "load_duration_ns": payload.get("load_duration"),
                "eval_count": payload.get("eval_count"),
            }
        except Exception as exc:
            record.update({"predictions": {}, "elapsed_seconds": None, "raw": None, "error": str(exc)})
            failures.append({"id": image["id"], "error": str(exc)})
            print(f"  FAILED: {exc}", flush=True)
        rows.append(record)
        (raw_dir / f"{image['id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    tiers = {}
    for tier in sorted({row["tier"] for row in rows}):
        tier_rows = [row for row in rows if row["tier"] == tier and row["error"] is None]
        tiers[tier] = {
            field: metric_for(tier_rows, field)
            for field in field_schema
            if any(field in row["labels"] for row in tier_rows)
        }

    elapsed_all = time.time() - started_all
    summary = {
        "model": args.model,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "benchmark": str(Path(args.labels).resolve()),
        "image_count": len(images),
        "success_count": len(images) - len(failures),
        "failure_count": len(failures),
        "elapsed_seconds": round(elapsed_all, 1),
        "model_meta": model_meta,
        "tiers": tiers,
        "failures": failures,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        f"# 结构化评测：{args.model}",
        "",
        f"- 图片：{len(images)} 张，成功 {summary['success_count']}，失败 {len(failures)}",
        f"- 总耗时：{summary['elapsed_seconds']} 秒",
        f"- 本基准不是独立盲测集，只能做前后回归比较。",
        "",
    ]
    for tier, metrics in tiers.items():
        lines.extend([f"## {TIER_LABELS.get(tier, tier)}", "", "| 字段 | 正确/有效 | 准确率 | 看不出召回 |", "|---|---:|---:|---:|"])
        for field, metric in metrics.items():
            unknown = (
                f"{metric['unknown_correct']}/{metric['unknown_total']}"
                if metric["unknown_total"]
                else "—"
            )
            lines.append(
                f"| {FIELD_LABELS[field]} | {metric['correct']}/{metric['total']} | "
                f"{pct(metric['accuracy'])} | {unknown} |"
            )
        lines.append("")
    if failures:
        lines.extend(["## 失败项", ""])
        for failure in failures:
            lines.append(f"- `{failure['id']}`：{failure['error']}")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out_dir/'report.md'}")
    print(json.dumps(tiers, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

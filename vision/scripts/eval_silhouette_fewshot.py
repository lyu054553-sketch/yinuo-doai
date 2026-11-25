#!/usr/bin/env python3
"""Evaluate a two-example few-shot prompt for H / waist / A-line silhouette."""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_H = ROOT / "vision/data/pilot_20/images/p16.png"
EXAMPLE_WAIST = ROOT / "vision/data/pilot_20/images/p03.png"
PROMPT = """下面按顺序有三张图。
第一张是“H 型”开发示例：肩、腰、下摆外轮廓大体垂直。
第二张是“收腰”开发示例：腰部明显向内收。
第三张是待判断图片。

只看上装的整体外轮廓，不要根据颜色、扣子或内搭判断：
- H 型：肩到下摆大体是直线。
- 收腰：腰部明显收进，身体呈 X 形或明显掐腰。
- A 摆：下摆明显向外扩。
- 看不出：遮挡、裁切或角度导致无法判断。

只判断第三张图，严格输出 JSON。"""


def encode(path: Path):
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5vl:7b")
    parser.add_argument(
        "--labels",
        default=str(ROOT / "vision/data/benchmark_v2/labels.json"),
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    images = [image for image in labels["images"] if image["tier"] == "real_gold"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, item in enumerate(images, start=1):
        source = ROOT / item["file"]
        print(f"[{index}/{len(images)}] {item['source_id']}", flush=True)
        started = time.time()
        response = requests.post(
            f"{args.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": args.model,
                "stream": False,
                "format": {
                    "type": "object",
                    "properties": {
                        "silhouette": {
                            "type": "string",
                            "enum": ["H 型", "收腰", "A 摆", "看不出"],
                        }
                    },
                    "required": ["silhouette"],
                    "additionalProperties": False,
                },
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_predict": 40, "num_ctx": 8192},
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT,
                        "images": [encode(EXAMPLE_H), encode(EXAMPLE_WAIST), encode(source)],
                    }
                ],
            },
            timeout=args.timeout,
        )
        response.raise_for_status()
        data = response.json()
        prediction = json.loads(data["message"]["content"])["silhouette"]
        gold = item["labels"]["silhouette"]
        row = {
            "id": item["source_id"],
            "gold": gold,
            "prediction": prediction,
            "correct": prediction == gold,
            "elapsed_seconds": round(time.time() - started, 2),
            "raw": data,
        }
        rows.append(row)
        print(f"  gold={gold} pred={prediction}", flush=True)

    correct = sum(row["correct"] for row in rows)
    summary = {
        "model": args.model,
        "total": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
        "examples": [str(EXAMPLE_H.relative_to(ROOT)), str(EXAMPLE_WAIST.relative_to(ROOT))],
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# 廓形 few-shot：{args.model}",
        "",
        f"- 正确：{correct}/{len(rows)}",
        f"- 示例：H 型 `{EXAMPLE_H.name}`，收腰 `{EXAMPLE_WAIST.name}`（均为不在测试集中的合成图）",
        "",
        "| 图片 | 金标准 | few-shot 预测 | 正确 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['gold']} | {row['prediction']} | "
            f"{'是' if row['correct'] else '否'} |"
        )
    lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out_dir/'report.md'}")


if __name__ == "__main__":
    main()

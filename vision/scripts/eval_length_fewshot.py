#!/usr/bin/env python3
"""Evaluate a three-example few-shot prompt for garment length."""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = [
    (ROOT / "vision/data/pilot_20/images/p14.png", "到腰", "短款，衣摆接近自然腰线"),
    (ROOT / "vision/data/pilot_20/images/p12.png", "臀部附近", "衣摆覆盖到臀线附近"),
    (ROOT / "vision/data/pilot_20/images/p03.png", "过臀", "衣摆明显越过臀线"),
]
PROMPT = """下面按顺序有四张服装图。
前三张是开发示例，第四张才是待判断图片，只看第四张。

第一张：“到腰”。短款，衣摆接近自然腰线。
第二张：“臀部附近”。衣摆覆盖到臀线附近。
第三张：“过臀”。衣摆明显越过臀线。

比较第四张图里上装下摆与人物腰、臀的位置关系：
- 到腰：下摆约在自然腰线附近。
- 臀部附近：下摆覆盖到臀线附近。
- 过臀：下摆明显越过臀线，达到大腿上部。
- 看不出：没有腰臀参照、画面被截断，或衣服没有穿在可判断的人体上。

不要因为前三张示例的顺序猜答案。只判断第四张，严格输出 JSON。"""


def encode(path: Path, max_side: int = 896):
    image = Image.open(path).convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


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
    example_images = [encode(path) for path, _, _ in EXAMPLES]
    rows = []
    failures = []
    for index, item in enumerate(images, start=1):
        source = ROOT / item["file"]
        print(f"[{index}/{len(images)}] {item['source_id']}", flush=True)
        started = time.time()
        try:
            response = requests.post(
                f"{args.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": args.model,
                    "stream": False,
                    "format": {
                        "type": "object",
                        "properties": {
                            "length": {
                                "type": "string",
                                "enum": ["到腰", "臀部附近", "过臀", "看不出"],
                            }
                        }
                        ,
                        "required": ["length"],
                        "additionalProperties": False,
                    },
                    "keep_alive": "10m",
                    "options": {"temperature": 0, "num_predict": 40, "num_ctx": 8192},
                    "messages": [
                        {
                            "role": "user",
                            "content": PROMPT,
                            "images": [*example_images, encode(source)],
                        }
                    ],
                },
                timeout=args.timeout,
            )
            response.raise_for_status()
            data = response.json()
            prediction = json.loads(data["message"]["content"])["length"]
            gold = item["labels"]["length"]
            row = {
                "id": item["source_id"],
                "gold": gold,
                "prediction": prediction,
                "correct": prediction == gold,
                "elapsed_seconds": round(time.time() - started, 2),
                "raw": data,
                "error": None,
            }
        except Exception as exc:
            gold = item["labels"]["length"]
            row = {
                "id": item["source_id"],
                "gold": gold,
                "prediction": None,
                "correct": False,
                "elapsed_seconds": round(time.time() - started, 2),
                "raw": None,
                "error": str(exc),
            }
            failures.append({"id": item["source_id"], "error": str(exc)})
        rows.append(row)
        print(
            f"  gold={row['gold']} pred={row['prediction']}"
            + (f" error={row['error']}" if row["error"] else ""),
            flush=True,
        )

    correct = sum(row["correct"] for row in rows)
    summary = {
        "model": args.model,
        "total": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
        "examples": [
            {
                "file": str(path.relative_to(ROOT)),
                "label": label,
                "description": description,
            }
            for path, label, description in EXAMPLES
        ],
        "failures": failures,
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# 衣长 few-shot：{args.model}",
        "",
        f"- 正确：{correct}/{len(rows)}",
        f"- 成功返回：{len(rows) - len(failures)}/{len(rows)}",
        "- 示例图均为未进入测试集的合成图：p14 到腰、p12 臀部附近、p03 过臀。",
        "- 为让四张图同时装入上下文，发送前将最长边缩至 896 像素。",
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
    if failures:
        lines.extend(["## 失败项", ""])
        for failure in failures:
            lines.append(f"- `{failure['id']}`：{failure['error']}")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out_dir/'report.md'}")


if __name__ == "__main__":
    main()

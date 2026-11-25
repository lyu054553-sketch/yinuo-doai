#!/usr/bin/env python3
"""Compare full-image and segmented upper-garment crops for pattern recognition."""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import requests
from PIL import Image

from color_lab import crop_upper
from segment import create_session, foreground_with_session


ROOT = Path(__file__).resolve().parents[2]
PATTERN_PROMPT = """看图片中的上装面料，判断花纹类型。只描述上装，不要描述内搭或背景。
可选值：
- 无：基本没有纹样。
- 暗纹：同色系提花、压纹、肌理或低对比纹样，需要仔细看面料表面。
- 明显图案：远看即可辨认的印花、刺绣或大图案。
- 看不出：分辨率、光线、裁切或遮挡导致无法判断。
严格只输出 JSON，不要解释。"""


def call(session, base_url, model, image_path, timeout):
    image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    started = time.time()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": {
                "type": "object",
                "properties": {
                    "pattern_category": {
                        "type": "string",
                        "enum": ["无", "暗纹", "明显图案", "看不出"],
                    }
                },
                "required": ["pattern_category"],
                "additionalProperties": False,
            },
            "keep_alive": "10m",
            "options": {"temperature": 0, "num_predict": 40, "num_ctx": 8192},
            "messages": [
                {
                    "role": "user",
                    "content": PATTERN_PROMPT,
                    "images": [image],
                }
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    parsed = json.loads(data["message"]["content"])
    return parsed["pattern_category"], data, time.time() - started


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

    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        raise SystemExit("缺少 onnxruntime，请先安装到 .venv-vision。") from exc

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    images = [image for image in labels["images"] if image["tier"] == "real_gold"]
    out_dir = Path(args.out_dir)
    crop_dir = out_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    segment_session = create_session()
    rows = []
    for index, item in enumerate(images, start=1):
        source = ROOT / item["file"]
        print(f"[{index}/{len(images)}] {item['source_id']}", flush=True)
        rgba = foreground_with_session(Image.open(source), segment_session)
        crop, _ = crop_upper(rgba, item["view_type"])
        crop_path = crop_dir / f"{item['source_id']}.png"
        crop.save(crop_path)
        full_value, full_raw, full_seconds = call(
            segment_session,
            args.ollama_url,
            args.model,
            source,
            args.timeout,
        )
        crop_value, crop_raw, crop_seconds = call(
            segment_session,
            args.ollama_url,
            args.model,
            crop_path,
            args.timeout,
        )
        gold = item["labels"]["pattern_category"]
        rows.append(
            {
                "id": item["source_id"],
                "gold": gold,
                "full": full_value,
                "crop": crop_value,
                "full_correct": full_value == gold,
                "crop_correct": crop_value == gold,
                "full_seconds": round(full_seconds, 2),
                "crop_seconds": round(crop_seconds, 2),
                "full_raw": full_raw,
                "crop_raw": crop_raw,
                "crop_file": str(crop_path.relative_to(out_dir)),
            }
        )
        print(
            f"  gold={gold} full={full_value} crop={crop_value}",
            flush=True,
        )

    full_correct = sum(row["full_correct"] for row in rows)
    crop_correct = sum(row["crop_correct"] for row in rows)
    summary = {
        "model": args.model,
        "total": len(rows),
        "full_correct": full_correct,
        "full_accuracy": full_correct / len(rows) if rows else None,
        "crop_correct": crop_correct,
        "crop_accuracy": crop_correct / len(rows) if rows else None,
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# 花纹裁剪对比：{args.model}",
        "",
        f"- 原图：{full_correct}/{len(rows)}",
        f"- 上装裁切：{crop_correct}/{len(rows)}",
        "",
        "| 图片 | 金标准 | 原图 | 原图正确 | 裁切 | 裁切正确 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['gold']} | {row['full']} | "
            f"{'是' if row['full_correct'] else '否'} | {row['crop']} | "
            f"{'是' if row['crop_correct'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "裁切规则：先用本地 U2Net 去背景，再按挂拍/上身类型截取上装区域。真实图只有 15 张，单张变化约 6.7 个百分点。",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out_dir/'report.md'}")


if __name__ == "__main__":
    main()

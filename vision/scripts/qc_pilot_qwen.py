#!/usr/bin/env python3
"""Independently inspect synthetic pilot labels with the local Qwen2.5-VL model."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("simple_probe", SCRIPTS / "simple_probe.py")
simple_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simple_probe)

FIELDS = ["collar_family", "front_closure", "length"]
FIELD_NAMES = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "length": "衣长",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "vision/data/pilot_mixed_20/manifest.json"),
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "vision/results/qc_qwen2.5vl_7b_mixed20"),
    )
    parser.add_argument("--model", default="qwen2.5vl:7b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=40)
    parser.add_argument("--keep-alive", default="10m")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    details = []
    for index, item in enumerate(manifest["images"], 1):
        path = manifest_path.parent / item["file"]
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        predictions = {}
        print(f"[{index}/{len(manifest['images'])}] {item['id']} 质检中…", flush=True)
        for field in FIELDS:
            gold = item["labels"][field]
            if gold == "看不出":
                predictions[field] = {
                    "gold": gold,
                    "pred": "跳过",
                    "match": None,
                    "raw": "",
                }
                continue
            answer = simple_probe.ask_text(
                args.ollama_url,
                args.model,
                simple_probe.PROMPTS[field][0],
                image_b64,
                args,
            )
            parsed = simple_probe.parse_enum(field, answer)
            predictions[field] = {
                "gold": gold,
                "pred": parsed,
                "match": parsed == gold,
                "raw": answer,
            }
        details.append(
            {
                "id": item["id"],
                "file": item["file"],
                "view_type": item.get("view_type", ""),
                "predictions": predictions,
            }
        )
        simple_probe.eval_lib.write_json(raw_dir / f"{item['id']}.json", details[-1])

    counts = {
        field: {
            "checked": sum(
                1
                for item in details
                if item["predictions"][field]["match"] is not None
            ),
            "match": sum(
                1
                for item in details
                if item["predictions"][field]["match"] is True
            ),
            "mismatch": sum(
                1
                for item in details
                if item["predictions"][field]["match"] is False
            ),
        }
        for field in FIELDS
    }
    summary = {
        "meta": {
            "model": args.model,
            "manifest": str(manifest_path.resolve()),
            "usage": "independent local QC only; Qwen predictions do not replace prompt labels",
        },
        "counts": counts,
        "details": details,
    }
    simple_probe.eval_lib.write_json(out_dir / "summary.json", summary)

    lines = [
        "# 生成图标签质检",
        "",
        "用本地 `qwen2.5vl:7b` 独立看 20 张合成图，与生成提示词标签对比。"
        "这只能发现明显冲突，不能把 Qwen 的答案当作新金标准。",
        "",
        "| 字段 | 一致 | 不一致 |",
        "|---|---:|---:|",
    ]
    for field in FIELDS:
        stat = counts[field]
        lines.append(
            f"| {FIELD_NAMES[field]} | {stat['match']}/{stat['checked']} | "
            f"{stat['mismatch']} |"
        )
    lines += [
        "",
        "## 不一致明细",
        "",
        "| 图片 | 视图 | 字段 | 提示词标签 | Qwen |",
        "|---|---|---|---|---|",
    ]
    for item in details:
        for field in FIELDS:
            prediction = item["predictions"][field]
            if prediction["match"] is False:
                lines.append(
                    f"| `{item['id']}` | {item['view_type']} | {FIELD_NAMES[field]} | "
                    f"{prediction['gold']} | {prediction['pred']} |"
                )
    lines += [
        "",
        "## 处理规则",
        "",
        "- 两边一致的标签可以保留。",
        "- 不一致的图片先人工看，不直接改标签。",
        "- 无法确认或明显画错的图片应从训练集剔除，不能把模型答案硬改成标签。",
    ]
    (out_dir / "report.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )
    print(f"完成：{out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

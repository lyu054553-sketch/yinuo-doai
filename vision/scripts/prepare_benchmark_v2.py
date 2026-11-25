#!/usr/bin/env python3
"""Build the frozen 50-image provisional benchmark from confirmed and pilot labels."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "vision/data/benchmark_v2/labels.json"

PATTERN_BY_ID = {
    "c1_garment": "暗纹",
    "c1_model": "暗纹",
    "c2_model": "暗纹",
    "c2_garment": "暗纹",
    "c3_jacket_garment": "暗纹",
    "c3_dress_garment": "无",
    "c4_garment": "暗纹",
    "c4_model": "暗纹",
    "c5_model": "暗纹",
    "c5_garment": "暗纹",
    "c6_model": "暗纹",
    "c6_garment": "暗纹",
    "c6_wf_design_board": "暗纹",
    "c6_wf_flat_and_cad": "暗纹",
    "c6_wf_sample": "无",
}

COLOR_BY_ID = {
    "c1_garment": "深灰",
    "c1_model": "深灰",
    "c2_model": "黑",
    "c2_garment": "黑",
    "c3_jacket_garment": "浅灰",
    "c3_dress_garment": "粉",
    "c4_garment": "棕",
    "c4_model": "棕",
    "c5_model": "浅灰",
    "c5_garment": "浅灰",
    "c6_model": "黑",
    "c6_garment": "黑",
    "c6_wf_design_board": "黑",
    "c6_wf_flat_and_cad": "黑",
    "c6_wf_sample": "白",
}

SCHEMA = {
    "collar_family": ["立领", "交领", "翻领", "斜襟", "圆领", "看不出"],
    "front_closure": ["外露扣", "无扣开襟", "交叠闭合", "系带", "看不出"],
    "buttons_visible": ["可见", "未见", "看不出"],
    "length": ["到腰", "臀部附近", "过臀", "看不出"],
    "silhouette": ["H 型", "收腰", "A 摆", "看不出"],
    "pattern_category": ["无", "暗纹", "明显图案", "看不出"],
    "primary_color": ["黑", "深灰", "中灰", "浅灰", "白", "米白", "藏青", "棕", "粉", "其他", "看不出"],
}

ALL_FIELDS = list(SCHEMA)
CORE_FIELDS = ["collar_family", "front_closure", "length"]
PILOT_EXCLUDE = {"p03", "p12", "p16", "p13", "p15"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def map_buttons(value):
    if value is True:
        return "可见"
    if value is False:
        return "未见"
    return "看不出"


def main():
    gold = load(ROOT / "vision/results/gold_labels.json")
    pilot = load(ROOT / "vision/data/pilot_20/manifest.json")
    mixed = load(ROOT / "vision/data/pilot_mixed_20/manifest.json")

    images = []
    for item in gold["images"]:
        labels = dict(item["labels"])
        labels["buttons_visible"] = map_buttons(labels["buttons_visible"])
        labels["pattern_category"] = PATTERN_BY_ID[item["id"]]
        labels["primary_color"] = COLOR_BY_ID[item["id"]]
        labels.pop("pattern", None)
        labels.pop("colors", None)
        images.append(
            {
                "id": "real__" + item["id"],
                "source_id": item["id"],
                "file": "data/" + item["file"],
                "tier": "real_gold",
                "view_type": item["kind"],
                "label_status": "human_confirmed",
                "evaluable_fields": ALL_FIELDS,
                "cluster": item["case"],
                "labels": labels,
            }
        )

    for manifest, base_dir, prefix in (
        (mixed, "vision/data/pilot_mixed_20/", "synthetic_mixed"),
        (pilot, "vision/data/pilot_20/", "synthetic_pilot"),
    ):
        for item in manifest["images"]:
            if prefix == "synthetic_pilot" and item["id"] in PILOT_EXCLUDE:
                continue
            labels = dict(item["labels"])
            images.append(
                {
                    "id": prefix + "__" + item["id"],
                    "source_id": item["id"],
                    "file": base_dir + item["file"],
                    "tier": "synthetic_prompt",
                    "view_type": item.get("view_type", "全身"),
                    "label_status": "prompt_match_unreviewed",
                    "evaluable_fields": CORE_FIELDS,
                    "cluster": item["id"],
                    "labels": labels,
                }
            )

    assert len(images) == 50, len(images)
    assert sum(image["tier"] == "real_gold" for image in images) == 15

    payload = {
        "meta": {
            "version": "2.0.0",
            "frozen_on": "2026-11-17",
            "purpose": (
                "同一批图片的前后回归比较；不是独立盲测集。"
                "上服 15 张真实图只做测试，不进入训练或 few-shot。"
            ),
            "schema": SCHEMA,
            "tiers": {
                "real_gold": "15 张上服真实图，人工确认标签",
                "synthetic_prompt": "35 张已有合成图，标签来自生成规格，未逐张人工复核",
            },
            "pilot_exclude": sorted(PILOT_EXCLUDE),
            "limitations": [
                "合成图仅三字段有标签。",
                "所有图片在之前阶段已被查看，因此不是独立盲测。",
                "真实图只有 15 张，单张错误会改变约 6.7 个百分点。",
                "15 张真实图来自 6 个案例，版型和颜色分布不均衡。",
            ],
        },
        "images": images,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {OUT}")
    print(f"共 {len(images)} 张：真实 {sum(i['tier'] == 'real_gold' for i in images)}，合成 {sum(i['tier'] == 'synthetic_prompt' for i in images)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Combine the best local zero-shot source for each clothing field."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FASHION = ROOT / "vision/results/fashionsiglip_zeroshot_all5_v2/summary.json"
QWEN7 = ROOT / "vision/results/simple_probe_qwen2.5vl_7b_v1/summary.json"
QWEN3 = ROOT / "vision/results/simple_probe_qwen2.5vl_3b/summary.json"
OUT = ROOT / "vision/results/hybrid_local"

FIELD_NAMES = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "length": "衣长",
    "buttons_visible": "纽扣可见",
    "silhouette": "廓形",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    fashion = read(FASHION)
    qwen7 = read(QWEN7)
    qwen3 = read(QWEN3)

    fashion_by_id = {item["id"]: item for item in fashion["details"]}
    qwen7_by_id = {item["id"]: item for item in qwen7["details"]}
    qwen3_by_id = {item["id"]: item for item in qwen3["details"]}

    details = []
    for image_id, source in fashion_by_id.items():
        q7 = qwen7_by_id[image_id]
        q3 = qwen3_by_id[image_id]
        predictions = {
            "collar_family": fashion_by_id[image_id]["predictions"]["collar_family"],
            "front_closure": fashion_by_id[image_id]["predictions"]["front_closure"],
            "length": fashion_by_id[image_id]["predictions"]["length"],
            "buttons_visible": {
                "pred": q7["fields"]["buttons_visible"]["pred"],
                "confidence": q7["fields"]["buttons_visible"]["confidence"],
            },
            "silhouette": {
                "pred": q3["fields"]["silhouette"]["pred"],
                "confidence": q3["fields"]["silhouette"]["confidence"],
            },
        }
        gold = {
            field: q7["fields"][field]["gold"]
            for field in FIELD_NAMES
        }
        details.append(
            {
                "id": image_id,
                "gold": gold,
                "predictions": predictions,
            }
        )

    fields = {}
    for field in FIELD_NAMES:
        known = [item for item in details if item["gold"][field] != "看不出"]
        unknown = [item for item in details if item["gold"][field] == "看不出"]
        wrong = [
            item["id"]
            for item in known
            if item["predictions"][field]["pred"] != item["gold"][field]
        ]
        fields[field] = {
            "known_correct": len(known) - len(wrong),
            "known_total": len(known),
            "known_accuracy": (
                round((len(known) - len(wrong)) / len(known), 4) if known else None
            ),
            "wrong_ids": wrong,
            "unknown_total": len(unknown),
            "unknown_answered_confidently": sum(
                item["predictions"][field]["pred"] != "看不出" for item in unknown
            ),
        }

    total_correct = sum(item["known_correct"] for item in fields.values())
    total_known = sum(item["known_total"] for item in fields.values())
    result = {
        "meta": {
            "collar_family": "Marqo FashionSigLIP zero-shot",
            "front_closure": "Marqo FashionSigLIP zero-shot",
            "length": "Marqo FashionSigLIP zero-shot",
            "buttons_visible": "Qwen2.5-VL 7B local prompt",
            "silhouette": "Qwen2.5-VL 3B local prompt, 3-vote majority",
            "usage": "local only; real Shangfu images used for evaluation only",
            "limitation": "model choice per field was informed by the same 15-image test set",
        },
        "total_known_correct": total_correct,
        "total_known": total_known,
        "total_known_accuracy": round(total_correct / total_known, 4),
        "fields": fields,
        "details": details,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 本地混合模型测试",
        "",
        "**一句话结论：** 不训练、不花钱，组合本机三个开源模型后，"
        f"五个字段在真实图已知标签上达到 {total_correct}/{total_known}"
        f"（{total_correct / total_known:.1%}）。",
        "",
        "| 字段 | 来源 | 准确 | 错误图片 |",
        "|---|---|---:|---|",
    ]
    for field in FIELD_NAMES:
        item = fields[field]
        source = result["meta"][field]
        wrong = "、".join(f"`{value}`" for value in item["wrong_ids"]) or "—"
        lines.append(
            f"| {FIELD_NAMES[field]} | {source} | "
            f"{item['known_correct']}/{item['known_total']} | {wrong} |"
        )
    lines += [
        "",
        "## 关键限制",
        "",
        "- 三个模型的字段分工参考了这 15 张图，因此不能当作独立泛化结果。",
        "- 衣长仍有标注口径问题；单件挂拍没有人体参照时，“臀部附近”和“过臀”不稳定。",
        "- 金标准为“看不出”的闭合方式和衣长，三个模型都不会主动弃权；"
        "产品必须用人工确认或额外规则兜底。",
        "- 这不是微调模型。对外只能写“本地开源视觉模型组合”，不能写“微调 Qwen2.5-VL”或“自研大模型”。",
        "",
        "## 可用的产品定位",
        "",
        "- 作为“候选属性提取器”：一次性给出 5 个字段，但全部标为“疑似”，由用户确认。",
        "- 不把任何字段标成“确定”，就不会出现“确定但错误”。",
        "- 若必须自动出“确定”答案，需要更多独立真实样本，而不是继续加提示词。",
    ]
    (OUT / "report.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )
    print(OUT / "report.md")


if __name__ == "__main__":
    main()

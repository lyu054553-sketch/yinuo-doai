#!/usr/bin/env python3
"""Run local, zero-shot clothing attribute probes with Marqo FashionSigLIP.

No training and no external API. The 15 Shangfu images are evaluation-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TRAIN_SCRIPT = Path(__file__).resolve().parent / "train_clip_probe.py"
spec = importlib.util.spec_from_file_location("clip_probe", TRAIN_SCRIPT)
clip_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clip_probe)

FIELDS = [
    "collar_family",
    "front_closure",
    "buttons_visible",
    "length",
    "silhouette",
]
FIELD_NAMES = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "buttons_visible": "纽扣可见",
    "length": "衣长",
    "silhouette": "廓形",
}

UNKNOWN = "看不出"
VIEW_BY_FIELD = {
    "collar_family": "upper",
    "front_closure": "torso",
    "buttons_visible": "torso",
    "length": "full",
    "silhouette": "full",
}

PROMPTS = {
    "collar_family": {
        "立领": [
            "a garment with a tall narrow standing mandarin collar that encircles the neck",
            "a stand-up collar jacket with a vertical front neck opening and no folded lapels",
        ],
        "交领": [
            "a robe-style garment with a crossed collar, two diagonal neckline edges overlapping at the chest",
            "a crossed wrap collar where the right front panel overlaps the left at the chest",
        ],
        "翻领": [
            "a tailored jacket with notched lapels folded outward like a blazer",
            "a suit collar with visible flat folded collar points",
        ],
        "斜襟": [
            "an asymmetric Chinese jacket with a diagonal neckline and diagonal front closure",
            "a garment with an off-center diagonal collar edge running across the chest",
        ],
        "圆领": [
            "a garment with a round crew neckline and no collar",
            "a collarless round neck top",
        ],
        UNKNOWN: [
            "the garment collar is cropped, hidden, folded, or impossible to determine",
            "the neckline is not clearly visible in this image",
        ],
    },
    "front_closure": {
        "外露扣": [
            "a garment front with a row of clearly visible buttons",
            "a buttoned front placket with visible round buttons",
        ],
        "无扣开襟": [
            "a garment with a completely open front and no buttons or ties",
            "an open-front jacket with visible front edges",
        ],
        "交叠闭合": [
            "a garment with overlapping front panels closed by hidden fasteners",
            "a wrap front with no visible buttons or external ties",
        ],
        "系带": [
            "a garment front closed by fabric ties or a tied sash bow",
            "a belted wrap jacket with a visible knot and hanging tie ends",
        ],
        UNKNOWN: [
            "the front closure is hidden by crossed arms, cropped, or impossible to determine",
            "no buttons, opening, overlap, or ties can be clearly determined from this view",
        ],
    },
    "buttons_visible": {
        "有": [
            "a garment front with clearly visible buttons",
            "a jacket with exposed round buttons on its front placket",
        ],
        "无": [
            "a garment front with no visible buttons",
            "an unbuttoned or buttonless jacket front",
        ],
        UNKNOWN: [
            "the front buttons are hidden, cropped, or impossible to determine",
            "the front of the garment is not visible enough to tell whether it has buttons",
        ],
    },
    "length": {
        "到腰": [
            "a cropped jacket whose hem ends at the natural waist",
            "a very short jacket ending at waist level",
        ],
        "臀部附近": [
            "a hip-length jacket whose hem ends at the hip bone",
            "a jacket ending around the hips rather than at the waist",
        ],
        "过臀": [
            "a coat whose hem extends below the hips to the upper thigh",
            "a long jacket ending about fifteen centimeters below the hip line",
        ],
        UNKNOWN: [
            "the full garment is cropped or the hem is outside the frame so its length cannot be determined",
            "only the upper body is shown, so the garment length relative to the body is unknown",
        ],
    },
    "silhouette": {
        "H 型": [
            "a garment with a straight columnar H-shaped silhouette",
            "a straight-cut garment with no defined waist and no flared hem",
        ],
        "收腰": [
            "a garment with a clearly fitted and cinched waist",
            "a tailored garment with a narrow defined waist",
        ],
        "A 摆": [
            "a garment with a fitted waist and a flared A-line hem",
            "a coat or dress with an A-shaped silhouette widening toward the hem",
        ],
        UNKNOWN: [
            "the full garment silhouette is hidden or cropped",
            "the waist and hem cannot both be seen, so the silhouette is unclear",
        ],
    },
}


def build_classifier(encoder, field, allow_abstain):
    classes = [
        value
        for value in PROMPTS[field]
        if allow_abstain or value != UNKNOWN
    ]
    prompts = []
    prompt_classes = []
    for label in classes:
        for prompt in PROMPTS[field][label]:
            prompts.append(prompt)
            prompt_classes.append(label)
    prompt_features = encoder.encode_texts(prompts)
    class_features = np.stack(
        [
            prompt_features[
                [index for index, label in enumerate(prompt_classes) if label == value]
            ].mean(axis=0)
            for value in classes
        ]
    )
    class_features /= np.maximum(
        np.linalg.norm(class_features, axis=1, keepdims=True), 1e-8
    )
    return classes, class_features


def predict(encoder, field, image, allow_abstain):
    classes, class_features = build_classifier(encoder, field, allow_abstain)
    view = VIEW_BY_FIELD[field]
    crop = clip_probe.crop_view(image, view)
    embedding = encoder.encode_images([crop])[0]
    logits = class_features @ embedding
    probabilities = np.exp(logits * 100.0 - np.max(logits * 100.0))
    probabilities /= probabilities.sum()
    index = int(probabilities.argmax())
    ordered = np.argsort(probabilities)[::-1]
    return {
        "pred": classes[index],
        "confidence": round(float(probabilities[index]), 4),
        "margin": round(float(probabilities[ordered[0]] - probabilities[ordered[1]]), 4),
        "probabilities": {
            value: round(float(probabilities[i]), 4) for i, value in enumerate(classes)
        },
    }


def normalize_gold(field, value):
    if field == "buttons_visible":
        if value is True:
            return "有"
        if value is False:
            return "无"
        return UNKNOWN
    return str(value)


def summarize(details):
    fields = {}
    for field in FIELDS:
        known = [item for item in details if item["gold"][field] != UNKNOWN]
        unknown = [item for item in details if item["gold"][field] == UNKNOWN]
        correct = sum(
            item["predictions"][field]["pred"] == item["gold"][field] for item in known
        )
        answered = sum(
            item["predictions"][field]["pred"] != UNKNOWN for item in known
        )
        certain_wrong = sum(
            item["predictions"][field]["pred"] not in {UNKNOWN, item["gold"][field]}
            for item in known
        )
        unknown_correct = sum(
            item["predictions"][field]["pred"] == UNKNOWN for item in unknown
        )
        all_correct = sum(
            item["predictions"][field]["pred"] == item["gold"][field]
            for item in details
        )
        fields[field] = {
            "known_correct": correct,
            "known_total": len(known),
            "known_accuracy": round(correct / len(known), 4) if known else None,
            "answered_known": answered,
            "certain_wrong": certain_wrong,
            "unknown_correct": unknown_correct,
            "unknown_total": len(unknown),
            "all_correct": all_correct,
            "all_total": len(details),
            "prediction_counts": dict(
                Counter(item["predictions"][field]["pred"] for item in details)
            ),
        }
    return fields


def write_report(result, path: Path):
    lines = [
        "# FashionSigLIP 本地零样本测试",
        "",
        "**结论：** 使用 `Marqo/marqo-fashionSigLIP` 在本机做零样本服装属性判断，"
        "不训练、不上传图片、不调用外部 API。",
        "",
        f"- 模型：`{result['meta']['model']}`",
        f"- 设备：`{result['meta']['device']}`",
        "- 测试：上服 15 张真实图，仅用于测试。",
        (
            "- 文本类别包含“看不出”，用于评估模型能否主动弃权。"
            if result["meta"]["allow_abstain"]
            else "- 默认只分类具体属性，不包含“看不出”；未识别项由产品层置信度阈值兜底。"
        ),
        "",
        "## 总体",
        "",
        "| 字段 | 已知标签准确 | 明显误答 | 金标准“看不出”时答对 | 含“看不出”的全量准确 |",
        "|---|---:|---:|---:|---:|",
    ]
    for field in FIELDS:
        data = result["fields"][field]
        lines.append(
            f"| {FIELD_NAMES[field]} | "
            f"{data['known_correct']}/{data['known_total']}"
            f"（{data['known_accuracy']:.1%}） | "
            f"{data['certain_wrong']} | "
            f"{data['unknown_correct']}/{data['unknown_total']} | "
            f"{data['all_correct']}/{data['all_total']} |"
        )

    for field in FIELDS:
        lines += [
            "",
            f"## {FIELD_NAMES[field]}逐图",
            "",
            "| 图片 | 金标准 | 预测 | 结果 | 最高概率 | 概率差 |",
            "|---|---|---|---:|---:|---:|",
        ]
        for item in result["details"]:
            prediction = item["predictions"][field]
            gold = item["gold"][field]
            mark = "✅" if prediction["pred"] == gold else "❌"
            if gold == UNKNOWN and prediction["pred"] != UNKNOWN:
                mark = "应弃权"
            lines.append(
                f"| `{item['id']}` | {gold} | {prediction['pred']} | {mark} | "
                f"{prediction['confidence']:.2f} | {prediction['margin']:.2f} |"
            )

    lines += [
        "",
        "## 解释与边界",
        "",
        "- 这是零样本文本分类，不是训练出来的分类器。",
        "- 置信度是余弦相似度经固定温度转成的概率，尚未做真实数据校准。",
        "- 测试集只有 15 张，1 张的变化约为 7 个百分点。",
        "- 上服 15 张真实图没有参与任何训练或提示词示例。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels", default=str(ROOT / "vision/results/gold_labels.json")
    )
    parser.add_argument("--images-root", default=str(ROOT / "data"))
    parser.add_argument(
        "--out-dir", default=str(ROOT / "vision/results/fashionsiglip_zeroshot")
    )
    parser.add_argument(
        "--model", default="hf-hub:Marqo/marqo-fashionSigLIP"
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--allow-abstain", action="store_true")
    args = parser.parse_args()

    items, images = clip_probe.load_real(
        Path(args.labels), Path(args.images_root)
    )
    encoder = clip_probe.ClipEncoder(args.model, "", args.device)
    details = []
    for item, image in zip(items, images):
        predictions = {
            field: predict(encoder, field, image, args.allow_abstain)
            for field in FIELDS
        }
        details.append(
            {
                "id": item["id"],
                "file": item["file"],
                "gold": {
                    field: normalize_gold(field, item["labels"][field])
                    for field in FIELDS
                },
                "predictions": predictions,
            }
        )
        print(f"[{item['id']}] 完成", flush=True)

    result = {
        "meta": {
            "model": args.model,
            "device": encoder.device,
            "allow_abstain": args.allow_abstain,
            "labels": str(Path(args.labels).resolve()),
            "images_root": str(Path(args.images_root).resolve()),
            "usage": "real Shangfu images used for evaluation only",
        },
        "fields": summarize(details),
        "details": details,
    }
    out = Path(args.out_dir)
    clip_probe.write_json(out / "summary.json", result)
    write_report(result, out / "report.md")
    print(f"\n完成：{out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

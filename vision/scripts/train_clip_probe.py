#!/usr/bin/env python3
"""Train and evaluate lightweight CLIP attribute probes on a small pilot set.

The 20 synthetic images are pilot data, not a production training set. The
15 Shangfu images under data/images are used for evaluation only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
import torch
from PIL import Image, ImageEnhance
from sklearn.linear_model import LogisticRegression


ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["collar_family", "front_closure", "length"]
FIELD_NAMES = {
    "collar_family": "领型",
    "front_closure": "闭合方式",
    "length": "衣长",
}
VIEW_BY_FIELD = {
    "collar_family": "upper",
    "front_closure": "torso",
    "length": "full",
}

TEXT_PROMPTS = {
    "collar_family": {
        "立领": [
            "a garment with a tall narrow standing mandarin collar",
            "a stand collar jacket without lapels",
        ],
        "交领": [
            "a garment with a crossed V-neck wrap collar, right side over left",
            "a crossed crossover collar with one front panel overlapping the other",
        ],
        "翻领": [
            "a tailored jacket with notched lapels folded outward",
            "a blazer collar with visible folded lapels",
        ],
        "斜襟": [
            "a garment with an asymmetric diagonal front opening and diagonal neckline",
            "an asymmetrical Chinese jacket with a diagonal front closure",
        ],
        "圆领": [
            "a garment with a round crew neckline and no collar",
            "a collarless round neck jacket",
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
    },
    "length": {
        "到腰": [
            "a short jacket whose hem ends at the natural waist",
            "a cropped jacket ending exactly at the waist",
        ],
        "臀部附近": [
            "a jacket whose hem ends at the hip bone level",
            "a hip-length jacket ending around the hips",
        ],
        "过臀": [
            "a coat whose hem extends below the hips to the upper thigh",
            "a long jacket ending about fifteen centimeters below the hip line",
        ],
    },
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def crop_view(image: Image.Image, mode: str) -> Image.Image:
    width, height = image.size
    boxes = {
        "full": (0.0, 0.0, 1.0, 1.0),
        "upper": (0.04, 0.01, 0.96, 0.61),
        "torso": (0.11, 0.08, 0.89, 0.68),
    }
    left, top, right, bottom = boxes[mode]
    return image.crop(
        (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
    )


def augment_view(image: Image.Image):
    yield image
    yield image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    yield ImageEnhance.Brightness(image).enhance(0.9)
    yield ImageEnhance.Brightness(image).enhance(1.1)


class ClipEncoder:
    def __init__(self, model_name: str, pretrained: str, device: str):
        import open_clip

        if device == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        self.device = device
        self.model_name = model_name
        self.pretrained = pretrained
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained or None, device=device
        )
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.model.eval()

    def encode_images(self, images: list[Image.Image], batch_size: int = 16):
        tensors = [self.preprocess(image) for image in images]
        chunks = []
        with torch.inference_mode():
            for start in range(0, len(tensors), batch_size):
                batch = torch.stack(tensors[start : start + batch_size]).to(self.device)
                features = self.model.encode_image(batch)
                features = features.float().cpu().numpy()
                chunks.append(features)
        features = np.concatenate(chunks, axis=0).astype(np.float32)
        features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-8)
        return features

    def encode_texts(self, texts: list[str]):
        tokens = self.tokenizer(texts).to(self.device)
        with torch.inference_mode():
            features = self.model.encode_text(tokens).float().cpu().numpy()
        features = features.astype(np.float32)
        features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-8)
        return features


def load_pilot(manifest_path: Path):
    manifest = read_json(manifest_path)
    base = manifest_path.parent
    images = []
    for item in manifest["images"]:
        path = base / item["file"]
        if not path.exists():
            raise FileNotFoundError(path)
        with Image.open(path) as opened:
            images.append(opened.convert("RGB").copy())
    return manifest["images"], images


def load_real(labels_path: Path, images_root: Path):
    labels = read_json(labels_path)
    items = labels["images"]
    images = []
    for item in items:
        path = images_root / item["file"]
        if not path.exists():
            raise FileNotFoundError(path)
        with Image.open(path) as opened:
            images.append(opened.convert("RGB").copy())
    return items, images


def group_embeddings(images, encoder, view, augment):
    views = []
    groups = []
    for image in images:
        base = crop_view(image, view)
        variants = list(augment_view(base)) if augment else [base]
        group = []
        for variant in variants:
            group.append(len(views))
            views.append(variant)
        groups.append(group)
    embeddings = encoder.encode_images(views)
    return embeddings, groups


def fit_model(x, y, c):
    model = LogisticRegression(
        C=c,
        max_iter=3000,
        class_weight="balanced",
        solver="lbfgs",
        random_state=17,
    )
    model.fit(x, y)
    return model


def grouped_leave_one_out(x, y, groups, c_values):
    results = {}
    for c in c_values:
        predictions = []
        truth = []
        for held_out in range(len(groups)):
            train_indices = [
                index
                for group_index, group in enumerate(groups)
                if group_index != held_out
                for index in group
            ]
            model = fit_model(x[train_indices], y[train_indices], c)
            prediction = model.predict(x[[groups[held_out][0]]])[0]
            predictions.append(str(prediction))
            truth.append(str(y[groups[held_out][0]]))
        results[c] = {
            "accuracy": float(np.mean(np.array(predictions) == np.array(truth))),
            "correct": int(np.sum(np.array(predictions) == np.array(truth))),
            "total": len(truth),
        }
    best_c = max(c_values, key=lambda c: (results[c]["accuracy"], -c))
    return best_c, results


def build_text_classifier(encoder, field):
    classes = list(TEXT_PROMPTS[field])
    prompts = []
    prompt_classes = []
    for label in classes:
        for prompt in TEXT_PROMPTS[field][label]:
            prompts.append(prompt)
            prompt_classes.append(label)
    prompt_features = encoder.encode_texts(prompts)
    class_features = np.stack(
        [
            prompt_features[[i for i, value in enumerate(prompt_classes) if value == label]]
            .mean(axis=0)
            for label in classes
        ]
    )
    class_features /= np.maximum(
        np.linalg.norm(class_features, axis=1, keepdims=True), 1e-8
    )
    return classes, class_features


def predict_text(features, classes, class_features):
    logits = features @ class_features.T
    probabilities = np.exp(logits * 100.0 - np.max(logits * 100.0, axis=1, keepdims=True))
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    indices = probabilities.argmax(axis=1)
    return [classes[i] for i in indices], probabilities


def evaluate_predictions(items, predictions, probabilities):
    details = []
    correct = 0
    scored = 0
    for item, prediction, probs in zip(items, predictions, probabilities):
        truth = item["labels"][item["field"]]
        unknown = truth == "看不出"
        is_correct = None if unknown else prediction == truth
        if not unknown:
            scored += 1
            correct += int(is_correct)
        details.append(
            {
                "id": item["id"],
                "gold": truth,
                "pred": prediction,
                "correct": is_correct,
                "confidence": round(float(np.max(probs)), 4),
            }
        )
    return {
        "correct": correct,
        "scored": scored,
        "accuracy": round(correct / scored, 4) if scored else None,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "vision/data/pilot_20/manifest.json"),
    )
    parser.add_argument(
        "--real-labels",
        default=str(ROOT / "vision/results/gold_labels.json"),
    )
    parser.add_argument("--real-images-root", default=str(ROOT / "data"))
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "vision/results/clip_probe_pilot20"),
    )
    parser.add_argument("--model", default="ViT-B-16")
    parser.add_argument("--pretrained", default="laion2b_s34b_b88k")
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--view-override",
        choices=["", "full", "upper", "torso"],
        default="",
        help="强制所有字段使用同一个裁剪视图，用于消融实验。",
    )
    parser.add_argument("--c-values", default="0.01,0.03,0.1,0.3,1.0")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--skip-real", action="store_true")
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    manifest_items, pilot_images = load_pilot(Path(args.manifest))
    encoder = ClipEncoder(args.model, args.pretrained, args.device)
    out_dir = Path(args.out_dir)
    model_dir = out_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    c_values = [float(value) for value in args.c_values.split(",") if value.strip()]

    result = {
        "meta": {
            "model": args.model,
            "pretrained": args.pretrained,
            "device": encoder.device,
            "view_override": args.view_override or None,
            "pilot_manifest": str(Path(args.manifest).resolve()),
            "pilot_images": len(manifest_items),
            "pilot_usage": "synthetic pilot only; not a production training set",
            "real_usage": "15 Shangfu images used for evaluation only, never training",
        },
        "fields": {},
    }

    for field in FIELDS:
        print(f"[{FIELD_NAMES[field]}] 提取合成图特征…", flush=True)
        view = args.view_override or VIEW_BY_FIELD[field]
        known_indices = [
            index
            for index, item in enumerate(manifest_items)
            if str(item["labels"].get(field, "看不出")) != "看不出"
        ]
        field_items = [manifest_items[index] for index in known_indices]
        field_images = [pilot_images[index] for index in known_indices]
        pilot_labels = [str(item["labels"][field]) for item in field_items]
        x_pilot, groups = group_embeddings(
            field_images, encoder, view=view, augment=True
        )
        y_pilot = np.array(
            [
                label
                for label, group in zip(pilot_labels, groups)
                for _ in group
            ]
        )

        print(f"[{FIELD_NAMES[field]}] 留一图像交叉验证…", flush=True)
        best_c, c_results = grouped_leave_one_out(
            x_pilot, y_pilot, groups, c_values
        )
        model = fit_model(x_pilot, y_pilot, best_c)
        joblib.dump(
            {
                "model": model,
                "field": field,
                "view": view,
                "clip_model": args.model,
                "pretrained": args.pretrained,
            },
            model_dir / f"{field}.joblib",
        )

        zero_shot_predictions = []
        zero_shot_truth = []
        classes, class_features = build_text_classifier(encoder, field)
        for group, label in zip(groups, pilot_labels):
            prediction, _ = predict_text(
                x_pilot[[group[0]]], classes, class_features
            )
            zero_shot_predictions.append(prediction[0])
            zero_shot_truth.append(label)
        zero_shot_correct = int(
            np.sum(np.array(zero_shot_predictions) == np.array(zero_shot_truth))
        )

        field_result = {
            "view": view,
            "class_counts": dict(Counter(pilot_labels)),
            "selected_c": best_c,
            "cv_by_c": {str(c): metrics for c, metrics in c_results.items()},
            "pilot_loocv": c_results[best_c],
            "pilot_zero_shot": {
                "correct": zero_shot_correct,
                "total": len(pilot_labels),
                "accuracy": round(zero_shot_correct / len(pilot_labels), 4),
            },
            "real_test": None,
            "real_test_zero_shot": None,
        }

        if not args.skip_real:
            print(f"[{FIELD_NAMES[field]}] 在 15 张真实图上测试…", flush=True)
            real_items, real_images = load_real(
                Path(args.real_labels), Path(args.real_images_root)
            )
            x_real, real_groups = group_embeddings(
                real_images, encoder, view=view, augment=False
            )
            real_predictions = model.predict(
                x_real[[group[0] for group in real_groups]]
            )
            real_probabilities = model.predict_proba(
                x_real[[group[0] for group in real_groups]]
            )
            real_items_for_field = [
                {**item, "field": field} for item in real_items
            ]
            field_result["real_test"] = evaluate_predictions(
                real_items_for_field,
                [str(value) for value in real_predictions],
                real_probabilities,
            )

            zero_predictions, zero_probabilities = predict_text(
                x_real[[group[0] for group in real_groups]],
                classes,
                class_features,
            )
            field_result["real_test_zero_shot"] = evaluate_predictions(
                real_items_for_field, zero_predictions, zero_probabilities
            )

        result["fields"][field] = field_result
        print(
            f"  合成图留一准确率 "
            f"{field_result['pilot_loocv']['correct']}/{field_result['pilot_loocv']['total']}"
            f"；真实图已知标签准确率 "
            f"{(field_result.get('real_test') or {}).get('correct', '—')}"
            f"/{(field_result.get('real_test') or {}).get('scored', '—')}",
            flush=True,
        )

    write_json(out_dir / "summary.json", result)
    write_report(result, out_dir / "report.md")
    print(f"\n完成：{out_dir}", flush=True)
    return 0


def write_report(result, path: Path):
    lines = [
        "# CLIP 小试跑报告",
        "",
        "**结论：** 这是 20 张合成图上的可行性小试跑，不是正式训练结果。"
        "只有三项指标都明显高于随机猜测、且真实图错误可人工兜底时，才值得扩到 100–200 张。",
        "",
        f"- 底座：`{result['meta']['model']}` / `{result['meta']['pretrained']}`",
        f"- 设备：`{result['meta']['device']}`",
        "- 训练：20 张受控合成图；上服 15 张真实图仅用于测试，未参与训练。",
        "- 合成图标签尚未全部人工确认，衣长和部分斜襟/交领图片存在标签风险。",
        "",
        "## 结果",
        "",
        "| 字段 | 合成图留一准确率 | 真实图准确率（排除“看不出”） | 真实图零样本准确率 |",
        "|---|---:|---:|---:|",
    ]
    for field in FIELDS:
        data = result["fields"][field]
        loocv = data["pilot_loocv"]
        real = data.get("real_test") or {}
        zero = data.get("real_test_zero_shot") or {}
        real_text = (
            f"{real.get('correct', '—')}/{real.get('scored', '—')}"
            if real
            else "未运行"
        )
        zero_text = (
            f"{zero.get('correct', '—')}/{zero.get('scored', '—')}"
            if zero
            else "未运行"
        )
        lines.append(
            f"| {FIELD_NAMES[field]} | "
            f"{loocv['correct']}/{loocv['total']}（{loocv['accuracy']:.1%}） | "
            f"{real_text} | {zero_text} |"
        )

    lines += ["", "## 真实图逐张结果", ""]
    for field in FIELDS:
        data = result["fields"][field]
        real = data.get("real_test")
        if not real:
            continue
        lines += [
            f"### {FIELD_NAMES[field]}",
            "",
            "| 图片 | 金标准 | 预测 | 结果 | 置信度 |",
            "|---|---|---|---:|---:|",
        ]
        for item in real["details"]:
            if item["correct"] is None:
                mark = "未计分"
            else:
                mark = "✅" if item["correct"] else "❌"
            lines.append(
                f"| `{item['id']}` | {item['gold']} | {item['pred']} | "
                f"{mark} | {item['confidence']:.2f} |"
            )
        lines.append("")

    lines += [
        "## 解释与边界",
        "",
        "- 合成图留一准确率只说明模型能否分开这 20 张图，不能代表真实服装泛化。",
        "- 真实图只有 15 张，且案例五此前在调试中接触过，因此这些数字只能作为工程参考。",
        "- 真实图中标为“看不出”的样本没有计入准确率分母；产品里需要用置信度阈值显示“需人工确认”。",
        "- 若真实图准确率不够，下一步应修正合成图和标签，而不是继续堆提示词。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        raise SystemExit(130)

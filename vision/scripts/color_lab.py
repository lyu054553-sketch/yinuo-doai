#!/usr/bin/env python3
"""Estimate the dominant upper-garment colour from a segmentation plus Lab matching."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

from segment import create_session, foreground_with_session


ROOT = Path(__file__).resolve().parents[2]

PALETTE_RGB = {
    "黑": (22, 22, 24),
    "深灰": (61, 61, 64),
    "中灰": (112, 112, 114),
    "浅灰": (177, 177, 178),
    "白": (244, 244, 242),
    "米白": (224, 215, 192),
    "藏青": (28, 43, 70),
    "棕": (94, 65, 48),
    "粉": (219, 145, 161),
}


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32) / 255.0
    rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float32,
    )
    rgb_flat = rgb.reshape(-1, 3)
    xyz = rgb_flat @ matrix.T
    white = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    xyz = xyz / white
    delta = 6 / 29

    def f(t):
        return np.where(t > delta**3, np.cbrt(t), t / (3 * delta**2) + 4 / 29)

    fx, fy, fz = f(xyz[:, 0]), f(xyz[:, 1]), f(xyz[:, 2])
    lab = np.column_stack(
        [
            116 * fy - 16,
            500 * (fx - fy),
            200 * (fy - fz),
        ]
    )
    return lab.reshape(rgb.shape[:-1] + (3,))


PALETTE_LAB = {name: srgb_to_lab(np.array([[rgb]], dtype=np.uint8))[0, 0] for name, rgb in PALETTE_RGB.items()}


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return (y > 55) & (cb >= 76) & (cb <= 132) & (cr >= 132) & (cr <= 178)


def dominant_lab(rgb: np.ndarray, mask: np.ndarray):
    pixels = rgb[mask]
    if len(pixels) < 100:
        raise ValueError("前景像素不足")
    if len(pixels) > 30_000:
        step = max(1, len(pixels) // 30_000)
        pixels = pixels[::step]
    k = min(5, max(2, len(pixels) // 100))
    model = KMeans(n_clusters=k, random_state=0, n_init=4)
    clusters = model.fit_predict(pixels)
    counts = Counter(clusters.tolist())
    center = model.cluster_centers_[counts.most_common(1)[0][0]]
    lab = srgb_to_lab(np.clip(center, 0, 255).astype(np.uint8).reshape(1, 1, 3))[0, 0]
    return lab.astype(float), counts.most_common(1)[0][0], len(pixels)


def nearest_palette(lab: np.ndarray):
    distances = {
        name: float(np.linalg.norm(lab - palette))
        for name, palette in PALETTE_LAB.items()
    }
    ordered = sorted(distances.items(), key=lambda item: item[1])
    return ordered[0][0], ordered[0][1], ordered[1][1]


def crop_upper(rgba: Image.Image, view_type: str):
    alpha = np.array(rgba.getchannel("A"))
    ys, xs = np.where(alpha > 24)
    if not len(xs):
        raise ValueError("没有分割出主体")
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    width, height = x1 - x0, y1 - y0
    is_person = any(word in view_type for word in ("上身", "全身"))
    if is_person:
        left = x0 + int(width * 0.08)
        right = x0 + int(width * 0.92)
        top = y0 + int(height * 0.10)
        bottom = y0 + int(height * 0.64)
    else:
        left, right, top, bottom = x0, x1, y0, y1
    return rgba.crop((left, top, right, bottom)), is_person


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        default=str(ROOT / "vision/data/benchmark_v2/labels.json"),
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        raise SystemExit("缺少 onnxruntime，请先安装到 .venv-vision。") from exc

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    images = [image for image in labels["images"] if image["tier"] == "real_gold"]
    if args.limit:
        images = images[: args.limit]
    out_dir = Path(args.out_dir)
    crop_dir = out_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    session = create_session()
    rows = []
    for index, item in enumerate(images, start=1):
        source = ROOT / item["file"]
        print(f"[{index}/{len(images)}] {item['source_id']}", flush=True)
        rgb_image = Image.open(source).convert("RGB")
        rgba = foreground_with_session(rgb_image, session)
        crop, is_person = crop_upper(rgba, item["view_type"])
        rgb = np.array(crop.convert("RGB"))
        alpha = np.array(crop.getchannel("A"))
        mask = alpha > 96
        mask &= ~skin_mask(rgb)
        lab, cluster_id, used = dominant_lab(rgb, mask)
        predicted, best_distance, second_distance = nearest_palette(lab)
        crop_path = crop_dir / f"{item['source_id']}.png"
        crop.save(crop_path)
        row = {
            "id": item["source_id"],
            "tier": item["tier"],
            "view_type": item["view_type"],
            "gold": item["labels"]["primary_color"],
            "prediction": predicted,
            "correct": predicted == item["labels"]["primary_color"],
            "lab": [round(value, 2) for value in lab.tolist()],
            "cluster_id": cluster_id,
            "pixels_used": used,
            "best_distance": round(best_distance, 2),
            "second_distance": round(second_distance, 2),
            "is_person_crop": is_person,
            "crop": str(crop_path.relative_to(out_dir)),
        }
        rows.append(row)
        print(
            f"  gold={row['gold']} pred={predicted} "
            f"correct={row['correct']} lab={row['lab']}",
            flush=True,
        )

    simple = [row for row in rows if not row["view_type"].startswith("工作流")]
    summary = {
        "total": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "accuracy": sum(row["correct"] for row in rows) / len(rows) if rows else None,
        "photo_only_total": len(simple),
        "photo_only_correct": sum(row["correct"] for row in simple),
        "photo_only_accuracy": (
            sum(row["correct"] for row in simple) / len(simple) if simple else None
        ),
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Lab 主色算法",
        "",
        f"- 全部真实图：{summary['correct']}/{summary['total']}",
        f"- 排除工作流拼图：{summary['photo_only_correct']}/{summary['photo_only_total']}",
        "",
        "| 图片 | 类型 | 金标准 | 预测 | 正确 | Lab |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['view_type']} | {row['gold']} | "
            f"{row['prediction']} | {'是' if row['correct'] else '否'} | "
            f"{row['lab']} |"
        )
    lines.extend(
        [
            "",
            "说明：颜色金标准本身是人工目测的粗粒度标签，部分图片存在撞色和光线影响；该结果只用于比较算法，不代表色卡级测量。",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out_dir/'report.md'}")


if __name__ == "__main__":
    main()

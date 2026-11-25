#!/usr/bin/env python3
"""Run the free local clothing-attribute pipeline on one image.

The output is deliberately conservative: every non-unknown field is marked
"疑似" because the current validation set is too small to justify "确定".
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clip_probe = load_module("clip_probe", SCRIPTS / "train_clip_probe.py")
fashionclip = load_module(
    "fashionclip_zeroshot", SCRIPTS / "fashionclip_zeroshot.py"
)
simple_probe = load_module("simple_probe", SCRIPTS / "simple_probe.py")


def ask_qwen(
    image_b64: str,
    model: str,
    prompt: str,
    ollama_url: str,
    timeout: int,
):
    args = argparse.Namespace(
        timeout=timeout,
        num_ctx=8192,
        num_predict=40,
        keep_alive="10m",
    )
    return simple_probe.ask_text(ollama_url, model, prompt, image_b64, args)


def conservative_field(value, source: str, evidence=None):
    if value == "看不出":
        confidence = "看不出"
    else:
        confidence = "疑似"
    result = {
        "value": value,
        "confidence": confidence,
        "source": source,
    }
    if evidence is not None:
        result["evidence"] = evidence
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--debug-out",
        default="",
        help="把模型来源和概率证据另存为调试 JSON。",
    )
    parser.add_argument(
        "--siglip-model", default="hf-hub:Marqo/marqo-fashionSigLIP"
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--qwen7", default="qwen2.5vl:7b")
    parser.add_argument("--qwen3", default="qwen2.5vl:3b")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    with Image.open(image_path) as opened:
        image = opened.convert("RGB").copy()
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    print("加载本地 FashionSigLIP…", flush=True)
    encoder = clip_probe.ClipEncoder(args.siglip_model, "", args.device)
    fields = {}
    sources = {
        "collar_family": "FashionSigLIP zero-shot",
        "front_closure": "FashionSigLIP zero-shot",
        "length": "FashionSigLIP zero-shot",
    }
    for field in ("collar_family", "front_closure", "length"):
        print(f"判断 {field}…", flush=True)
        prediction = fashionclip.predict(encoder, field, image, False)
        fields[field] = conservative_field(
            prediction["pred"],
            sources[field],
            {
                "probability": prediction["confidence"],
                "margin": prediction["margin"],
            },
        )

    print("用 Qwen 7B 判断纽扣…", flush=True)
    button_raw = ask_qwen(
        image_b64,
        args.qwen7,
        simple_probe.PROMPTS["buttons_visible"][0],
        args.ollama_url,
        args.timeout,
    )
    button_value = simple_probe.parse_enum("buttons_visible", button_raw)
    fields["buttons_visible"] = conservative_field(
        button_value, "Qwen2.5-VL 7B local", {"raw": button_raw}
    )

    print("用 Qwen 3B 三次投票判断廓形…", flush=True)
    silhouette_votes = []
    silhouette_raw = []
    for prompt in simple_probe.PROMPTS["silhouette"]:
        raw = ask_qwen(
            image_b64,
            args.qwen3,
            prompt,
            args.ollama_url,
            args.timeout,
        )
        silhouette_raw.append(raw)
        silhouette_votes.append(simple_probe.parse_enum("silhouette", raw))
    silhouette_value, _, count = simple_probe.majority(silhouette_votes)
    fields["silhouette"] = conservative_field(
        silhouette_value,
        "Qwen2.5-VL 3B local, 3-vote majority",
        {"votes": silhouette_votes, "agreement": count},
    )

    print("用 Qwen 7B 提取纹样和颜色…", flush=True)
    pattern_raw = ask_qwen(
        image_b64,
        args.qwen7,
        "看图片，这件上服的纹样是什么？用不超过 12 个字描述；"
        "如果看不出，只回答：看不出。不要解释。",
        args.ollama_url,
        args.timeout,
    )
    color_raw = ask_qwen(
        image_b64,
        args.qwen7,
        "看图片，这件上服的主色是什么？只回答 1 到 3 个中文颜色词，"
        "用顿号分隔；如果看不出，只回答：看不出。不要解释。",
        args.ollama_url,
        args.timeout,
    )
    fields["pattern"] = conservative_field(
        simple_probe.parse_pattern(pattern_raw),
        "Qwen2.5-VL 7B local",
        {"raw": pattern_raw},
    )
    fields["colors"] = conservative_field(
        simple_probe.parse_colors(color_raw),
        "Qwen2.5-VL 7B local",
        {"raw": color_raw},
    )

    strict = {
        field: {
            "value": item["value"],
            "confidence": item["confidence"],
        }
        for field, item in fields.items()
    }
    diagnostics = {
        "meta": {
            "image": str(image_path.resolve()),
            "mode": "local_hybrid_zero_shot",
            "model_policy": "no upload, no external API",
            "certainty_policy": (
                "所有非“看不出”字段一律标为“疑似”；"
                "当前 15 张测试图不足以支持“确定”。"
            ),
        },
        "fields": fields,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(strict, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n写入：{out}", flush=True)
    if args.debug_out:
        debug_out = Path(args.debug_out)
        debug_out.parent.mkdir(parents=True, exist_ok=True)
        debug_out.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"调试证据：{debug_out}", flush=True)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

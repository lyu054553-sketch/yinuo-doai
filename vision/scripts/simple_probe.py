#!/usr/bin/env python3
"""用逐字段简单提问探测本地视觉模型的能力。

每个枚举字段用 3 个同义问法各问一次，取多数答案；
把握程度由投票一致性推导，而不是让模型自报：
- 3 票一致且不是“看不出” -> 确定
- 2 票一致 -> 疑似
- 没有多数 -> 看不出

这个脚本只做诊断，不训练，不上传图片。
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

spec = importlib.util.spec_from_file_location("eval_lib", HERE / "eval.py")
eval_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_lib)


PROMPTS = {
    "collar_family": [
        "看图片，这件上服的领型是什么？只回答一个词：立领、交领、翻领、斜襟、圆领或看不出。不要解释。",
        "请判断图片中主体上服的领口类型。只回答：立领、交领、翻领、斜襟、圆领或看不出。不要解释。",
        "这件衣服的领子是哪种？只回答：立领、交领、翻领、斜襟、圆领或看不出。不要解释。",
    ],
    "front_closure": [
        "看图片，这件上服的前襟是怎么闭合的？只回答一个词：外露扣、无扣开襟、交叠闭合、系带或看不出。不要解释。",
        "请判断图片中主体上服的前襟闭合方式。只回答：外露扣、无扣开襟、交叠闭合、系带或看不出。不要解释。",
        "这件衣服的前襟如何合上？只回答：外露扣、无扣开襟、交叠闭合、系带或看不出。不要解释。",
    ],
    "buttons_visible": [
        "看图片，这件上服的正面能看到纽扣吗？只回答：有、无或看不出。不要解释。",
        "请判断图片中主体上服正面是否有可见纽扣。只回答：有、无或看不出。不要解释。",
        "这件衣服正面有扣子吗？只回答：有、无或看不出。不要解释。",
    ],
    "length": [
        "看图片，这件上服的下摆到哪里？只回答一个词：到腰、臀部附近、过臀或看不出。不要解释。",
        "请判断图片中主体上服的下摆位置。只回答：到腰、臀部附近、过臀或看不出。不要解释。",
        "这件衣服的长度到身体哪里？只回答：到腰、臀部附近、过臀或看不出。不要解释。",
    ],
    "silhouette": [
        "看图片，这件上服的廓形是什么？只回答一个词：H型、收腰、A摆或看不出。不要解释。",
        "请判断图片中主体上服的轮廓形状。只回答：H型、收腰、A摆或看不出。不要解释。",
        "这件衣服整体是直筒、收腰还是下摆放宽？只回答：H型、收腰、A摆或看不出。不要解释。",
    ],
}


SYNONYMS = {
    "小立领": "立领",
    "中式立领": "立领",
    "青果领": "翻领",
    "西装领": "翻领",
    "圆领口": "圆领",
    "无扣": "无扣开襟",
    "对襟": "无扣开襟",
    "交叠": "交叠闭合",
    "系带闭合": "系带",
    "绑带": "系带",
    "及臀": "臀部附近",
    "臀中": "臀部附近",
    "过臀部": "过臀",
    "齐腰": "到腰",
    "直筒": "H 型",
    "H型": "H 型",
    "收腰型": "收腰",
    "A字": "A 摆",
    "A字形": "A 摆",
    "A摆": "A 摆",
}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def clean_text(text: str) -> str:
    text = (text or "").strip().split("\n")[0].strip()
    text = re.sub(r"^(回答|答案|输出)[：:]\s*", "", text)
    return text.strip("。.,，;；:：\"'“”‘’ ")


def parse_enum(field: str, text: str):
    cleaned = clean_text(text)
    if field == "buttons_visible":
        if "看不出" in cleaned or "看不清" in cleaned:
            return "看不出"
        if any(word in cleaned for word in ("没有", "无", "否", "不可见", "没看到")):
            return False
        if any(word in cleaned for word in ("有", "是", "可见", "看到")):
            return True
        return "看不出"
    allowed = {
        "collar_family": ["立领", "交领", "翻领", "斜襟", "圆领"],
        "front_closure": ["外露扣", "无扣开襟", "交叠闭合", "系带"],
        "length": ["到腰", "臀部附近", "过臀"],
        "silhouette": ["H 型", "收腰", "A 摆"],
    }[field]
    if "看不出" in cleaned or "看不清" in cleaned:
        return "看不出"
    for value in allowed:
        if value.replace(" ", "") in cleaned.replace(" ", ""):
            return value
    for alias, value in SYNONYMS.items():
        if alias.replace(" ", "") in cleaned.replace(" ", ""):
            if value in allowed:
                return value
    tokens = re.split(r"[\s,，、/|；;]+", cleaned)
    for token in tokens:
        canonical = eval_lib.canonical_value(field, token)
        if canonical in allowed:
            return canonical
    return "看不出"


def majority(values: list):
    normalized = [v for v in values if v is not None]
    if not normalized:
        return "看不出", "看不出", 0
    if len(normalized) == 1:
        value = normalized[0]
        if value == "看不出":
            return "看不出", "看不出", 1
        return value, "确定", 1
    counts = Counter(normalized)
    value, count = counts.most_common(1)[0]
    if value == "看不出":
        return "看不出", "看不出", count
    if count == len(normalized) and len(normalized) >= 2:
        return value, "确定", count
    if count >= 2 and len(normalized) >= 2:
        return value, "疑似", count
    return "看不出", "看不出", count


def ask_text(base_url: str, model: str, prompt: str, image_b64: str, args):
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
        "keep_alive": args.keep_alive,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return (payload.get("message") or {}).get("content", "")


def parse_pattern(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned or "看不出" in cleaned:
        return "看不出"
    return cleaned[:80]


def parse_colors(text: str):
    cleaned = clean_text(text)
    if not cleaned or "看不出" in cleaned:
        return ["看不出"]
    parts = [p.strip() for p in re.split(r"[、,，/|；;和]", cleaned) if p.strip()]
    colors = []
    for part in parts:
        part = part[:20]
        if part and part not in colors:
            colors.append(part)
        if len(colors) >= 3:
            break
    return colors or ["看不出"]


def probe_one(item: dict, args):
    image_path = Path(args.images_root) / item["file"]
    if not image_path.exists():
        return {
            "id": item["id"],
            "file": item["file"],
            "model": args.model,
            "gold": item.get("labels"),
            "parsed": None,
            "validation_errors": [],
            "error": f"图片不存在：{image_path}",
            "raw_answers": {},
            "votes": {},
            "elapsed_s": 0.0,
        }
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    parsed = {}
    raw_answers = {}
    votes = {}
    try:
        for field, prompts in PROMPTS.items():
            answers = []
            parsed_votes = []
            for prompt in prompts[: args.votes]:
                text = ask_text(args.ollama_url, args.model, prompt, image_b64, args)
                answers.append(text)
                parsed_votes.append(parse_enum(field, text))
            value, confidence, count = majority(parsed_votes)
            parsed[field] = {"value": value, "confidence": confidence}
            raw_answers[field] = answers
            votes[field] = {
                "values": parsed_votes,
                "majority": value,
                "count": count,
                "agreement": round(count / max(1, len(parsed_votes)), 2),
            }
        pattern_raw = ask_text(
            args.ollama_url,
            args.model,
            "看图片，这件上服的纹样是什么？用不超过 12 个字描述；如果看不出，只回答：看不出。不要解释。",
            image_b64,
            args,
        )
        colors_raw = ask_text(
            args.ollama_url,
            args.model,
            "看图片，这件上服的主色是什么？只回答 1 到 3 个中文颜色词，用顿号分隔；如果看不出，只回答：看不出。不要解释。",
            image_b64,
            args,
        )
        parsed["pattern"] = {
            "value": parse_pattern(pattern_raw),
            "confidence": "确定" if parse_pattern(pattern_raw) != "看不出" else "看不出",
        }
        parsed["colors"] = {
            "value": parse_colors(colors_raw),
            "confidence": "确定" if parse_colors(colors_raw) != ["看不出"] else "看不出",
        }
        raw_answers["pattern"] = [pattern_raw]
        raw_answers["colors"] = [colors_raw]
    except Exception as exc:
        return {
            "id": item["id"],
            "file": item["file"],
            "model": args.model,
            "gold": item.get("labels"),
            "parsed": None,
            "validation_errors": [],
            "error": str(exc),
            "raw_answers": raw_answers,
            "votes": votes,
            "elapsed_s": 0.0,
        }
    validation_errors = eval_lib.validate_deconstruct(
        parsed, eval_lib.read_json(ROOT / "vision/schema/deconstruct.schema.json")
    )
    return {
        "id": item["id"],
        "file": item["file"],
        "model": args.model,
        "gold": item.get("labels"),
        "parsed": parsed,
        "validation_errors": validation_errors,
        "error": None,
        "raw_answers": raw_answers,
        "votes": votes,
        "elapsed_s": None,
    }


def render_report(summary: dict, records: list[dict], out_dir: Path) -> str:
    field_labels = {
        "collar_family": "领型",
        "front_closure": "闭合方式",
        "buttons_visible": "纽扣可见",
        "length": "衣长",
        "silhouette": "廓形",
    }
    lines = [
        "# 逐字段简单提问探测",
        "",
        f"**模型：** `{summary['model']}`  ",
        f"**测试集：** {summary['n_images']} 张上服真实图（只测试，未训练）  ",
        "**方法：** 每个枚举字段用 3 个同义问法各问一次，取多数答案；",
        "把握程度由投票一致性推导（3 票一致=确定，2 票=疑似，无多数=看不出）。",
        "",
        "## 一句话结论",
        "",
        (
            f"五个枚举字段准确率 {eval_lib.pct(summary['enum_correct'], summary['enum_total'])}，"
            f"“确定但错误”率 {eval_lib.pct(summary['certain_wrong'], summary['certain_total'])}。"
        ),
        "",
        "## 总体指标",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 格式合法 | {summary['format_valid']} / {summary['n_images']}（{eval_lib.pct(summary['format_valid'], summary['n_images'])}） |",
        f"| 枚举字段准确 | {summary['enum_correct']} / {summary['enum_total']}（{eval_lib.pct(summary['enum_correct'], summary['enum_total'])}） |",
        f"| 标为“确定” | {summary['certain_total']} |",
        f"| 确定但错误 | {summary['certain_wrong']}（{eval_lib.pct(summary['certain_wrong'], summary['certain_total'])}） |",
        f"| 金标准“看不出”时答“看不出” | {summary['unseen_gold_answered_unseen']} / {summary['unseen_gold_total']}（{eval_lib.pct(summary['unseen_gold_answered_unseen'], summary['unseen_gold_total'])}） |",
        "",
        "## 分字段",
        "",
        "| 字段 | 准确 | 确定 | 确定但错 | 三票一致率 |",
        "|---|---|---|---|---|",
    ]
    for field in eval_lib.ENUM_FIELDS:
        stats = summary["fields"].get(field, {})
        agreements = [
            r["votes"][field]["agreement"]
            for r in records
            if r.get("votes") and field in r["votes"]
        ]
        avg_agreement = sum(agreements) / len(agreements) if agreements else 0
        lines.append(
            f"| {field_labels[field]} | {stats.get('correct',0)}/{stats.get('total',0)}"
            f"（{eval_lib.pct(stats.get('correct',0), stats.get('total',0))}）"
            f" | {stats.get('certain_total',0)} | {stats.get('certain_wrong',0)}"
            f"（{eval_lib.pct(stats.get('certain_wrong',0), stats.get('certain_total',0))}）"
            f" | {avg_agreement:.0%} |"
        )
    lines += [
        "",
        "## 逐图结果",
        "",
        "| 图片 | 领型 | 闭合 | 纽扣 | 衣长 | 廓形 | 错误字段 |",
        "|---|---|---|---|---|---|---|",
    ]
    for detail in summary["details"]:
        cells = []
        for field in eval_lib.ENUM_FIELDS:
            item = detail["fields"][field]
            if item["pred"] is None:
                cells.append("—")
            else:
                mark = "✅" if item["correct"] else "❌"
                cells.append(f"{mark} {item['pred']}（{item['confidence'] or '?'}）")
        wrong = "、".join(field_labels[f] for f in detail["wrong_fields"]) or "—"
        lines.append(f"| `{detail['id']}` | " + " | ".join(cells) + f" | {wrong} |")
    lines += [
        "",
        "## 局限",
        "",
        "- 测试集只有 15 张图；单个字段错 1 张就变化约 6.7 个百分点。",
        "- 把握程度是投票一致性推导的，不是模型原生的置信度。",
        "- 这是量化模型，结果不能代表未量化模型或微调后的模型。",
        "",
        f"原始逐图输出：`{out_dir / 'raw'}`  ",
        f"机器可读汇总：`{out_dir / 'summary.json'}`",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default=str(ROOT / "vision/results/gold_labels.json"))
    parser.add_argument("--images-root", default=str(ROOT / "data"))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--model", default="qwen2.5vl:3b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--votes", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=40)
    parser.add_argument("--keep-alive", default="10m")
    args = parser.parse_args()
    if args.votes < 1:
        args.votes = 1
    if args.votes > 3:
        args.votes = 3
    gold = eval_lib.read_json(Path(args.labels))
    items = gold["images"]
    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        items = [item for item in items if item["id"] in wanted]
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    if not items:
        print("没有选中任何图片。", file=sys.stderr)
        return 2
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else ROOT / "vision/results" / f"simple_probe_{slug(args.model)}"
    )
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index, item in enumerate(items, 1):
        raw_path = raw_dir / f"{item['id']}.json"
        if args.resume and raw_path.exists():
            existing = eval_lib.read_json(raw_path)
            if existing.get("parsed") is not None:
                print(f"[{index}/{len(items)}] {item['id']} 已有结果，跳过", flush=True)
                records.append(existing)
                continue
        print(f"[{index}/{len(items)}] {item['id']} 逐字段提问中…", flush=True)
        record = probe_one(item, args)
        eval_lib.write_json(raw_path, record)
        records.append(record)
        if record["error"]:
            print(f"  失败：{record['error']}", flush=True)
        else:
            print(
                f"  完成，格式错误 {len(record['validation_errors'])} 项",
                flush=True,
            )
    summary = eval_lib.summarize_deconstruct(records, args.model)
    summary["prompt_mode"] = "simple_vote"
    summary["votes_per_field"] = args.votes
    eval_lib.write_json(out_dir / "summary.json", summary)
    eval_lib.write_text(out_dir / "report.md", render_report(summary, records, out_dir))
    print(
        f"\n完成：枚举准确 {summary['enum_correct']}/{summary['enum_total']}，"
        f"确定但错 {summary['certain_wrong']}/{summary['certain_total']}",
        flush=True,
    )
    print(f"报告：{out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

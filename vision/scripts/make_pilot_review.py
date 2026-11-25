#!/usr/bin/env python3
"""Build a local contact sheet for the 20-image CLIP pilot."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "vision/data/pilot_20/manifest.json"),
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    manifest_path = Path(args.manifest)
    output = (
        Path(args.output) if args.output else manifest_path.parent / "review.html"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["images"]
    counts = {
        field: Counter(item["labels"][field] for item in items)
        for field in ("collar_family", "front_closure", "length")
    }
    cards = []
    for item in items:
        labels = item["labels"]
        risk = html.escape(item.get("risk", "") or "无")
        status = html.escape(item["label_review"])
        cards.append(
            f"""
            <article class="card">
              <a href="{html.escape(item['file'])}" target="_blank">
                <img src="{html.escape(item['file'])}" alt="{html.escape(item['id'])}">
              </a>
              <div class="body">
                <h2>{html.escape(item['id'])}</h2>
                <dl>
                  <div><dt>领型</dt><dd>{html.escape(labels['collar_family'])}</dd></div>
                  <div><dt>闭合</dt><dd>{html.escape(labels['front_closure'])}</dd></div>
                  <div><dt>衣长</dt><dd>{html.escape(labels['length'])}</dd></div>
                </dl>
                <p class="status">{status}</p>
                <p class="risk"><strong>风险：</strong>{risk}</p>
              </div>
            </article>
            """
        )
    count_html = "".join(
        f"<li><strong>{html.escape(field)}</strong>："
        + "，".join(
            f"{html.escape(label)} {count}" for label, count in sorted(counter.items())
        )
        + "</li>"
        for field, counter in counts.items()
    )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CLIP 20 张小试跑复核</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
    body {{ margin: 0; background: #f4f1ea; color: #1f211d; }}
    header {{ padding: 32px clamp(18px, 4vw, 64px); background: #1f211d; color: #f4f1ea; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(24px, 4vw, 42px); }}
    header p {{ max-width: 820px; line-height: 1.7; color: #d8d2c4; }}
    .counts {{ display: flex; flex-wrap: wrap; gap: 14px; padding: 0; list-style: none; }}
    main {{ padding: 28px clamp(14px, 3vw, 48px) 64px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 18px; }}
    .card {{ background: #fffdf8; border: 1px solid #d8d1c1; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 24px #2b291c12; }}
    img {{ display: block; width: 100%; aspect-ratio: 2 / 3; object-fit: cover; background: #ddd7ca; }}
    .body {{ padding: 16px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    dl {{ margin: 0; }}
    dl div {{ display: flex; justify-content: space-between; gap: 16px; padding: 6px 0; border-bottom: 1px dashed #ddd5c5; }}
    dt {{ color: #6d6a61; }}
    dd {{ margin: 0; font-weight: 650; }}
    .status {{ color: #705223; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .risk {{ margin: 12px 0 0; color: #5c5a52; font-size: 13px; line-height: 1.55; }}
  </style>
</head>
<body>
  <header>
    <h1>CLIP 20 张小试跑复核</h1>
    <p>只用于验证合成图与标签是否可靠。标签目前来自受控提示词，尚未人工逐张确认；上服真实图不在这个目录里。</p>
    <ul class="counts">{count_html}</ul>
  </header>
  <main><section class="grid">{''.join(cards)}</section></main>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

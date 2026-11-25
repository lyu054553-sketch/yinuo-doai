"""生成或校验 C0 示例：用案例二、案例六的真实规格跑 build_prompts。

用法（在项目根目录）：
    python backend/app/media/make_c0_examples.py --check   # 只比对，不写文件
    python backend/app/media/make_c0_examples.py --write   # 重新写出 C0_EXAMPLES.json

不调用任何模型、生图或视觉服务；输出不含时间戳，可逐字复现。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent
BACKEND_DIR = MEDIA_DIR.parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.media import prompts as p  # noqa: E402

CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
OUT_PATH = MEDIA_DIR / "C0_EXAMPLES.json"
EXAMPLE_CASES = ("c2", "c6")

NOTE = (
    "C0 示例：由 make_c0_examples.py 用 data/cases.json 的案例二、案例六真实规格生成，"
    "不含时间戳；同一份方案重复生成结果逐字一致。"
)


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def build_examples() -> dict:
    examples = []
    for case_id in EXAMPLE_CASES:
        case = load_case(case_id)
        spec_version = {
            "version": 1,
            "created_at": "2026-11-18T09:00:00+08:00",
            "reason": "导入",
            "fields": {
                field: {
                    "value": case["spec"][field],
                    "resource_ids": [],
                    "utterance_ids": [],
                    "origin": "已有方案",
                    "confirmed": False,
                }
                for field in p.FIELD_ORDER
            },
        }
        examples.append({
            "case_id": case_id,
            "title": case["title"],
            "spec_version": spec_version,
            "result": p.build_prompts(spec_version),
        })
    return {
        "note": NOTE,
        "generator": "backend/app/media/make_c0_examples.py",
        "entry": "app.media.prompts.build_prompts",
        "cases": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="写出 C0_EXAMPLES.json")
    group.add_argument("--check", action="store_true", help="比对 C0_EXAMPLES.json 是否与当前代码一致")
    args = parser.parse_args()

    expected = build_examples()
    text = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"

    if args.write:
        OUT_PATH.write_text(text, encoding="utf-8")
        print(f"已写出 {OUT_PATH}")
        return 0

    if not OUT_PATH.exists():
        print(f"缺少 {OUT_PATH}，请先运行 --write")
        return 1
    actual = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    if actual == expected:
        print("一致：C0_EXAMPLES.json 与当前代码输出完全相同")
        return 0
    print("不一致：C0_EXAMPLES.json 与当前代码输出不同，请检查改动")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
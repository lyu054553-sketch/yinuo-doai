"""C2 真实效果图核对脚本（由总控在有视觉服务时执行）。

用法（在项目根目录）：

    # 案例六的成衣图，对照案例六方案：交领、无扣，应判为符合
    python backend/app/media/check_c2.py \
        --image data/images/c6_garment.jpeg --case c6

    # 案例一的成衣图（立领），对照案例六方案：领型应判为不符合
    python backend/app/media/check_c2.py \
        --image data/images/c1_garment.jpeg --case c6

先启动视觉服务（终端 A）：

    python vision/server_local.py --port 8001

脚本只读 `data/cases.json` 的**开发集**案例与本地图片，只调本机视觉服务；
不出图、不调大模型、不写会话文件。案例五是留出集，脚本直接拒绝（`split != "dev"`）。

输出 JSON：核对清单逐项（expect / observed / verdict / confidence）、模型标识、结论行。
视觉服务没启动时打印“视觉服务未启动…”并以退出码 2 结束；看图失败退出码 3。
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

from app.config import settings  # noqa: E402
from app.media import check, prompts, vision_client  # noqa: E402

CASES_PATH = PROJECT_ROOT / "data" / "cases.json"


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        if case["id"] == case_id:
            if case.get("split") != "dev":
                raise SystemExit(f"{case_id} 不是开发集案例（留出集不能用于开发与测试），已停止。")
            return case
    raise SystemExit(f"data/cases.json 里没有案例 {case_id}。")


def spec_version(case: dict, version: int = 1) -> dict:
    """按约定 SpecVersion 结构包一层，与后端核心存的形态一致。"""
    return {
        "version": version,
        "created_at": "2026-11-18T09:00:00+08:00",
        "reason": "导入",
        "fields": {
            field: {
                "value": prompts.field_text(case["spec"].get(field, "")),
                "resource_ids": [],
                "utterance_ids": [],
                "origin": "已有方案",
                "confirmed": False,
            }
            for field in prompts.FIELD_ORDER
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C2：效果图核对真实检查（先解构、再由程序比对）")
    parser.add_argument("--image", default="data/images/c6_garment.jpeg")
    parser.add_argument("--case", default="c6", help="对照哪个开发集案例的方案，如 c6")
    parser.add_argument("--version", type=int, default=1, help="方案版本号（默认 1）")
    parser.add_argument("--vision-url", default=None, help="默认读 VISION_URL（http://127.0.0.1:8001）")
    parser.add_argument("--timeout", type=float, default=320.0)
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = settings.project_root / image_path
    if not image_path.is_file():
        print(f"找不到图片：{image_path}", file=sys.stderr)
        return 2

    case = load_case(args.case)
    spec = spec_version(case, args.version)
    base = (args.vision_url or settings.vision_url).rstrip("/")

    try:
        data_url = vision_client.encode_image_file(image_path)
        result = check.check_image(data_url, spec, vision_url=base, timeout=args.timeout)
    except vision_client.VisionServiceUnavailable as exc:
        print(f"视觉服务未启动或连不上：{exc}", file=sys.stderr)
        return 2
    except vision_client.VisionError as exc:
        print(f"看图失败：{exc}", file=sys.stderr)
        return 3

    payload = {
        "image": (
            str(image_path.relative_to(settings.project_root))
            if settings.project_root in image_path.parents
            else str(image_path)
        ),
        "case": case["id"],
        "case_title": case["title"],
        "version": args.version,
        "vision_url": base,
        "check": result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    counted = {name: 0 for name in check.VERDICTS}
    for item in result["items"]:
        counted[item["verdict"]] += 1
    mismatched = [item for item in result["items"] if item["verdict"] == "不符合"]
    print(
        f"\n结论：符合 {counted['符合']} 项、不符合 {counted['不符合']} 项、看不出 {counted['看不出']} 项"
    )
    for item in mismatched:
        print(f"  不符合【{item['field']}】预期 {item['expect']}，看到 {item['observed']}")
    manual = "、".join(
        item["field"] for item in result["items"] if item["observed"] == "需人工确认"
    )
    if manual:
        print(f"  需人工确认：{manual}（衣长/廓形/颜色不采用模型结果）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""C2 真实视觉服务核对检查：只有视觉服务确实不可达时才 skip。

两条验收场景（由总控执行）：

- `data/images/c6_garment.jpeg` 对照案例六方案：交领、无扣，应判为符合；
- `data/images/c1_garment.jpeg`（立领）对照案例六方案：领型应判为不符合。

口径（C3 复核后收紧）：

- 程序不可达（端口连不上）才 skip，并写明原因；
- 模型“看错”属于真实观测，**不再 skip 掩盖**：先断言程序一致性（观测与结论必须一致），
  真实差距标 `xfail`（报告里显示 XFAIL，不算通过），冻结模型与标准答案一概不动；
- 已知未达标（见 WORKLOG）：冻结模型把 c6 的交领成衣图识别为立领，领型一项需人工复核。

运行方式同 C1：终端 A `python vision/server_local.py --port 8001`，终端 B `pytest backend/tests/media -q`。
也可以用脚本单跑：`python backend/app/media/check_c2.py --image ... --case c6`。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.config import settings
from app.media import check, prompts, vision_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"


def _vision_available() -> bool:
    parsed = urlparse(settings.vision_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _vision_available(),
    reason=f"视觉服务未启动（VISION_URL={settings.vision_url}），真实效果图核对跳过；"
    "先运行 python vision/server_local.py --port 8001 再跑本文件",
)


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def spec_version(case_id: str, version: int = 1) -> dict:
    case = load_case(case_id)
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


def by_field(result: dict) -> list[dict]:
    return result["items"]


def run(image_name: str, case_id: str) -> dict:
    image = settings.project_root / "data" / "images" / image_name
    assert image.is_file(), f"缺少图片 {image}"
    data_url = vision_client.encode_image_file(image)
    return check.check_image(data_url, spec_version(case_id), timeout=320.0)


def test_c6_image_against_c6_spec():
    result = run("c6_garment.jpeg", "c6")
    items = by_field(result)
    print("\n核对项：", [(i["field"], i["expect"], i["observed"], i["verdict"]) for i in items])

    # 冻结策略：衣长/廓形/颜色一律“看不出 + 需人工确认”（任何观测都不采用）
    for item in items[4:]:
        assert item["verdict"] == "看不出"
        assert item["observed"] == "需人工确认"

    collar, _, buttons, pattern, *_ = items

    # 方案无扣：模型看清了纽扣就必须判“符合”（真实结果：无外露纽扣 → 符合）
    assert buttons["observed"] in {"无外露纽扣", "看不出"}, buttons
    if buttons["observed"] == "无外露纽扣":
        assert buttons["verdict"] == "符合"
    else:
        assert buttons["verdict"] == "看不出"
        pytest.xfail(f"冻结模型在 c6 成衣图上没看清纽扣（观测：{buttons['observed']}），无法核对")

    # 交领：程序一致性先断言（观测≠交领就必须“不符合”，绝不算符合）
    if collar["observed"] == "交领":
        assert collar["verdict"] == "符合"
    else:
        assert collar["verdict"] == "不符合"
        pytest.xfail(
            "已知未达标：冻结模型把 c6 交领成衣图识别为"
            f"“{collar['observed']}”，领型一项需人工复核；模型与标准答案均未改动"
        )

    # 纹样：真实观测决定结论；看不出时只记差距，不硬判符合
    if pattern["observed"] == "可见纹样":
        assert pattern["verdict"] == "符合"
    elif pattern["observed"] == "看不出":
        assert pattern["verdict"] == "看不出"
        pytest.xfail(f"冻结模型没在 c6 成衣图上看出纹样（观测：{pattern['observed']}）")
    else:
        assert pattern["verdict"] == "不符合"
        pytest.xfail(f"冻结模型把 c6 暗纹判为“{pattern['observed']}”，纹样一项需人工复核")


def test_c1_image_against_c6_spec():
    result = run("c1_garment.jpeg", "c6")
    items = by_field(result)
    print("\n核对项：", [(i["field"], i["expect"], i["observed"], i["verdict"]) for i in items])

    collar, _, buttons, *_ = items

    # 领型：c1 是立领、方案要交领。观测≠交领就必须“不符合”，观测=交领同理不能算符合。
    if collar["observed"] == "交领":
        assert collar["verdict"] == "符合"
        pytest.xfail("冻结模型把 c1 立领图识别为交领，无法验证“领型不符合”")
    elif collar["observed"] == "看不出":
        assert collar["verdict"] == "看不出"
        pytest.xfail("冻结模型没看出 c1 图的领型，无法验证“领型不符合”")
    else:
        assert collar["verdict"] == "不符合"

    # 纽扣：方案无扣。看到外露纽扣必须判“不符合”，看不清只能“看不出”。
    if buttons["observed"] == "有外露纽扣":
        assert buttons["verdict"] == "不符合"
    elif buttons["observed"] == "看不出":
        assert buttons["verdict"] == "看不出"
        pytest.xfail("冻结模型没看清 c1 图的纽扣，无法验证“纽扣不符合”")
    else:  # 观测为“无外露纽扣”
        assert buttons["verdict"] == "符合"
        pytest.xfail("冻结模型在 c1 图上没看到外露纽扣，无法验证“纽扣不符合”")
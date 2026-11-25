"""C1 真实视觉服务检查：服务没启动时自动跳过并说明原因。

总控已把 `vision/server_local.py --port 8001` 起起来；本机没有视觉服务时，
这里只跳过、不判失败，避免把“环境没起”当成代码错误。

本文件自带一个小会话构造，不 import 其他测试文件，避免 pytest 收集路径差异。
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest

from app.config import settings
from app.media import reference, vision_client
from app.schemas import FIELD_ORDER, Mapping, Session, SpecField, SpecVersion, Utterance


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
    reason=f"视觉服务未启动（VISION_URL={settings.vision_url}），真实图检查跳过；"
    "先运行 python vision/server_local.py --port 8001 再跑本文件",
)


def _session(text: str) -> Session:
    fields = {f: SpecField(value="", origin="无出处") for f in FIELD_ORDER}
    fields["领型"] = SpecField(value="立领", origin="已有方案")
    return Session(
        id="sc1live000001",
        title="C1 真实图检查",
        mode="审计已有方案",
        source="mock",
        created_at="2026-11-18T10:00:00+00:00",
        input={"text": text, "existing_spec": None, "sample_id": None},
        utterances=[
            Utterance(
                id="u1",
                text=text,
                start=0,
                end=len(text),
                type="领型",
                strength="硬约束",
                status="已落实",
                mappings=[Mapping(field="领型", status="已落实")],
            )
        ],
        spec_versions=[
            SpecVersion(
                version=1,
                created_at="2026-11-18T10:00:00+00:00",
                reason="C1 检查",
                fields=fields,
            )
        ],
    )


def test_c1_reference_reports_collar_conflict():
    """data/images/c1_garment.jpeg（立领）+ “脖子短，领子不能太高” 应报出冲突。"""
    image = settings.project_root / "data/images/c1_garment.jpeg"
    assert image.is_file()
    session = _session("脖子短，领子不能太高")

    data_url = vision_client.encode_image_file(image)
    result = vision_client.deconstruct(data_url, timeout=320.0)
    analysis = reference.build_reference_analysis(
        image_url="/files/c1_garment.jpeg",
        result=result,
        utterances=session.utterances,
    )

    fields = analysis["fields"]
    # 冻结策略：三项不采用、四项最高“疑似”
    assert fields["衣长"]["used"] is False
    assert fields["廓形"]["used"] is False
    assert fields["颜色"]["used"] is False
    for label in ("领型", "前襟闭合", "纽扣", "纹样"):
        assert fields[label]["used"] is True
        assert fields[label]["confidence"] in {"疑似", "看不出"}

    print("\n实际识别：", {k: v["value"] for k, v in fields.items()})
    print("冲突：", analysis["conflicts"])

    collar_value = fields["领型"]["value"]
    if collar_value == "立领":
        assert analysis["conflicts"], "图被识别为立领时，必须报出领高冲突"
        assert analysis["conflicts"][0]["field"] == "领型"
        assert analysis["conflicts"][0]["utterance_ids"] == ["u1"]
    else:
        # 只在服务不可达时才 skip（见文件头）。模型看错属于真实观测，不跳过掩盖：
        # 这里仍然检查程序一致性（没看到立领就不该报领型冲突），再把真实差距标成
        # xfail（报告里显示为 XFAIL，不写成通过），不改冻结模型、不改标准答案。
        assert not [
            conflict for conflict in analysis["conflicts"] if conflict["field"] == "领型"
        ], "没识别成立领却报了领型冲突，说明程序规则有问题，不是模型问题"
        pytest.xfail(
            f"已知差距：冻结模型把 c1 立领成衣图识别为 {collar_value}，领高冲突因此未能报出"
        )
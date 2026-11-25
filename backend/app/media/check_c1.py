"""C1 真实图检查脚本（由总控在有视觉服务时执行）。

用法（在项目根目录）：

    python backend/app/media/check_c1.py \
        --image data/images/c1_garment.jpeg \
        --text "脖子短，领子不能太高"

先启动视觉服务：

    python vision/server_local.py --port 8001

脚本只读图片、只调本机视觉服务，不出图、不写会话文件。
输出：裁剪后的七字段（含 used）、冲突列表、台账条目，最后一行给出结论。
视觉服务没启动时打印中文错误并以退出码 2 结束。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.media import reference, vision_client  # noqa: E402
from app.schemas import Mapping, Session, SpecField, SpecVersion, Utterance  # noqa: E402
from app.schemas import FIELD_ORDER  # noqa: E402


def split_utterances(text: str) -> list[dict]:
    """按中文标点切句，位置直接在原文里查找；够本脚本演示用。"""
    segments: list[tuple[int, int, str]] = []
    start = 0
    for index, char in enumerate(text):
        if char in "，,。.；;":
            chunk = text[start:index]
            if chunk.strip():
                segments.append((start, index, chunk))
            start = index + 1
    tail = text[start:]
    if tail.strip():
        segments.append((start, len(text), tail))
    utterances = []
    for i, (s, e, chunk) in enumerate(segments, start=1):
        lead = len(chunk) - len(chunk.lstrip())
        utterances.append(
            {
                "id": f"u{i}",
                "text": chunk.strip(),
                "start": s + lead,
                "end": e,
                "type": "描述",
                "strength": "硬约束",
                "status": "需设计师判断",
                "mappings": [],
                "resolved_by": None,
            }
        )
    return utterances


def build_session(text: str) -> Session:
    fields = {f: SpecField(value="", origin="无出处") for f in FIELD_ORDER}
    return Session(
        id="sc1check00001",
        title="C1 参考图检查",
        mode="审计已有方案",
        source="mock",
        created_at="2026-11-18T00:00:00+00:00",
        input={"text": text, "existing_spec": None, "sample_id": None},
        utterances=[Utterance.model_validate(u) for u in split_utterances(text)],
        spec_versions=[
            SpecVersion(
                version=1,
                created_at="2026-11-18T00:00:00+00:00",
                reason="C1 检查",
                fields=fields,
            )
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="C1：参考图解析 + 冲突检测真实检查")
    parser.add_argument("--image", default="data/images/c1_garment.jpeg")
    parser.add_argument("--text", default="脖子短，领子不能太高")
    parser.add_argument("--vision-url", default=None, help="默认读 VISION_URL（http://127.0.0.1:8001）")
    parser.add_argument("--timeout", type=float, default=320.0)
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = settings.project_root / image_path
    if not image_path.is_file():
        print(f"找不到图片：{image_path}", file=sys.stderr)
        return 2

    base = (args.vision_url or settings.vision_url).rstrip("/")
    session = build_session(args.text)
    try:
        data_url = vision_client.encode_image_file(image_path)
        result = vision_client.deconstruct(data_url, vision_url=base, timeout=args.timeout)
    except vision_client.VisionServiceUnavailable as exc:
        print(f"视觉服务未启动或连不上：{exc}", file=sys.stderr)
        return 2
    except vision_client.VisionError as exc:
        print(f"看图失败：{exc}", file=sys.stderr)
        return 3

    analysis = reference.build_reference_analysis(
        image_url=f"/files/{image_path.name}",
        result=result,
        utterances=session.utterances,
    )
    ledger = reference.build_ledger_entry(
        analysis, version=1, at="2026-11-18T00:00:00+00:00"
    )
    payload = {
        "image": str(image_path.relative_to(settings.project_root))
        if settings.project_root in image_path.parents
        else str(image_path),
        "vision_url": base,
        "model": analysis["model"],
        "text": args.text,
        "reference": analysis,
        "ledger": ledger,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    conflicts = analysis["conflicts"]
    if conflicts:
        print("\n结论：发现与原话冲突 " + str(len(conflicts)) + " 处 —— "
              + "；".join(f"【{c['field']}】{c['message']}" for c in conflicts))
    else:
        print("\n结论：本次未发现冲突。请确认图里的领型是否被识别为“立领”。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
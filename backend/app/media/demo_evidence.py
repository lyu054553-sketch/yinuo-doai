"""C2 演示素材：纯本地的证据清单校验与核对摘要。

本模块**不调用任何模型、视觉服务、生图接口，也不发起网络请求**。它只做三件事：

1. 读 `demo_evidence.json`（演示素材清单）：登记“哪张图 × 哪个方案 × 哪份原始证据”。
2. 校验本地证据是否**存在且结构可读**（`orchestration/runs/*.json` 由总控真机跑出，
   首个 JSON 对象后可以附人工文字备注）。
3. 生成演示核对摘要：把证据里的 `field / expect / observed / verdict / confidence` **原样复述**。

口径（不得违反）：

- **不判定“视觉通过”。** 状态只有“待采集 / 已采集，需人工复核 / 证据无法识别，需人工复核”
  三种，绝不会有“通过”“符合”。
- **不改写观测与结论。** 摘要里的 `verdict` 逐项照抄证据；证据说“不符合”“看不出”，
  摘要就只能是“不符合”“看不出”，代码里没有任何把它改成“符合”的分支。
- **没有证据就是待采集**，不是通过，也不是失败。
- 清单里不复制任何证据内容（观测值、结论都从证据文件运行时读取）。

入口：

    from app.media import demo_evidence
    summary = demo_evidence.build_summary()                 # dict，确定性
    text = demo_evidence.format_text(summary)               # 中文摘要

命令行（项目根目录）：

    python backend/app/media/demo_evidence.py               # 打印摘要
    python backend/app/media/demo_evidence.py --json        # 只打印 JSON

退出码（只表示“素材/证据是否齐、能否读”，**不代表视觉是否通过**）：

- 0：所有案例的图片与证据都在，且每条证据都能读出核对项；
- 2：有案例缺图片或证据（待采集）；
- 3：证据存在但读不出核对项（结构无法识别，需人工复核）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

try:  # 作为包的一部分被 import
    from .check import VERDICTS
except ImportError:  # 直接 `python backend/app/media/demo_evidence.py` 运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.media.check import VERDICTS

MANIFEST_PATH = Path(__file__).with_name("demo_evidence.json")
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 状态词：只描述“证据采集/可读性”，不描述视觉结果。
PENDING = "待采集"
COLLECTED = "已采集，需人工复核"
UNRECOGNIZED = "证据无法识别，需人工复核"

# 摘要里逐项照抄的证据键（缺哪个就少抄哪个，不补值）。
ITEM_KEYS = ("field", "expect", "observed", "verdict", "confidence")

MANIFEST_CASE_KEYS = ("id", "label", "scenario", "image", "scheme_case", "evidence")

OVERALL_PENDING = "演示素材未齐（待采集）"
OVERALL_UNRECOGNIZED = "证据已采集，但有无法识别的证据，需人工复核"
OVERALL_COLLECTED = "证据已齐，需人工复核（系统不判定视觉通过）"


class DemoEvidenceError(RuntimeError):
    """清单或参数本身不合法（例如清单缺键）。`str(exc)` 是中文。"""


# ---------------------------------------------------------------- 清单


def load_manifest(path: Path | str | None = None) -> dict[str, Any]:
    """读清单并校验结构；清单不合法直接报错，不猜。"""
    manifest_path = Path(path) if path is not None else MANIFEST_PATH
    if not manifest_path.is_file():
        raise DemoEvidenceError(f"找不到演示素材清单：{manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoEvidenceError(f"演示素材清单不是合法 JSON：{manifest_path}") from exc
    validate_manifest(data)
    return data


def validate_manifest(data: Any) -> None:
    """清单必须是一个对象，含 `cases` 列表，每个案例都有约定键且类型正确。"""
    if not isinstance(data, Mapping):
        raise DemoEvidenceError("演示素材清单的根节点必须是对象。")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DemoEvidenceError("演示素材清单必须包含非空的 cases 列表。")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise DemoEvidenceError(f"cases[{index}] 必须是对象。")
        missing = [key for key in MANIFEST_CASE_KEYS if key not in case]
        if missing:
            raise DemoEvidenceError(f"cases[{index}] 缺少键：{'、'.join(missing)}。")
        for key in MANIFEST_CASE_KEYS:
            if not isinstance(case[key], str) or not case[key].strip():
                raise DemoEvidenceError(f"cases[{index}] 的 {key} 必须是非空字符串。")
        if case["id"] in seen:
            raise DemoEvidenceError(f"cases 里的 id 重复：{case['id']}。")
        seen.add(case["id"])
        for key in ("judged_fields", "always_unknown_fields"):
            value = case.get(key, [])
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise DemoEvidenceError(f"cases[{index}] 的 {key} 必须是字符串列表。")
        hints = case.get("manual_hints", [])
        if not isinstance(hints, list) or any(not isinstance(v, str) for v in hints):
            raise DemoEvidenceError(f"cases[{index}] 的 manual_hints 必须是字符串列表。")


# ---------------------------------------------------------------- 证据读取


def _load_json(path: Path) -> tuple[Any, str | None]:
    """读 JSON；读不出时返回 (None, 中文原因)，由调用方标“证据无法识别”。"""
    if not path.is_file():
        return None, f"证据文件不存在：{path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"证据文件读不出：{exc}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        try:
            payload, end = json.JSONDecoder().raw_decode(raw.lstrip())
        except json.JSONDecodeError:
            return None, f"证据文件不是合法 JSON：{exc}"
        trailing = raw.lstrip()[end:]
        if not isinstance(payload, dict) or not trailing.strip() or trailing.lstrip().startswith(("{", "[")):
            return None, f"证据文件不是合法 JSON：{exc}"
        return payload, None


def _walk(node: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _as_item(raw: Any) -> dict[str, Any] | None:
    """一个核对项必须至少有 field / observed / verdict，且 verdict 在固定词表内。"""
    if not isinstance(raw, Mapping):
        return None
    if "field" not in raw or "observed" not in raw or "verdict" not in raw:
        return None
    if raw["verdict"] not in VERDICTS:
        return None
    return {key: raw[key] for key in ITEM_KEYS if key in raw}


def extract_items(payload: Any) -> list[dict[str, Any]]:
    """从证据里取出核对项，**原样**返回（不排序、不改值、不补值）。

    优先用 `payload["check"]["items"]`（与约定 CheckResult 同形）；
    否则在整份证据里递归找带 field/observed/verdict 的对象。
    一项都找不到说明结构无法识别，返回空列表。
    """
    if not isinstance(payload, Mapping):
        return []
    block = payload.get("check")
    if isinstance(block, Mapping) and isinstance(block.get("items"), list):
        candidates: Iterable[Any] = block["items"]
    else:
        candidates = _walk(payload)
    items: list[dict[str, Any]] = []
    for raw in candidates:
        item = _as_item(raw)
        if item is not None:
            items.append(item)
    return items


def extract_model(payload: Any) -> str | None:
    """模型标识只照抄证据里已有的字符串，取不到返回 None，不编造。"""
    if not isinstance(payload, Mapping):
        return None
    block = payload.get("check")
    if isinstance(block, Mapping):
        model = block.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    for node in _walk(payload):
        model = node.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


# ---------------------------------------------------------------- 摘要


def _count(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in VERDICTS}
    for item in items:
        verdict = item.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def summarize_case(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """一个演示案例的状态与证据复述（不含任何“通过”结论）。"""
    image_rel = case["image"]
    evidence_rel = case["evidence"]
    image_path = root / image_rel
    evidence_path = root / evidence_rel

    image_exists = image_path.is_file()
    evidence_exists = evidence_path.is_file()

    record: dict[str, Any] = {
        "id": case["id"],
        "label": case["label"],
        "scenario": case["scenario"],
        "image": image_rel,
        "image_exists": image_exists,
        "scheme_case": case["scheme_case"],
        "evidence": evidence_rel,
        "evidence_exists": evidence_exists,
        "status": PENDING,
        "reason": "",
        "model": None,
        "items": [],
        "counted": _count([]),
        "mismatch_fields": [],
        "unknown_fields": [],
        "judged_fields": list(case.get("judged_fields", [])),
        "always_unknown_fields": list(case.get("always_unknown_fields", [])),
        "manual_hints": list(case.get("manual_hints", [])),
        "note": "只复述证据原文；系统不判定视觉通过，须人工复核。",
    }

    if not image_exists and not evidence_exists:
        record["status"] = PENDING
        record["reason"] = "演示图片与原始证据都还没采集到。"
        return record
    if not image_exists:
        record["status"] = PENDING
        record["reason"] = f"演示图片还没采集到：{image_rel}"
        return record
    if not evidence_exists:
        record["status"] = PENDING
        record["reason"] = f"原始证据还没采集到：{evidence_rel}"
        return record

    payload, error = _load_json(evidence_path)
    if payload is None:
        record["status"] = UNRECOGNIZED
        record["reason"] = error or "证据文件读不出。"
        return record

    items = extract_items(payload)
    if not items:
        record["status"] = UNRECOGNIZED
        record["reason"] = "证据里没有可识别的核对项（field/observed/verdict），请人工核对原文。"
        return record

    record["status"] = COLLECTED
    record["reason"] = "证据已读取，逐项照抄，交人工复核。"
    record["model"] = extract_model(payload)
    record["items"] = items
    record["counted"] = _count(items)
    # 照抄：不符合/看不出 各归各位，没有任何“改判为符合”的分支。
    record["mismatch_fields"] = [
        item["field"] for item in items if item.get("verdict") == "不符合"
    ]
    record["unknown_fields"] = [
        item["field"] for item in items if item.get("verdict") == "看不出"
    ]
    return record


def build_summary(
    root: Path | str | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成演示核对摘要（确定性：同样的清单+证据永远同样的输出，不含时间戳）。"""
    data = dict(manifest) if manifest is not None else load_manifest()
    validate_manifest(data)
    base = Path(root) if root is not None else PROJECT_ROOT
    cases = [summarize_case(case, base) for case in data["cases"]]

    statuses = {case["status"] for case in cases}
    if PENDING in statuses:
        overall = OVERALL_PENDING
    elif UNRECOGNIZED in statuses:
        overall = OVERALL_UNRECOGNIZED
    else:
        overall = OVERALL_COLLECTED

    return {
        "schema_version": int(data.get("schema_version", 1)),
        "kind": data.get("kind", "本地视觉演示素材清单"),
        "notice": data.get("notice", ""),
        "known_gap": data.get("known_gap", ""),
        "overall": overall,
        "cases": cases,
    }


def exit_code_for(summary: Mapping[str, Any]) -> int:
    """退出码只反映素材/证据状态，不反映视觉是否通过。"""
    statuses = {case.get("status") for case in summary.get("cases", [])}
    if PENDING in statuses:
        return 2
    if UNRECOGNIZED in statuses:
        return 3
    return 0


# ---------------------------------------------------------------- 文本


def format_text(summary: Mapping[str, Any]) -> str:
    lines = [
        "演示素材核对摘要（本地静态证据，未调用视觉服务与任何模型）",
        f"总体状态：{summary['overall']}",
        "说明：以下只复述证据原文，不代表视觉通过；不符合与看不出一律照实保留，须人工复核。",
    ]
    if summary.get("known_gap"):
        lines.append(f"已知缺口：{summary['known_gap']}")
    for case in summary.get("cases", []):
        lines.append("")
        lines.append(f"【{case['label']}】（案例编号 {case['id']}，场景：{case['scenario']}）")
        lines.append(
            f"  图片：{case['image']}（{'存在' if case['image_exists'] else '缺失'}）；"
            f"方案：{case['scheme_case']}"
        )
        lines.append(
            f"  证据：{case['evidence']}（{'存在' if case['evidence_exists'] else '缺失'}）"
        )
        lines.append(f"  状态：{case['status']}｜{case['reason']}")
        if case.get("model"):
            lines.append(f"  模型（照抄证据）：{case['model']}")
        for item in case.get("items", []):
            pieces = [f"【{item.get('field', '')}】{item.get('verdict', '')}"]
            if "expect" in item:
                pieces.append(f"预期：{item['expect']}")
            if "observed" in item:
                pieces.append(f"看到：{item['observed']}")
            if "confidence" in item:
                pieces.append(f"把握：{item['confidence']}")
            lines.append("  - " + "；".join(pieces))
        if case.get("mismatch_fields"):
            lines.append(
                "  不符合（照实保留，不得改写）：" + "、".join(case["mismatch_fields"])
            )
        if case.get("unknown_fields"):
            lines.append("  看不出：" + "、".join(case["unknown_fields"]))
        if case.get("always_unknown_fields"):
            lines.append(
                "  请人工确认（冻结策略不采用模型结果）：" + "、".join(case["always_unknown_fields"])
            )
        for hint in case.get("manual_hints", []):
            lines.append(f"  复核提示：{hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 命令行


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C2 演示素材：校验本地证据是否存在/可读，并生成演示核对摘要（不调用任何模型）"
    )
    parser.add_argument("--root", default=None, help="项目根目录（默认自动推断）")
    parser.add_argument("--json", action="store_true", help="只打印 JSON，不打印中文摘要")
    args = parser.parse_args(argv)

    try:
        summary = build_summary(root=args.root)
    except DemoEvidenceError as exc:
        print(f"演示素材清单有问题：{exc}", file=sys.stderr)
        return 4

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.json:
        print()
        print(format_text(summary))
        print()
        print("注：退出码只表示素材/证据是否齐全可读，不代表视觉通过。")
    return exit_code_for(summary)


__all__ = [
    "MANIFEST_PATH", "PROJECT_ROOT", "PENDING", "COLLECTED", "UNRECOGNIZED",
    "OVERALL_PENDING", "OVERALL_UNRECOGNIZED", "OVERALL_COLLECTED",
    "DemoEvidenceError", "load_manifest", "validate_manifest", "extract_items",
    "extract_model", "summarize_case", "build_summary", "exit_code_for",
    "format_text", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())

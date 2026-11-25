"""C2 演示素材：离线合成测试（**不连视觉服务、不调任何模型、不联网**）。

重点证明三件事：

1. 证据里原始观测与方案不一致时，摘要**照抄“不符合”**，绝不能被任何分支改成“符合”；
2. 没有证据（或证据读不出）时状态是“待采集 / 证据无法识别”，**不是通过**；
3. 整个摘要流程不调用视觉服务、生图接口或任何网络。

证据内容均为**合成的测试数据**，不是真机结果；真机证据由总控另外跑出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.media import demo_evidence, image_provider, vision_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]
IMAGE_REL = "data/images/demo.jpeg"
EVIDENCE_REL = "orchestration/runs/demo-evidence.json"

# 合成证据：领型“不符合”、纽扣“符合”、长度“看不出”。仅用于测试，不代表真机观测。
SYNTHETIC_EVIDENCE = {
    "check": {
        "model": "fake-vision-model",
        "items": [
            {"field": "领型", "expect": "交领", "observed": "立领", "verdict": "不符合", "confidence": "疑似"},
            {"field": "版型结构", "expect": "不应看到纽扣", "observed": "无外露纽扣", "verdict": "符合", "confidence": "疑似"},
            {"field": "装饰纹样", "expect": "应有可见纹样", "observed": "看不出", "verdict": "看不出", "confidence": "看不出"},
        ],
    }
}


def make_manifest(**overrides) -> dict:
    case = {
        "id": "demo-case",
        "label": "合成演示案例",
        "scenario": "反例",
        "image": IMAGE_REL,
        "scheme_case": "c6",
        "evidence": EVIDENCE_REL,
        "judged_fields": ["领型"],
        "always_unknown_fields": ["长度比例"],
        "manual_hints": ["逐项对照证据原文，不得改写。"],
    }
    case.update(overrides)
    return {"schema_version": 1, "kind": "测试清单", "cases": [case]}


def make_root(tmp_path: Path, *, image: bool = True, evidence: object | None = None,
              evidence_text: str | None = None) -> Path:
    if image:
        image_path = tmp_path / IMAGE_REL
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"demo-image" * 8)
    evidence_path = tmp_path / EVIDENCE_REL
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if evidence_text is not None:
        evidence_path.write_text(evidence_text, encoding="utf-8")
    elif evidence is not None:
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------ 清单


def test_real_manifest_is_valid_and_has_no_evidence_content():
    manifest = demo_evidence.load_manifest()
    ids = [case["id"] for case in manifest["cases"]]
    assert ids == ["c2-c1-counterexample", "c2-c6-live"]
    evidences = [case["evidence"] for case in manifest["cases"]]
    assert evidences == [
        "orchestration/runs/c2-c1-counterexample.json",
        "orchestration/runs/c2-c6-live.json",
    ]
    images = [case["image"] for case in manifest["cases"]]
    assert images == ["data/images/c1_garment.jpeg", "data/images/c6_garment.jpeg"]

    # 清单只是索引：不得内嵌任何核对项（观测/结论都从证据文件运行时读取）。
    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    for node in walk(manifest):
        assert not ({"observed", "verdict", "items"} & set(node)), node

    text = json.dumps(manifest, ensure_ascii=False)
    assert "5cm" not in text and "cm" not in text  # 不塞任何虚构尺寸
    assert "通过" not in text.replace("不判定“视觉通过”", "")  # 不宣称通过


def test_manifest_requires_case_keys():
    bad = {"cases": [{"id": "x", "label": "y"}]}
    with pytest.raises(demo_evidence.DemoEvidenceError):
        demo_evidence.validate_manifest(bad)


def test_manifest_rejects_duplicate_ids():
    case = make_manifest()["cases"][0]
    with pytest.raises(demo_evidence.DemoEvidenceError) as exc:
        demo_evidence.validate_manifest({"cases": [dict(case), dict(case)]})
    assert "重复" in str(exc.value)


# ------------------------------------------------------------ 没证据 = 待采集


def test_missing_evidence_is_pending_not_pass(tmp_path):
    root = make_root(tmp_path, evidence=None)  # 只有图片，没有证据
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())

    case = summary["cases"][0]
    assert case["status"] == demo_evidence.PENDING
    assert case["evidence_exists"] is False
    assert summary["overall"] == demo_evidence.OVERALL_PENDING
    assert demo_evidence.exit_code_for(summary) == 2
    assert "证据还没采集到" in case["reason"]

    # 摘要与文本都不能把“待采集”说成通过。
    assert "通过" not in case["status"]
    assert "待采集" in demo_evidence.format_text(summary)


def test_missing_image_is_pending(tmp_path):
    root = make_root(tmp_path, image=False, evidence=SYNTHETIC_EVIDENCE)
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    assert summary["cases"][0]["status"] == demo_evidence.PENDING
    assert summary["cases"][0]["image_exists"] is False


def test_unreadable_evidence_is_not_pass(tmp_path):
    root = make_root(tmp_path, evidence_text="{不是合法 JSON")
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    case = summary["cases"][0]
    assert case["status"] == demo_evidence.UNRECOGNIZED
    assert summary["overall"] == demo_evidence.OVERALL_UNRECOGNIZED
    assert demo_evidence.exit_code_for(summary) == 3
    assert case["items"] == []


def test_json_with_human_notes_keeps_original_items(tmp_path):
    root = make_root(
        tmp_path,
        evidence_text=json.dumps(SYNTHETIC_EVIDENCE, ensure_ascii=False)
        + "\n\n结论：领型不符合，需人工复核。\n",
    )
    case = demo_evidence.build_summary(root=root, manifest=make_manifest())["cases"][0]
    assert case["status"] == demo_evidence.COLLECTED
    assert case["mismatch_fields"] == ["领型"]


def test_concatenated_json_objects_are_rejected(tmp_path):
    root = make_root(tmp_path, evidence_text='{"check": {"items": []}}\n{"check": {"items": []}}')
    case = demo_evidence.build_summary(root=root, manifest=make_manifest())["cases"][0]
    assert case["status"] == demo_evidence.UNRECOGNIZED


def test_evidence_without_check_items_is_unrecognized(tmp_path):
    root = make_root(tmp_path, evidence={"note": "只有说明，没有核对项"})
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    assert summary["cases"][0]["status"] == demo_evidence.UNRECOGNIZED


# ------------------------------------------------------------ 原样保留观测与结论


def test_original_mismatch_is_never_rewritten_to_match(tmp_path):
    root = make_root(tmp_path, evidence=SYNTHETIC_EVIDENCE)
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    case = summary["cases"][0]

    assert case["status"] == demo_evidence.COLLECTED
    collar = next(item for item in case["items"] if item["field"] == "领型")
    assert collar["observed"] == "立领"
    assert collar["verdict"] == "不符合"
    assert case["mismatch_fields"] == ["领型"]
    assert case["counted"]["不符合"] == 1
    assert case["counted"]["符合"] == 1
    assert case["model"] == "fake-vision-model"

    text = demo_evidence.format_text(summary)
    assert "【领型】不符合" in text
    assert "看到：立领" in text
    assert "不得改写" in text  # 明确提示人工复核，不改判


def test_unknown_verdict_stays_unknown(tmp_path):
    root = make_root(tmp_path, evidence=SYNTHETIC_EVIDENCE)
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    case = summary["cases"][0]
    pattern = next(item for item in case["items"] if item["field"] == "装饰纹样")
    assert pattern["verdict"] == "看不出"
    assert pattern["observed"] == "看不出"
    assert "装饰纹样" in case["unknown_fields"]
    assert "装饰纹样" not in case["mismatch_fields"]
    assert [item["field"] for item in case["items"]] == ["领型", "版型结构", "装饰纹样"]


def test_statuses_never_say_pass(tmp_path):
    for evidence in (None, SYNTHETIC_EVIDENCE, {"note": "x"}):
        root = make_root(tmp_path / str(id(evidence)), evidence=evidence)
        summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
        for case in summary["cases"]:
            assert case["status"] in {
                demo_evidence.PENDING,
                demo_evidence.COLLECTED,
                demo_evidence.UNRECOGNIZED,
            }
        assert summary["overall"] != "通过"
        assert "视觉通过" not in summary["overall"].replace("不判定视觉通过", "")


# ------------------------------------------------------------ 证据解析


def test_extract_items_prefers_check_block_and_keeps_order():
    payload = {
        "check": {"items": SYNTHETIC_EVIDENCE["check"]["items"]},
        "debug": {"items": [{"field": "领型", "observed": "翻领", "verdict": "不符合"}]},
    }
    items = demo_evidence.extract_items(payload)
    assert [item["field"] for item in items] == ["领型", "版型结构", "装饰纹样"]
    assert items[0]["observed"] == "立领"  # 用 check 区块，不被 debug 干扰


def test_extract_items_falls_back_to_nested_and_skips_invalid_verdict():
    payload = {
        "nested": [
            {"field": "领型", "observed": "立领", "verdict": "差不多"},
            {"field": "装饰纹样", "observed": "无可见纹样", "verdict": "不符合", "confidence": "疑似"},
        ]
    }
    items = demo_evidence.extract_items(payload)
    assert items == [
        {"field": "装饰纹样", "observed": "无可见纹样", "verdict": "不符合", "confidence": "疑似"}
    ]
    assert demo_evidence.extract_items("不是对象") == []


def test_summary_is_deterministic(tmp_path):
    root = make_root(tmp_path, evidence=SYNTHETIC_EVIDENCE)
    first = demo_evidence.build_summary(root=root, manifest=make_manifest())
    second = demo_evidence.build_summary(root=root, manifest=make_manifest())
    assert first == second
    assert "时间" not in json.dumps(first, ensure_ascii=False)


# ------------------------------------------------------------ 不联网


def test_demo_summary_makes_no_vision_or_image_calls(tmp_path, monkeypatch):
    calls: list[str] = []

    def boom(name):
        def _boom(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"摘要流程不应该调用 {name}")

        return _boom

    monkeypatch.setattr(vision_client, "deconstruct", boom("vision.deconstruct"))
    monkeypatch.setattr(vision_client, "_post_json", boom("vision._post_json"))
    monkeypatch.setattr(image_provider, "_post_json", boom("image_provider._post_json"))

    root = make_root(tmp_path, evidence=SYNTHETIC_EVIDENCE)
    summary = demo_evidence.build_summary(root=root, manifest=make_manifest())
    demo_evidence.format_text(summary)
    assert calls == []


# ------------------------------------------------------------ 真机证据（存在才查）


def test_real_evidence_if_present_never_claims_pass():
    if not (PROJECT_ROOT / EVIDENCE_REL).parent.is_dir():
        pytest.skip("orchestration/runs 不存在，真机证据尚未采集")
    manifest = demo_evidence.load_manifest()
    missing = [
        case["evidence"]
        for case in manifest["cases"]
        if not (PROJECT_ROOT / case["evidence"]).is_file()
    ]
    if missing:
        pytest.skip(f"真机证据尚未采集：{'、'.join(missing)}")

    summary = demo_evidence.build_summary()
    for case in summary["cases"]:
        # 证据在也要人工复核；状态绝不含“通过”。
        assert case["status"] in {demo_evidence.COLLECTED, demo_evidence.UNRECOGNIZED}
        assert "通过" not in case["status"]
    print(demo_evidence.format_text(summary))


# ------------------------------------------------------------ C4 演示提示词


def test_c4_prompt_has_no_fabricated_dimensions():
    worklog = (PROJECT_ROOT / "backend/app/media/WORKLOG.md").read_text(encoding="utf-8")
    prompt = worklog.split("参考图生成提示词", 1)[1].split("配套演示说明", 1)[0]
    assert "高立领（贴合颈部" in prompt
    assert "领高约 5cm" not in prompt
    assert "5cm" not in prompt

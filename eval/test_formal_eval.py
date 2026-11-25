#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2 评测 runner 的离线单元测试：不联网、不启动后端、不发任何模型请求。

覆盖：
1. 审批门禁：正确哈希通过；被篡改/未批准/缺文件一律拒绝。
2. 数据规范化：注入集 20 组/18 问题/5 个无问题组；原话状态取最严重映射；开发集 6 个问题句；
   多件套方案拍平；注入组请求体只有口述与已有方案。
3. 标签泄漏防护：多带 expected_issues / 标题 / 答案说明文字 / “测试注入”字样，必须拒绝发送。
4. 对照组配对：只算问题检出；误报按标准“已落实”与“间接体现”分列；未匹配主张单列；
   非原文子串的问题条目单列为严重错误。
5. 失败保留分母：一个未知状态失败仍留在问题检出分母里；稳定性不得判为一致。
6. resume 决策与 checkpoint：成功复用、未知不重试、确定失败可重跑。
7. 留出集 c5：注入集出现 c5、开发集请求体出现 c5，一律拒绝。
8. 密钥只从环境读取，状态摘要不含明文。
9. 报告生成冒烟：能生成含分子/分母的中文报告。

用法（由总控执行）：
    python eval/test_formal_eval.py
    python -m pytest eval/test_formal_eval.py -q
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import formal_baseline  # noqa: E402
import formal_data  # noqa: E402
import formal_http  # noqa: E402
import formal_report  # noqa: E402
import run_formal_eval  # noqa: E402
import run_eval  # noqa: E402

REPO = formal_data.repo_root()
DATA_DIR = os.path.join(REPO, "data")
DATASET = os.path.join(HERE, "datasets", "injected.json")
APPROVAL = os.path.join(REPO, "orchestration", "approvals", "d1.json")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _write(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _tmpdir() -> str:
    return tempfile.mkdtemp(prefix="yinuo-d2-test-")


def _raises(fn, needle: str = "") -> bool:
    try:
        fn()
    except formal_data.FormalDataError as exc:
        return needle in str(exc)
    return False


# ---------------------------------------------------------------------------
# 1. 审批门禁
# ---------------------------------------------------------------------------

def test_real_approval_passes_by_hash():
    info = formal_data.check_approval(APPROVAL, DATASET)
    assert info["decision"] == "approved"
    assert info["sha256_match"] is True
    assert info["dataset_sha256_actual"] == info["dataset_sha256_expected"]


def test_approval_rejects_tampered_dataset():
    tmp = _tmpdir()
    try:
        record = json.load(open(APPROVAL, "r", encoding="utf-8"))
        copy_path = os.path.join(tmp, "injected.json")
        shutil.copyfile(DATASET, copy_path)
        record["dataset_sha256"] = "0" * 64
        approval_path = os.path.join(tmp, "d1.json")
        _write(approval_path, record)
        assert _raises(lambda: formal_data.check_approval(approval_path, copy_path), "SHA256")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_approval_rejects_not_approved_or_missing():
    tmp = _tmpdir()
    try:
        record = json.load(open(APPROVAL, "r", encoding="utf-8"))
        record["decision"] = "pending_review"
        approval_path = os.path.join(tmp, "d1.json")
        _write(approval_path, record)
        assert _raises(lambda: formal_data.check_approval(approval_path, DATASET), "尚未获批")
        assert _raises(lambda: formal_data.check_approval(os.path.join(tmp, "none.json"), DATASET), "找不到")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. 数据规范化
# ---------------------------------------------------------------------------

def test_injected_dataset_shape():
    inj = formal_data.load_injected_dataset(DATASET)
    assert len(inj["group_ids"]) == 20
    assert len(set(inj["group_ids"])) == 20
    assert inj["issue_count"] == 18
    assert sorted(inj["clean_group_ids"]) == ["inj05", "inj06", "inj10", "inj14", "inj18"]
    assert "c5" not in inj["group_ids"]

    gold = inj["gold_by_id"]["inj01"]
    note_utterance = next(u for u in gold["utterances"] if u["id"] == "inj01-u6")
    assert note_utterance["status"] == "被改写"
    assert note_utterance["mappings"][0]["field"] == "装饰纹样"

    # 一个问题句同时映射两栏时，状态取最严重的一条
    gold2 = inj["gold_by_id"]["inj02"]
    conflict = next(u for u in gold2["utterances"] if u["id"] == "inj02-u3")
    assert conflict["status"] == "有冲突"
    assert len(conflict["mappings"]) == 2


def test_dev_gold_problem_count_is_six():
    gold = formal_data.load_dev_gold(DATA_DIR)
    counts = {
        cid: sum(1 for u in gold[cid]["utterances"] if u["status"] in formal_data.PROBLEM_STATUSES)
        for cid in formal_data.DEV_CASE_IDS
    }
    assert counts == {"c1": 0, "c2": 3, "c3": 1, "c4": 1, "c6": 1}
    assert "c5" not in gold


def test_normalize_existing_spec_flattens_multi_piece():
    spec = {"版型结构": {"外套": "H型", "连衣裙": "A型"}, "领型": "立领"}
    flat = run_eval.normalize_existing_spec(spec)
    assert flat["版型结构"] == "外套：H型；连衣裙：A型"
    assert flat["领型"] == "立领"
    assert flat["面料"] == ""


def test_injected_payload_only_text_and_spec():
    inj = formal_data.load_injected_dataset(DATASET)
    for gid in inj["group_ids"]:
        gold = inj["gold_by_id"][gid]
        payload = formal_data.injected_payload(gold)
        assert set(payload.keys()) == {"text", "existing_spec"}
        assert payload["text"] == gold["text"]
        assert set(payload["existing_spec"].keys()) == set(formal_data.FIELDS)
        serialized = json.dumps(payload, ensure_ascii=False)
        for token in ("测试注入", "虚构", "expected_issues", "resources"):
            assert token not in serialized, f"{gid} 请求体出现 {token}"


# ---------------------------------------------------------------------------
# 3. 标签泄漏防护
# ---------------------------------------------------------------------------

def test_payload_guard_blocks_answer_material():
    inj = formal_data.load_injected_dataset(DATASET)
    gold = inj["gold_by_id"]["inj04"]
    payload = formal_data.injected_payload(gold)
    formal_data.assert_payload_clean("yinuo", "injected", gold, payload)

    with_issues = dict(payload)
    with_issues["expected_issues"] = gold["expected_issues"]
    assert _raises(lambda: formal_data.assert_payload_clean("yinuo", "injected", gold, with_issues), "非输入字段")

    with_title = dict(payload)
    with_title["title"] = gold["title"]
    assert _raises(lambda: formal_data.assert_payload_clean("yinuo", "injected", gold, with_title), "标题")

    with_sample = dict(payload)
    with_sample["sample_id"] = "inj04"
    assert _raises(lambda: formal_data.assert_payload_clean("yinuo", "injected", gold, with_sample), "sample_id")

    leaked_spec = copy.deepcopy(payload)
    leaked_spec["existing_spec"]["面料"] = "云锦绒面料（测试注入）"
    assert _raises(lambda: formal_data.assert_payload_clean("yinuo", "injected", gold, leaked_spec), "答案提示词")

    leaked_note = dict(payload)
    leaked_note["text"] = payload["text"] + gold["utterances"][0]["mappings"][0]["note"]
    assert _raises(lambda: formal_data.assert_payload_clean("yinuo", "injected", gold, leaked_note), "标准答案")


def test_baseline_request_has_no_answer_material():
    import argparse

    inj = formal_data.load_injected_dataset(DATASET)
    gold = inj["gold_by_id"]["inj01"]
    args = argparse.Namespace(model="m", baseline_max_tokens=100, baseline_response_format=False)
    plan = {"lane": "baseline", "subset": "injected", "case_id": "inj01", "repeat": 1, "gold_problem_count": 1}
    request = run_formal_eval.build_baseline_request(plan, gold, args)
    packed = json.dumps(request, ensure_ascii=False)
    assert gold["title"] not in packed, "对照组请求不得含注入组标题"
    assert gold["category"] not in packed, "对照组请求不得含注入组类别"
    assert gold["expected_issues"][0]["reason"] not in packed
    assert "expected_issues" not in packed
    assert gold["text"] in packed  # 客户口述必须发给对照组
    for field in formal_data.FIELDS:
        assert gold["spec"][field] in packed


def test_dev_payload_refuses_holdout():
    fake = {"id": "c5", "title": "留出", "text": "随便一句", "spec": {f: "" for f in formal_data.FIELDS}}
    assert _raises(lambda: formal_data.dev_payload(fake), "非开发")


def test_injected_dataset_refuses_holdout_group():
    raw = json.load(open(DATASET, "r", encoding="utf-8"))
    raw = copy.deepcopy(raw)
    raw["groups"][0]["id"] = "c5"
    tmp = _tmpdir()
    try:
        path = os.path.join(tmp, "injected_bad.json")
        _write(path, raw)
        assert _raises(lambda: formal_data.load_injected_dataset(path), "留出")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. 对照组：只算问题检出
# ---------------------------------------------------------------------------

def _baseline_gold() -> Dict[str, Any]:
    return {
        "id": "x1",
        "title": "测试用案例",
        "text": "我想要一件宽松外套，领子低一点，面料用真丝。整体不要收腰。",
        "utterances": [
            {"id": "u1", "text": "我想要一件宽松外套", "status": "已落实",
             "mappings": [{"field": "松弛余量", "status": "已落实"}]},
            {"id": "u2", "text": "领子低一点", "status": "被改写",
             "mappings": [{"field": "领型", "status": "被改写"}]},
            {"id": "u3", "text": "面料用真丝", "status": "已落实",
             "mappings": [{"field": "面料", "status": "已落实"}]},
            {"id": "u4", "text": "整体不要收腰", "status": "未落实",
             "mappings": [{"field": "版型结构", "status": "未落实"}]},
        ],
    }


def test_baseline_detection_fp_and_unmatched_claims():
    gold = _baseline_gold()
    items = [
        {"index": 1, "text": "领子低一点", "field": "领型", "reason": "方案改成高领", "error": None},
        {"index": 2, "text": "面料用真丝", "field": "面料", "reason": "方案改成化纤", "error": None},
        {"index": 3, "text": "整体不要收腰", "field": "色彩", "reason": "方案收腰", "error": None},
        {"index": 4, "text": "客户还说不要太长", "field": "长度比例", "reason": "未提长度", "error": None},
        {"index": 5, "text": "我想要一件宽松外套", "field": "松弛余量", "reason": "方案偏紧", "error": None},
    ]
    metrics = formal_baseline.evaluate_baseline("x1", items, gold["text"], gold)
    assert metrics["gold_problem_count"] == 2
    assert metrics["detected"] == 2, "两个标准问题句都应有条目配上"
    assert metrics["field_agree"] == 1, "只有“领子低一点”的字段对得上"
    assert metrics["fp_by_status"]["已落实"] == 2, "面料用真丝、想要宽松一点 应算标准已落实的误报"
    assert metrics["fp_by_status"].get("间接体现", 0) == 0
    assert len(metrics["claims_unmatched"]) == 1
    assert metrics["claims_unmatched"][0]["text"] == "客户还说不要太长"
    assert len(metrics["fabricated_texts"]) == 1
    assert metrics["fabricated_texts"][0]["type"].startswith("引文不是原文子串")
    # 对照组不产出拆句/字段比率，避免与完整系统混比
    assert "seg" not in metrics
    assert "field" not in metrics

    rows = {r["gold_utterance_id"]: r for r in metrics["rows"]}
    assert rows["u4"]["detected"] is True and rows["u4"]["field_match"] is False


def test_baseline_parse_fenced_and_invalid():
    good = {
        "model": "fake-model",
        "choices": [{"message": {"content": '```json\n{"issues":[{"text":"领子低一点","field":"领型","reason":"改了"}]}\n```'}}],
    }
    parsed = formal_baseline.parse_baseline_response(good)
    assert parsed["ok"] is True
    assert parsed["items"][0]["field"] == "领型"
    assert parsed["model"] == "fake-model"

    bad = {"choices": [{"message": {"content": "我觉得没什么问题"}}]}
    assert formal_baseline.parse_baseline_response(bad)["ok"] is False

    out_of_schema = {"choices": [{"message": {"content": '{"issues":[{"text":"x","field":"颜色"}]}'}}]}
    parsed2 = formal_baseline.parse_baseline_response(out_of_schema)
    assert parsed2["ok"] is False, "所有条目字段都不在词表内时应判失败，不能硬凑"


# ---------------------------------------------------------------------------
# 5. 失败保留分母 + 稳定性
# ---------------------------------------------------------------------------

def _empty_fields() -> Dict[str, Any]:
    return {f: {"value": "", "resource_ids": [], "utterance_ids": [], "origin": "已有方案", "confirmed": False}
            for f in formal_data.FIELDS}


def test_failure_stays_in_denominator_and_blocks_stability():
    plan = [
        {"lane": "yinuo", "subset": "injected", "case_id": "g1", "repeat": 1, "gold_problem_count": 2},
        {"lane": "yinuo", "subset": "injected", "case_id": "g1", "repeat": 2, "gold_problem_count": 2},
    ]
    metrics = {
        "problem": {
            "gold_problem_count": 2,
            "detected": 1,
            "rows": [
                {"gold_utterance_id": "g1-u1", "detected": True, "pred_status": "被改写"},
                {"gold_utterance_id": "g1-u2", "detected": False, "pred_status": "已落实"},
            ],
            "false_positives": [],
        },
        "seg": {"gold": 3, "pred": 3, "paired": 3},
        "field": {"gold_fields": 3, "pred_fields": 3, "match": 3},
        "status": {"paired": 3, "agree": 2, "loose_agree": 2},
        "fabrication": {"violations": [], "ungrounded_fields": ["面料"]},
    }
    units = [
        {"lane": "yinuo", "subset": "injected", "case_id": "g1", "repeat": 1, "ok": True,
         "outcome": "success", "elapsed_ms": 100.0, "metrics": metrics},
        {"lane": "yinuo", "subset": "injected", "case_id": "g1", "repeat": 2, "ok": False,
         "outcome": "unknown", "error": "超时", "elapsed_ms": 300.0, "metrics": None},
    ]
    stats = formal_report.aggregate(units, plan)["yinuo"]["injected"]
    assert stats["gold_problems"] == 4, "分母必须是计划重复的问题句总数，失败不剔除"
    assert stats["detected"] == 1
    assert stats["units_success"] == 1
    assert stats["units_unknown_failure"] == 1
    assert stats["fp_已落实"] == 0
    assert stats["ungrounded_fields"] == 1

    stability = formal_report.stability_for(units, "yinuo", "injected", "g1", 2)
    assert stability["consistent"] is False
    assert "不能判定一致" in stability["status"]


def test_fp_split_keeps_indirect_visible():
    plan = [{"lane": "baseline", "subset": "dev", "case_id": "d1", "repeat": 1, "gold_problem_count": 0}]
    metrics = {
        "gold_problem_count": 0,
        "detected": 0,
        "items_count": 2,
        "invalid_items": 0,
        "field_agree": 0,
        "claims_unmatched": [],
        "fabricated_texts": [],
        "schema_errors": [],
        "false_positives": [
            {"gold_status": "已落实", "gold_utterance_id": "u1"},
            {"gold_status": "间接体现", "gold_utterance_id": "u2"},
        ],
    }
    units = [{"lane": "baseline", "subset": "dev", "case_id": "d1", "repeat": 1, "ok": True,
              "outcome": "success", "metrics": metrics}]
    stats = formal_report.aggregate(units, plan)["baseline"]["dev"]
    assert stats["fp_已落实"] == 1 and stats["fp_间接体现"] == 1
    assert stats["fp_total"] == 2


# ---------------------------------------------------------------------------
# 6. resume 决策与 checkpoint
# ---------------------------------------------------------------------------

def test_decide_unit_action_resume_rules():
    success = {"outcome": formal_http.OUTCOME_SUCCESS}
    unknown = {"outcome": formal_http.OUTCOME_UNKNOWN}
    definite = {"outcome": formal_http.OUTCOME_DEFINITE}
    assert run_formal_eval.decide_unit_action(success, False) == "execute"
    assert run_formal_eval.decide_unit_action(success, True) == "reuse_success"
    assert run_formal_eval.decide_unit_action(unknown, True) == "keep_unknown", "未知状态不得自动重试"
    assert run_formal_eval.decide_unit_action(unknown, False) == "execute"
    assert run_formal_eval.decide_unit_action(definite, True) == "execute"
    assert run_formal_eval.decide_unit_action(None, True) == "execute"


def test_checkpoint_roundtrip_and_key_paths():
    tmp = _tmpdir()
    try:
        plan = {"lane": "yinuo", "subset": "injected", "case_id": "inj01", "repeat": 1,
                "gold_problem_count": 1}
        path = run_formal_eval.checkpoint_path(tmp, plan)
        assert path.endswith("yinuo__injected__inj01__r1.json")
        assert run_formal_eval.load_checkpoint(path) is None
        run_formal_eval.save_json(path, {"outcome": "success", "unit": plan})
        loaded = run_formal_eval.load_checkpoint(path)
        assert loaded["outcome"] == "success"
        assert loaded["unit"]["case_id"] == "inj01"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7. 密钥
# ---------------------------------------------------------------------------

def test_api_key_only_from_env_and_redacted():
    name = "YINUO_TEST_MAGPIE_KEY"
    os.environ.pop(name, None)
    absent = formal_http.resolve_api_key([name])
    assert absent["present"] is False and absent["key"] is None
    try:
        os.environ[name] = "sk-test-secret-value"
        present = formal_http.resolve_api_key([name])
        assert present["present"] is True
        assert present["env"] == name
        status = formal_http.key_status(present)
        assert status == {"present": True, "env": name, "checked": None}
        assert "sk-test-secret-value" not in json.dumps(status, ensure_ascii=False)
    finally:
        os.environ.pop(name, None)


# ---------------------------------------------------------------------------
# 8. 报告冒烟
# ---------------------------------------------------------------------------

def test_report_smoke_lists_fractions_and_failures():
    gold = _baseline_gold()
    plan = [
        {"lane": "yinuo", "subset": "dev", "case_id": "x1", "repeat": 1, "gold_problem_count": 2},
        {"lane": "baseline", "subset": "dev", "case_id": "x1", "repeat": 1, "gold_problem_count": 2},
    ]
    yinuo_metrics = {
        "problem": {
            "gold_problem_count": 2,
            "detected": 1,
            "rows": [
                {"gold_utterance_id": "u2", "gold_text": "领子低一点", "gold_status": "被改写",
                 "detected": True, "pred_status": "被改写", "gold_fields": ["领型"]},
                {"gold_utterance_id": "u4", "gold_text": "整体不要收腰", "gold_status": "未落实",
                 "detected": False, "pred_status": "已落实", "gold_fields": ["版型结构"]},
            ],
            "false_positives": [
                {"gold_utterance_id": "u3", "gold_text": "面料用真丝", "gold_status": "已落实",
                 "pred_text": "面料用真丝", "pred_status": "未落实"},
            ],
        },
        "seg": {"gold": 4, "pred": 4, "paired": 4},
        "field": {"gold_fields": 4, "pred_fields": 4, "match": 3},
        "status": {"paired": 4, "agree": 2, "loose_agree": 2},
        "fabrication": {"violations": [], "ungrounded_fields": []},
    }
    baseline_metrics = formal_baseline.evaluate_baseline(
        "x1",
        [{"index": 1, "text": "领子低一点", "field": "领型", "reason": "改了", "error": None}],
        gold["text"],
        gold,
    )
    units = [
        {"lane": "yinuo", "subset": "dev", "case_id": "x1", "repeat": 1, "ok": True,
         "outcome": "success", "elapsed_ms": 120.0, "metrics": yinuo_metrics},
        {"lane": "baseline", "subset": "dev", "case_id": "x1", "repeat": 1, "ok": True,
         "outcome": "success", "elapsed_ms": 80.0, "metrics": baseline_metrics},
    ]
    stats = formal_report.aggregate(units, plan)
    summary = {
        "run_id": "test-run",
        "run_dir": "/tmp/test-run",
        "started_at": "2025-01-01T00:00:00",
        "finished_at": "2025-01-01T00:01:00",
        "headline": "测试用一句话结论。",
        "planned_count": 2,
        "success_count": 2,
        "failure_count": 0,
        "units": units,
        "planned": plan,
        "stats": stats,
        "failures": [],
        "lanes": ["yinuo", "baseline"],
        "subsets": ["dev"],
        "golds": {"x1": gold},
        "environment": {
            "base_url": "http://127.0.0.1:8100",
            "mock_mode": False,
            "health_model": "auto/deepseek-v4.1-flash",
            "model": "auto/deepseek-v4.1-flash",
            "model_matches": True,
            "image_provider": "none",
            "vision_ok": True,
            "baseline_endpoint": "http://127.0.0.1:3425/v1/chat/completions",
            "api_key_env": None,
            "api_key_present": False,
            "repeats": 1,
            "concurrency": 3,
            "timeout": 180.0,
            "dataset_sha256": "a" * 64,
            "approval_path": "/tmp/d1.json",
            "approval_recorded_at": "2025-01-01T00:00:00",
            "approval_sha256_match": True,
            "scheme_frozen": False,
            "holdout_authorized": False,
            "dev_case_ids": ["x1"],
            "dev_issue_count": 2,
            "injected_group_count": 20,
            "injected_issue_count": 18,
            "injected_clean_groups": ["inj05"],
            "injected_group_ids": ["inj01"],
        },
    }
    markdown = formal_report.build_report(summary)
    for needle in ("# 衣诺 D2 正式评测报告", "分子/分母", "逐句核对", "失败清单", "局限"):
        assert needle in markdown, f"报告缺少「{needle}」"
    assert "1/2" in markdown
    assert "标准“已落实”被报成问题" in markdown


# ---------------------------------------------------------------------------
# 简易运行器
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]
    failed: List[str] = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed.append(name)
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {name}")
    print("-" * 60)
    print(f"共 {len(tests)} 项，通过 {len(tests) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
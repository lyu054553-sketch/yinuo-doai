#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D3 一次性留出集脚本的离线测试：临时目录 + 合成案例 + 假 HTTP。

覆盖必须证明的几点：
1. 缺授权时不读取 data、不发请求；
2. 代码快照 SHA256 与批准不一致时拒绝；
3. 只在门禁全部通过后调用一次 POST，且请求体不含答案字段；
4. 超时/未知结果也保留 latch，且不自动重试；
5. 后端 MOCK 模式拒绝运行；
6. latch 存在后任何运行都拒绝（即使首次请求失败）。

测试用的案例与接口返回全部是临时合成的，**不含任何真实留出集内容或预期指标**。
运行方式：`python eval/test_holdout_once.py`（由 GPT 总控执行）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_holdout_once as d3  # noqa: E402

FIELDS = ["品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量"]

MODEL = "fake-model-for-offline-test"
SYNTHETIC_TEXT = "合成测试用口述：希望版型宽松，领子别太高。"
SYNTHETIC_SPEC = {f: f"{f}的合成取值" for f in FIELDS}


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

def write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(payload, (bytes, bytearray)) else "w"
    kwargs = {} if mode == "wb" else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        if isinstance(payload, (bytes, bytearray)):
            fh.write(payload)
        elif isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=2)


def build_repo(root: str) -> str:
    """临时仓库：backend/app 两个源文件、data 下的合成 c5、resources、eval 目录。"""
    write(os.path.join(root, "backend", "app", "main.py"), "APP = 'fake'\n")
    write(os.path.join(root, "backend", "app", "schemas.py"), "SCHEMA = 'fake'\n")
    os.makedirs(os.path.join(root, "eval"), exist_ok=True)

    case = {
        "cases": [
            {
                "id": "c5",
                "title": "合成留出案例",
                "customer_text": SYNTHETIC_TEXT,
                "spec": dict(SYNTHETIC_SPEC),
                "utterances": [
                    {
                        "id": "u1",
                        "text": "希望版型宽松",
                        "mappings": [{"field": "松弛余量", "status": "已落实", "note": "合成标注"}],
                    },
                    {
                        "id": "u2",
                        "text": "领子别太高",
                        "mappings": [{"field": "领型", "status": "被改写", "note": "合成标注"}],
                    },
                ],
            }
        ]
    }
    write(os.path.join(root, "data", "cases.json"), case)
    write(os.path.join(root, "data", "resources.json"), {"patterns": [{"id": "S1"}, {"id": "P1"}]})
    return root


def approval_payload(root: str, **overrides):
    snapshot = d3.backend_snapshot(os.path.join(root, "backend", "app"))
    record = {
        "decision": "approved",
        "scope": "scheme_freeze_and_single_holdout",
        "scheme_frozen": True,
        "holdout_authorized": True,
        "user_message": "合成测试授权：允许冻结后对留出集跑一次（非真实授权）。",
        "backend_aggregate_sha256": snapshot["aggregate_sha256"],
        "model": MODEL,
        "recorded_at": "2025-01-01T00:00:00",
    }
    record.update(overrides)
    return record


def write_approval(root: str, **overrides) -> str:
    path = os.path.join(root, "orchestration", "approvals", "freeze.json")
    write(path, approval_payload(root, **overrides))
    return path


def health_ok(**overrides):
    health = {"ok": True, "mock_mode": False, "llm_model": MODEL, "image_provider": "none", "vision_ok": False}
    health.update(overrides)
    return {
        "method": "GET", "url": "http://fake/api/health", "at": d3.now_iso(),
        "outcome": d3.OUTCOME_SUCCESS, "http_status": 200, "elapsed_ms": 3.0,
        "error": None, "raw": "", "raw_truncated": False, "parsed": health,
    }


def session_payload(root: str):
    """按临时 c5 的标准答案合成一份自洽的 Session 返回（字段全部由 fixture 生成）。"""
    gold = d3.load_holdout_gold(os.path.join(root, "data"))["c5"]
    text = gold["text"]
    utterances = []
    for utt in gold["utterances"]:
        start = text.index(utt["text"])
        utterances.append(
            {
                "id": utt["id"],
                "text": utt["text"],
                "start": start,
                "end": start + len(utt["text"]),
                "type": "合成",
                "strength": "软偏好",
                "status": utt["status"],
                "mappings": utt["mappings"],
                "resolved_by": None,
            }
        )
    fields = {}
    for field in FIELDS:
        fields[field] = {
            "value": SYNTHETIC_SPEC[field],
            "resource_ids": [],
            "utterance_ids": [utterances[0]["id"]],
            "origin": "已有方案",
            "confirmed": False,
        }
    return {
        "id": "fake-session",
        "title": "合成留出案例",
        "mode": "审计已有方案",
        "source": "llm",
        "created_at": "2025-01-01T00:00:01",
        "input": {"text": text, "existing_spec": dict(SYNTHETIC_SPEC), "sample_id": "c5"},
        "utterances": utterances,
        "spec_versions": [{"version": 1, "created_at": "2025-01-01T00:00:01", "reason": "审计", "fields": fields}],
        "questions": [],
        "designer_items": [],
        "skipped": [],
        "ledger": [],
        "reference": None,
        "images": [],
    }


def post_success(root: str, ms: float = 17.5):
    return {
        "method": "POST", "url": "http://fake/api/sessions", "at": d3.now_iso(),
        "outcome": d3.OUTCOME_SUCCESS, "http_status": 200, "elapsed_ms": ms,
        "error": None, "raw": "", "raw_truncated": False, "parsed": session_payload(root),
    }


def post_with_session(session, ms: float = 17.5):
    return {
        "method": "POST", "url": "http://fake/api/sessions", "at": d3.now_iso(),
        "outcome": d3.OUTCOME_SUCCESS, "http_status": 200, "elapsed_ms": ms,
        "error": None, "raw": "", "raw_truncated": False, "parsed": session,
    }


MISSING = object()


def session_with_source(root: str, source):
    """返回一份合成 Session；source 传 MISSING 时删除该字段。"""
    payload = session_payload(root)
    if source is MISSING:
        payload.pop("source", None)
    else:
        payload["source"] = source
    return payload


def post_unknown(error: str = "合成超时：请求状态未知"):
    return {
        "method": "POST", "url": "http://fake/api/sessions", "at": d3.now_iso(),
        "outcome": d3.OUTCOME_UNKNOWN, "http_status": None, "elapsed_ms": 60000.0,
        "error": error, "raw": "", "raw_truncated": False, "parsed": None,
    }


class FakeApi:
    def __init__(self, post_factory, health_factory=health_ok):
        self.post_factory = post_factory
        self.health_factory = health_factory
        self.gets = []
        self.posts = []

    def get(self, url):
        self.gets.append(url)
        return self.health_factory()

    def post(self, url, payload):
        self.posts.append({"url": url, "payload": payload})
        return self.post_factory()


class SpyLoader:
    """记录是否被调用；调用即说明门禁没有拦住。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, data_dir):
        self.calls += 1
        raise AssertionError("门禁未通过时不得读取 data/ 中的留出输入")


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="yinuo-d3-test-")
        build_repo(self.root)
        self.spy = SpyLoader()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make_runner(self, api: FakeApi, approval_path=None, gold_loader=None, resource_loader=None):
        return d3.HoldoutRunner(
            repo_root=self.root,
            approval_path=approval_path,
            base_url="http://fake",
            http_get=api.get,
            http_post=api.post,
            gold_loader=gold_loader or self.spy,
            resource_loader=resource_loader or (lambda data_dir: {"S1", "P1"}),
            logger=lambda msg: None,
        )

    def real_runner(self, api: FakeApi, approval_path):
        """测试成功/失败路径时用真实的数据加载（读取的是临时合成的 data/）。"""
        return self.make_runner(
            api,
            approval_path=approval_path,
            gold_loader=d3.load_holdout_gold,
            resource_loader=d3.load_resource_ids,
        )

    def latch_path(self) -> str:
        return os.path.join(self.root, "eval", "runs", "holdout-once", d3.LATCH_NAME)

    def report_path(self) -> str:
        return os.path.join(self.root, "eval", "reports", d3.REPORT_NAME)


# ---------------------------------------------------------------------------
# 1. 授权缺失
# ---------------------------------------------------------------------------

class TestApprovalGate(BaseCase):
    def test_missing_approval_refuses_without_reading_data(self):
        api = FakeApi(post_success)
        runner = self.make_runner(api)  # 默认路径下没有 freeze.json
        with self.assertRaises(d3.GateRefusal):
            runner.execute()
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))
        self.assertFalse(os.path.exists(self.report_path()))

    def test_dry_run_missing_approval_raises_and_no_side_effects(self):
        api = FakeApi(post_success)
        runner = self.make_runner(api)
        with self.assertRaises(d3.GateRefusal):
            runner.dry_run()
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_missing_field_rejected(self):
        for field in ("user_message", "backend_aggregate_sha256", "model", "recorded_at"):
            with self.subTest(field=field):
                overrides = {field: ""}
                if field in ("scheme_frozen", "holdout_authorized"):
                    overrides[field] = False
                path = write_approval(self.root, **overrides)
                api = FakeApi(post_success)
                with self.assertRaises(d3.GateRefusal):
                    self.make_runner(api, approval_path=path).execute()
                self.assertEqual(api.posts, [])
                self.assertEqual(self.spy.calls, 0)

    def test_not_frozen_or_not_authorized_rejected(self):
        for overrides in ({"scheme_frozen": False}, {"holdout_authorized": False}, {"decision": "pending"}):
            with self.subTest(overrides=overrides):
                path = write_approval(self.root, **overrides)
                api = FakeApi(post_success)
                with self.assertRaises(d3.GateRefusal):
                    self.make_runner(api, approval_path=path).execute()
                self.assertEqual(api.posts, [])

    def test_d1_approval_path_never_accepted(self):
        path = os.path.join(self.root, "orchestration", "approvals", "d1.json")
        write(path, approval_payload(self.root))
        api = FakeApi(post_success)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])

    def test_d1_record_cannot_stand_in_for_freeze(self):
        """即使 d1.json 里有 scheme_frozen/holdout_authorized，也不得据此推断冻结。"""
        path = os.path.join(self.root, "orchestration", "approvals", "d1.json")
        write(path, approval_payload(self.root, scope="d1_dataset_only"))
        with self.assertRaises(d3.GateRefusal):
            d3.check_approval(path)


# ---------------------------------------------------------------------------
# 2. 代码快照
# ---------------------------------------------------------------------------

class TestBackendHashGate(BaseCase):
    def test_wrong_hash_rejected_before_any_request(self):
        path = write_approval(self.root, backend_aggregate_sha256="a" * 64)
        api = FakeApi(post_success)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_code_change_after_freeze_rejected(self):
        path = write_approval(self.root)
        # 冻结后改动后端源文件 → 快照变化
        write(os.path.join(self.root, "backend", "app", "main.py"), "APP = 'changed'\n")
        api = FakeApi(post_success)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)

    def test_snapshot_ignores_pycache(self):
        snap_before = d3.backend_snapshot(os.path.join(self.root, "backend", "app"))
        write(os.path.join(self.root, "backend", "app", "__pycache__", "main.cpython-311.pyc"), b"\x00")
        snap_after = d3.backend_snapshot(os.path.join(self.root, "backend", "app"))
        self.assertEqual(snap_before["aggregate_sha256"], snap_after["aggregate_sha256"])

    def test_json_sources_included_and_match_d2_algorithm(self):
        rules = os.path.join(self.root, "backend", "app", "media_rules.json")
        write(rules, {"media_rule": "A"})
        snap = d3.backend_snapshot(os.path.join(self.root, "backend", "app"))
        self.assertEqual(snap["file_count"], 3)  # 两个 .py + 一个 .json
        self.assertIn("backend/app/media_rules.json", snap["files"])
        self.assertEqual(snap["files"]["backend/app/media_rules.json"], d3.sha256_file(rules))
        expected = hashlib.sha256(json.dumps(snap["files"], sort_keys=True).encode()).hexdigest()
        self.assertEqual(snap["aggregate_sha256"], expected)

    def test_json_change_changes_hash(self):
        rules = os.path.join(self.root, "backend", "app", "knowledge_attrs.json")
        write(rules, {"attr": "A"})
        before = d3.backend_snapshot(os.path.join(self.root, "backend", "app"))["aggregate_sha256"]
        write(rules, {"attr": "B"})
        after = d3.backend_snapshot(os.path.join(self.root, "backend", "app"))["aggregate_sha256"]
        self.assertNotEqual(before, after)

    def test_json_change_after_freeze_rejected(self):
        path = write_approval(self.root)
        rules = os.path.join(self.root, "backend", "app", "media_rules.json")
        self.assertFalse(os.path.exists(rules))
        write(rules, {"media_rule": "frozen 后新增"})
        api = FakeApi(post_success)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertFalse(os.path.exists(self.latch_path()))


# ---------------------------------------------------------------------------
# 3. health 门禁
# ---------------------------------------------------------------------------

class TestHealthGate(BaseCase):
    def test_mock_mode_rejected(self):
        path = write_approval(self.root)
        api = FakeApi(post_success, health_factory=lambda: health_ok(mock_mode=True))
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_model_mismatch_rejected(self):
        path = write_approval(self.root)
        api = FakeApi(post_success, health_factory=lambda: health_ok(llm_model="another-model"))
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_missing_mock_mode_rejected(self):
        path = write_approval(self.root)
        record = health_ok()
        record["parsed"].pop("mock_mode", None)
        api = FakeApi(post_success, health_factory=lambda: record)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_string_false_mock_mode_rejected(self):
        path = write_approval(self.root)
        api = FakeApi(post_success, health_factory=lambda: health_ok(mock_mode="False"))
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).execute()
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))


# ---------------------------------------------------------------------------
# 4. 成功路径：仅一次调用 + latch + 报告
# ---------------------------------------------------------------------------

class TestSingleRun(BaseCase):
    def test_success_runs_exactly_one_post_and_writes_report(self):
        path = write_approval(self.root)
        api = FakeApi(lambda: post_success(self.root))
        runner = self.real_runner(api, path)
        code = runner.execute()
        self.assertEqual(code, d3.EXIT_OK)
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(len(api.gets), 1)
        self.assertTrue(os.path.exists(self.latch_path()))

        payload = api.posts[0]["payload"]
        self.assertTrue(set(payload.keys()) <= d3.ALLOWED_PAYLOAD_KEYS)
        self.assertEqual(payload["sample_id"], "c5")
        self.assertNotIn("utterances", payload)
        self.assertNotIn("expected_issues", payload)

        with open(self.report_path(), "r", encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("问题句检出 1/1", report)
        self.assertIn("误报 0 次", report)
        self.assertIn("1 个案例", report)

        with open(os.path.join(self.root, "eval", "runs", "holdout-once", "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["session_meta"]["source"], "llm")
        self.assertEqual(result["post"]["elapsed_ms"], 17.5)

    def test_payload_strips_answers(self):
        path = write_approval(self.root)
        api = FakeApi(lambda: post_success(self.root))
        self.real_runner(api, path).execute()
        serialized = json.dumps(api.posts[0]["payload"], ensure_ascii=False)
        self.assertNotIn("合成标注", serialized)  # mappings 的 note
        self.assertNotIn("被改写", serialized)
        self.assertNotIn("已落实", serialized)

    def test_second_run_after_success_refused(self):
        path = write_approval(self.root)
        api = FakeApi(lambda: post_success(self.root))
        self.real_runner(api, path).execute()
        self.assertEqual(len(api.posts), 1)

        api2 = FakeApi(lambda: post_success(self.root))
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api2, approval_path=path).execute()
        self.assertEqual(api2.posts, [])
        self.assertEqual(api2.gets, [])


# ---------------------------------------------------------------------------
# 5. 超时/未知：latch 保留、不重试、报告不称通过
# ---------------------------------------------------------------------------

class TestUnknownOutcome(BaseCase):
    def test_timeout_keeps_latch_and_never_retries(self):
        path = write_approval(self.root)
        api = FakeApi(post_unknown)
        runner = self.real_runner(api, path)
        with self.assertRaises(d3.RunFailure):
            runner.execute()
        self.assertEqual(len(api.posts), 1)
        self.assertTrue(os.path.exists(self.latch_path()))

        with open(os.path.join(self.root, "eval", "runs", "holdout-once", "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["post"]["outcome"], d3.OUTCOME_UNKNOWN)

        with open(self.report_path(), "r", encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("本次留出集运行**失败**", report)
        self.assertIn("不构成任何“通过”结论", report)
        self.assertNotIn("拆句召回率", report)

        # 之后再跑一律拒绝，且不发请求
        api2 = FakeApi(lambda: post_success(self.root))
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api2, approval_path=path).execute()
        self.assertEqual(api2.posts, [])

    def test_definite_failure_also_keeps_latch(self):
        path = write_approval(self.root)
        record = post_unknown("合成 HTTP 500")
        record["outcome"] = d3.OUTCOME_DEFINITE
        record["http_status"] = 500
        api = FakeApi(lambda: record)
        with self.assertRaises(d3.RunFailure):
            self.real_runner(api, path).execute()
        self.assertEqual(len(api.posts), 1)
        self.assertTrue(os.path.exists(self.latch_path()))


# ---------------------------------------------------------------------------
# 6. dry-run 正常路径
# ---------------------------------------------------------------------------

class TestDryRun(BaseCase):
    def test_dry_run_never_requests_or_loads(self):
        path = write_approval(self.root)
        api = FakeApi(post_success)
        runner = self.make_runner(api, approval_path=path)
        self.assertEqual(runner.dry_run(), d3.EXIT_OK)
        self.assertEqual(api.gets, [])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.spy.calls, 0)
        self.assertFalse(os.path.exists(self.latch_path()))

    def test_dry_run_refuses_when_latch_exists(self):
        path = write_approval(self.root)
        os.makedirs(os.path.dirname(self.latch_path()), exist_ok=True)
        with open(self.latch_path(), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        api = FakeApi(post_success)
        with self.assertRaises(d3.GateRefusal):
            self.make_runner(api, approval_path=path).dry_run()
        self.assertEqual(api.gets, [])


# ---------------------------------------------------------------------------
# 7. main 入口的退出码（不真正联网）
# ---------------------------------------------------------------------------

class TestMainExitCodes(BaseCase):
    def test_main_dry_run_refused_without_approval(self):
        code = d3.main(["--dry-run", "--repo-root", self.root, "--approval",
                        os.path.join(self.root, "orchestration", "approvals", "freeze.json")])
        self.assertEqual(code, d3.EXIT_REFUSED)

    def test_main_execute_refused_without_approval(self):
        code = d3.main(["--execute", "--repo-root", self.root, "--approval",
                        os.path.join(self.root, "orchestration", "approvals", "freeze.json"),
                        "--base-url", "http://127.0.0.1:1"])
        self.assertEqual(code, d3.EXIT_REFUSED)


if __name__ == "__main__":
    unittest.main(verbosity=2)

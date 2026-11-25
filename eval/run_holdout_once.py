#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣诺 D3：留出集（案例五）一次性评测脚本。

设计原则（必须逐条守住）：
1. 默认是 dry-run：不发任何 HTTP 请求、不读取 `data/` 中的任何案例内容（自然也不碰 c5）。
   真正的运行必须显式加 `--execute`。
2. 运行前先过门禁，全部通过才加载留出输入：
   - 用户方案冻结授权文件（默认 `orchestration/approvals/freeze.json`）必须存在且字段齐全；
   - 实际 `backend/app/**/*.py` 与 `*.json` 源文件快照的聚合 SHA256 必须与授权记录中的一致；
   - `GET /api/health` 必须非 MOCK（`mock_mode` 明确为布尔 false）、且大模型名与授权记录一致；
   - 返回的 Session 必须 `source=llm`（示例模式或其他来源一律判失败）。
   任何一项不满足即拒绝，且**不创建 latch、不读取留出输入、不发 POST**。
3. 首次 `POST /api/sessions` 之前，以 `O_CREAT|O_EXCL` 原子写 durable latch。
   latch 一旦存在，之后任何运行都拒绝，无论首次请求成功、失败还是超时；未知结果绝不自动重试。
4. 只发**一次** `POST /api/sessions`（审计案例五），不跑 baseline、不重复 3 次。
   原始请求、响应、失败、耗时、会话 source、代码快照全部保留到 `eval/runs/holdout-once/`。
5. 计分复用 D0 的纯函数 `eval/run_eval.py`（只导入纯函数，不导入后端代码）。
6. 单独报告写到 `eval/reports/d3_留出集报告.md`；运行失败时报告只写失败，绝不写成“通过”。

本 AI 不执行脚本；实际运行与验收由 GPT 总控完成。离线测试见 `eval/test_holdout_once.py`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_eval  # noqa: E402  D0 的纯评分函数

SCRIPT_VERSION = "eval-d3-1.0"

HOLDOUT_CASE_ID = "c5"

# 授权文件必须齐全的字段（缺任何一项即拒绝）
REQUIRED_APPROVAL_FIELDS = (
    "decision",
    "scope",
    "scheme_frozen",
    "holdout_authorized",
    "user_message",
    "backend_aggregate_sha256",
    "model",
    "recorded_at",
)
EXPECTED_DECISION = "approved"
EXPECTED_SCOPE = "scheme_freeze_and_single_holdout"

# 请求体只允许这些键（不带任何标准答案信息）
ALLOWED_PAYLOAD_KEYS = {"text", "existing_spec", "sample_id", "title"}

OUTCOME_SUCCESS = "success"
OUTCOME_DEFINITE = "definite_failure"
OUTCOME_UNKNOWN = "unknown"

# 退出码
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 4          # 门禁拒绝：未发 POST、未读留出输入
EXIT_RUN_FAILED = 5       # 已发 POST 但结果失败/未知：latch 保留，不对外称通过

REPORT_NAME = "d3_留出集报告.md"
LATCH_NAME = "HOLDOUT_LATCH.json"


class GateRefusal(RuntimeError):
    """门禁不满足：拒绝继续，且不做任何请求、不读取留出输入。"""


class RunFailure(RuntimeError):
    """已经发过请求但未取得可用结果（失败或超时未知）。latch 已保留。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def default_repo_root() -> str:
    return os.path.dirname(HERE)


# ---------------------------------------------------------------------------
# 代码快照：backend/app 源文件的聚合 SHA256
# ---------------------------------------------------------------------------

def backend_snapshot(backend_app_dir: str) -> Dict[str, Any]:
    """对 backend/app 下所有 .py 与 .json 文件做确定性聚合哈希。

    为什么要包含 .json：知识属性、媒体规则等 JSON 文件同样决定后端行为，
    冻结后改动它们和改代码一样必须被发现。

    算法（与 D2 已验收快照一致）：
    - files = {"backend/app/相对路径": 文件 SHA256, ...}，键为相对仓库根的 POSIX 路径；
    - aggregate = sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()。
    算法写进报告与 runbook，保证可复现。
    """
    if not os.path.isdir(backend_app_dir):
        raise GateRefusal(f"找不到后端源码目录：{backend_app_dir}，无法核对代码快照。")

    abs_app = os.path.abspath(backend_app_dir)
    repo_root = os.path.dirname(os.path.dirname(abs_app))
    files: Dict[str, str] = {}
    for root, dirs, names in os.walk(abs_app):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if not (name.endswith(".py") or name.endswith(".json")):
                continue
            full = os.path.abspath(os.path.join(root, name))
            rel_repo = os.path.relpath(full, repo_root).replace(os.sep, "/")
            files[rel_repo] = sha256_file(full)
    files = dict(sorted(files.items()))

    if not files:
        raise GateRefusal(f"{backend_app_dir} 下没有 .py/.json 源文件，无法核对代码快照。")

    aggregate = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {
        "algorithm": "sha256(json.dumps({repo_rel_path: file_sha256}, sort_keys=True))",
        "backend_app_dir": backend_app_dir,
        "file_count": len(files),
        "files": files,
        "aggregate_sha256": aggregate,
    }


# ---------------------------------------------------------------------------
# 授权门禁
# ---------------------------------------------------------------------------

def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_approval(approval_path: str) -> Dict[str, Any]:
    """读取并校验用户方案冻结授权文件。任何缺失或类型不符都拒绝。"""
    abs_path = os.path.abspath(approval_path)
    if os.path.basename(abs_path).lower() == "d1.json":
        raise GateRefusal(
            "拒绝：D3 只认用户方案冻结授权（freeze.json），不允许用 D1 数据审批记录推断冻结。"
        )
    if not os.path.isfile(abs_path):
        raise GateRefusal(
            f"拒绝运行：找不到用户方案冻结授权文件 {abs_path}（默认 orchestration/approvals/freeze.json）。"
            "本机尚未冻结，必须先由项目负责人写出明确授权后才能跑留出集。"
        )

    try:
        record = read_json(abs_path)
    except (OSError, ValueError) as exc:
        raise GateRefusal(f"拒绝运行：授权文件无法解析（{exc}）。")
    if not isinstance(record, dict):
        raise GateRefusal("拒绝运行：授权文件顶层不是 JSON 对象。")

    missing = [key for key in REQUIRED_APPROVAL_FIELDS if key not in record]
    if missing:
        raise GateRefusal(f"拒绝运行：授权文件缺少字段：{'、'.join(missing)}。")

    if record.get("decision") != EXPECTED_DECISION:
        raise GateRefusal(f"拒绝运行：授权 decision={record.get('decision')!r}，必须为 {EXPECTED_DECISION!r}。")
    if record.get("scope") != EXPECTED_SCOPE:
        raise GateRefusal(f"拒绝运行：授权 scope={record.get('scope')!r}，必须为 {EXPECTED_SCOPE!r}。")
    if record.get("scheme_frozen") is not True:
        raise GateRefusal("拒绝运行：授权未明确 scheme_frozen=true（方案尚未冻结）。")
    if record.get("holdout_authorized") is not True:
        raise GateRefusal("拒绝运行：授权未明确 holdout_authorized=true（未授权跑留出集）。")
    if not _is_nonempty_str(record.get("user_message")):
        raise GateRefusal("拒绝运行：授权缺少非空的 user_message（必须引用项目负责人的明确指令）。")
    if not _is_nonempty_str(record.get("model")):
        raise GateRefusal("拒绝运行：授权缺少 model（须写明冻结时使用的大模型名）。")
    if not _is_nonempty_str(record.get("recorded_at")):
        raise GateRefusal("拒绝运行：授权缺少 recorded_at（授权记录时间）。")

    approved_hash = record.get("backend_aggregate_sha256")
    if not _is_nonempty_str(approved_hash) or len(approved_hash.strip()) != 64:
        raise GateRefusal("拒绝运行：授权缺少合法的 backend_aggregate_sha256（应为 64 位十六进制）。")
    approved_hash = approved_hash.strip().lower()
    try:
        int(approved_hash, 16)
    except ValueError:
        raise GateRefusal("拒绝运行：backend_aggregate_sha256 不是合法的十六进制字符串。")

    return {
        "path": abs_path,
        "sha256": sha256_file(abs_path),
        "record": record,
        "approved_hash": approved_hash,
        "model": record["model"].strip(),
        "user_message": record["user_message"].strip(),
        "recorded_at": record["recorded_at"].strip(),
    }


def check_backend_hash(approval: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    actual = snapshot.get("aggregate_sha256")
    if actual != approval["approved_hash"]:
        raise GateRefusal(
            "拒绝运行：实际 backend/app 源文件快照与批准的不一致"
            f"（批准 {approval['approved_hash'][:12]}…，实际 {str(actual)[:12]}…）。"
            "冻结后代码被改动，必须先由项目负责人重新冻结并授权。"
        )


# ---------------------------------------------------------------------------
# HTTP（标准库；单次调用、不重试；失败分类与 D2 一致）
# ---------------------------------------------------------------------------

def http_request(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 180.0,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": f"yinuo-eval-d3/{SCRIPT_VERSION}"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    record: Dict[str, Any] = {
        "method": method,
        "url": url,
        "at": now_iso(),
        "outcome": OUTCOME_UNKNOWN,
        "http_status": None,
        "elapsed_ms": None,
        "error": None,
        "raw": "",
        "raw_truncated": False,
        "parsed": None,
    }
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", "replace")
        record["http_status"] = status
        record["raw"] = body[:20000]
        record["raw_truncated"] = len(body) > 20000
        try:
            record["parsed"] = json.loads(body)
        except ValueError:
            record["outcome"] = OUTCOME_DEFINITE
            record["error"] = f"返回内容不是 JSON：{body[:300]}"
            return record
        record["outcome"] = OUTCOME_SUCCESS
        return record
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        record["http_status"] = exc.code
        record["raw"] = body[:20000]
        record["raw_truncated"] = len(body) > 20000
        record["outcome"] = OUTCOME_DEFINITE
        record["error"] = f"HTTP {exc.code}：{body[:600]}"
        try:
            record["parsed"] = json.loads(body)
        except ValueError:
            pass
        return record
    except urllib.error.URLError as exc:
        record["outcome"] = OUTCOME_UNKNOWN
        record["error"] = f"无法访问（请求状态未知，不自动重试）：{exc}"
        return record
    except (TimeoutError, OSError) as exc:
        record["outcome"] = OUTCOME_UNKNOWN
        record["error"] = f"网络异常或超时（请求状态未知，不自动重试）：{exc}"
        return record
    finally:
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)


# ---------------------------------------------------------------------------
# 门禁通过后才允许的留出集加载
# ---------------------------------------------------------------------------

def load_holdout_gold(data_dir: str) -> Dict[str, Any]:
    """只加载留出案例 c5 的标准答案。调用点必须在所有门禁（含 latch）之后。"""
    try:
        gold = run_eval.load_gold(data_dir, [HOLDOUT_CASE_ID])
    except run_eval.EvalError as exc:
        raise RunFailure(f"留出集标准答案加载失败：{exc}")
    if HOLDOUT_CASE_ID not in gold:
        raise RunFailure("留出集标准答案中找不到案例五（c5）。")
    item = gold[HOLDOUT_CASE_ID]
    if not item.get("text") or not item.get("utterances"):
        raise RunFailure("留出集标准答案不完整（缺少客户口述或拆句）。")
    return gold


def load_resource_ids(data_dir: str) -> set:
    try:
        return run_eval.load_resource_ids(data_dir)
    except run_eval.EvalError as exc:
        raise RunFailure(f"资源库加载失败：{exc}")


def build_payload(gold: Dict[str, Any]) -> Dict[str, Any]:
    item = gold[HOLDOUT_CASE_ID]
    payload = {
        "text": item["text"],
        "existing_spec": run_eval.normalize_existing_spec(item.get("spec")),
        "sample_id": HOLDOUT_CASE_ID,
        "title": item.get("title") or "",
    }
    # 硬门禁：请求体只能是输入字段，不得夹带任何标准答案
    extra = set(payload.keys()) - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise RunFailure(f"请求体出现非输入字段：{sorted(extra)}，拒绝发送。")
    if payload["sample_id"] != HOLDOUT_CASE_ID:
        raise RunFailure("请求体的 sample_id 不是留出案例 c5，拒绝发送。")
    if not isinstance(payload.get("existing_spec"), dict):
        raise RunFailure("请求体缺少已有方案（existing_spec），拒绝发送。")
    for field, value in payload["existing_spec"].items():
        if not isinstance(value, str):
            raise RunFailure(f"已有方案字段 {field} 不是字符串，拒绝发送。")
    return payload


# ---------------------------------------------------------------------------
# 运行器（可注入 HTTP / 数据加载，便于离线测试）
# ---------------------------------------------------------------------------

class HoldoutRunner:
    def __init__(
        self,
        *,
        repo_root: str,
        approval_path: Optional[str] = None,
        base_url: str = "http://127.0.0.1:8100",
        timeout: float = 180.0,
        http_get=None,
        http_post=None,
        gold_loader=None,
        resource_loader=None,
        logger=None,
    ):
        self.repo_root = os.path.abspath(repo_root)
        self.eval_dir = os.path.join(self.repo_root, "eval")
        self.approval_path = approval_path or os.path.join(
            self.repo_root, "orchestration", "approvals", "freeze.json"
        )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.backend_app_dir = os.path.join(self.repo_root, "backend", "app")
        self.data_dir = os.path.join(self.repo_root, "data")
        self.runs_dir = os.path.join(self.eval_dir, "runs", "holdout-once")
        self.reports_dir = os.path.join(self.eval_dir, "reports")
        self.latch_path = os.path.join(self.runs_dir, LATCH_NAME)
        self.report_path = os.path.join(self.reports_dir, REPORT_NAME)

        self._http_get = http_get or (lambda url: http_request("GET", url, None, self.timeout))
        self._http_post = http_post or (
            lambda url, payload: http_request("POST", url, payload, self.timeout)
        )
        self._gold_loader = gold_loader or load_holdout_gold
        self._resource_loader = resource_loader or load_resource_ids
        self._log = logger or (lambda msg: print(msg, file=sys.stderr))

        self.calls: List[Dict[str, Any]] = []

    # -- 门禁 -------------------------------------------------------------

    def _check_latch_absent(self) -> None:
        if os.path.exists(self.latch_path):
            raise GateRefusal(
                f"拒绝运行：一次性 latch 已存在（{self.latch_path}）。"
                "留出集只允许运行一次，无论上次成功、失败还是超时，都不再重复。"
            )

    def _create_latch(self, approval: Dict[str, Any], snapshot: Dict[str, Any], health: Dict[str, Any]) -> Dict[str, Any]:
        os.makedirs(self.runs_dir, exist_ok=True)
        payload = {
            "latch": True,
            "created_at": now_iso(),
            "pid": os.getpid(),
            "case_id": HOLDOUT_CASE_ID,
            "base_url": self.base_url,
            "model": approval["model"],
            "approval_path": approval["path"],
            "approval_sha256": approval["sha256"],
            "backend_aggregate_sha256": snapshot["aggregate_sha256"],
            "health": {
                "mock_mode": health.get("mock_mode"),
                "llm_model": health.get("llm_model"),
            },
            "status": "attempt_started",
            "note": "首次 POST 之前原子创建；之后任何运行都拒绝，即使请求超时或结果未知。",
        }
        try:
            fd = os.open(self.latch_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise GateRefusal(
                f"拒绝运行：一次性 latch 已存在（{self.latch_path}）。留出集只允许运行一次。"
            )
        try:
            os.write(fd, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        fsync_dir(self.runs_dir)
        return payload

    # -- dry-run ----------------------------------------------------------

    def dry_run(self) -> int:
        """只检查不需要碰留出输入、也不需要发请求的门禁。"""
        approval = check_approval(self.approval_path)
        self._check_latch_absent()
        snapshot = backend_snapshot(self.backend_app_dir)
        check_backend_hash(approval, snapshot)
        print("dry-run 通过：授权文件齐全、代码快照与批准一致、latch 尚不存在。", file=sys.stderr)
        print("dry-run 未发送任何 HTTP 请求，未读取 data/ 中任何案例内容。", file=sys.stderr)
        print(f"授权中记录的模型：{approval['model']}；由 --execute 运行时再核对 /api/health。", file=sys.stderr)
        return EXIT_OK

    # -- 正式运行 ---------------------------------------------------------

    def execute(self) -> int:
        # 门禁 1：授权文件
        approval = check_approval(self.approval_path)
        # 门禁 2：latch 尚未使用（先查一次，避免无谓请求）
        self._check_latch_absent()
        # 门禁 3：代码快照
        snapshot = backend_snapshot(self.backend_app_dir)
        check_backend_hash(approval, snapshot)

        # 门禁 4：health 必须非 mock 且模型一致
        health_record = self._http_get(self.base_url + "/api/health")
        self.calls.append(health_record)
        if health_record.get("outcome") != OUTCOME_SUCCESS:
            raise GateRefusal(f"拒绝运行：/api/health 不可用（{health_record.get('error')}）。")
        health = health_record.get("parsed")
        if not isinstance(health, dict):
            raise GateRefusal("拒绝运行：/api/health 返回不是 JSON 对象。")
        if health.get("mock_mode") is not False:
            raise GateRefusal(
                "拒绝运行：/api/health 的 mock_mode 必须明确为布尔 false"
                f"（实际 {health.get('mock_mode')!r}）；缺失、字符串 \"False\" 或为真都不通过。"
            )
        if str(health.get("llm_model")) != approval["model"]:
            raise GateRefusal(
                "拒绝运行：后端实际模型与授权不一致"
                f"（授权 {approval['model']!r}，实际 {health.get('llm_model')!r}）。"
            )

        # 所有门禁通过：先落 durable latch，再加载留出输入
        latch = self._create_latch(approval, snapshot, health)
        os.makedirs(self.runs_dir, exist_ok=True)
        self._write_json("health.json", health)
        self._write_json("approval.json", {"path": approval["path"], "sha256": approval["sha256"],
                                           "record": approval["record"]})
        self._write_json("code_snapshot.json", {
            "backend": snapshot,
            "approved_aggregate_sha256": approval["approved_hash"],
            "runner_script": {"path": os.path.abspath(__file__), "sha256": sha256_file(os.path.abspath(__file__))},
            "run_eval": {"path": os.path.join(self.eval_dir, "run_eval.py"),
                         "sha256": sha256_file(os.path.join(self.eval_dir, "run_eval.py"))
                         if os.path.isfile(os.path.join(self.eval_dir, "run_eval.py")) else None},
        })

        try:
            gold = self._gold_loader(self.data_dir)
            valid_ids = self._resource_loader(self.data_dir)
            payload = build_payload(gold)
        except RunFailure as exc:
            self._finish_failed(latch, approval, snapshot, str(exc), post_record=None, scored=None)
            raise

        self._write_json("post_request.json", payload)

        # 唯一的一次 POST，不重试
        post_record = self._http_post(self.base_url + "/api/sessions", payload)
        self.calls.append(post_record)
        self._write_json("post_response.json", post_record)
        self._write_calls()

        if post_record.get("outcome") != OUTCOME_SUCCESS:
            self._finish_failed(
                latch, approval, snapshot,
                f"POST /api/sessions 未成功（outcome={post_record.get('outcome')}）：{post_record.get('error')}",
                post_record=post_record, scored=None,
            )
            raise RunFailure(
                f"留出集请求失败或状态未知（outcome={post_record.get('outcome')}），不重试；latch 已保留。"
            )

        session = post_record.get("parsed")
        if not isinstance(session, dict):
            self._finish_failed(
                latch, approval, snapshot, "POST 返回内容不是 JSON 对象，无法审计",
                post_record=post_record, scored=None,
            )
            raise RunFailure("留出集返回不是对象，未取得可用结果；latch 已保留。")
        source = session.get("source")
        if source != "llm":
            self._finish_failed(
                latch, approval, snapshot,
                f"会话 source={source!r}，留出集必须 source=llm（真实模型模式）",
                post_record=post_record, scored=None,
            )
            raise RunFailure(
                f"留出集会话 source={source!r} 不是 llm，结果不可用；latch 已保留。"
            )
        try:
            metrics = run_eval.evaluate_session(HOLDOUT_CASE_ID, session, gold[HOLDOUT_CASE_ID], valid_ids)
        except run_eval.EvalError as exc:
            self._finish_failed(
                latch, approval, snapshot, f"返回结果无法按标准答案计分：{exc}",
                post_record=post_record, scored=None,
            )
            raise RunFailure(f"留出集结果无法计分：{exc}；latch 已保留。")

        self._finish_success(latch, approval, snapshot, health, post_record, session, metrics)
        return EXIT_OK

    # -- 落盘 -------------------------------------------------------------

    def _write_json(self, name: str, payload: Any) -> str:
        path = os.path.join(self.runs_dir, name)
        write_json(path, payload)
        return path

    def _write_calls(self) -> None:
        os.makedirs(self.runs_dir, exist_ok=True)
        path = os.path.join(self.runs_dir, "calls.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for call in self.calls:
                slim = {k: v for k, v in call.items() if k != "parsed"}
                fh.write(json.dumps(slim, ensure_ascii=False) + "\n")

    def _finish_success(self, latch, approval, snapshot, health, post_record, session, metrics) -> None:
        spec_versions = session.get("spec_versions") or []
        result = {
            "status": "completed",
            "at": now_iso(),
            "run_ok": True,
            "metrics": metrics,
            "session_meta": {
                "session_id": session.get("id"),
                "source": session.get("source"),
                "mode": session.get("mode"),
                "created_at": session.get("created_at"),
                "version": spec_versions[-1].get("version") if spec_versions else None,
            },
            "post": {
                "outcome": post_record.get("outcome"),
                "http_status": post_record.get("http_status"),
                "elapsed_ms": post_record.get("elapsed_ms"),
            },
            "latch": latch,
        }
        self._write_json("result.json", result)
        self._write_calls()
        markdown = build_report(
            approval=approval, snapshot=snapshot, health=health,
            post_record=post_record, session=session, metrics=metrics,
            runs_dir=self.runs_dir, report_path=self.report_path,
        )
        self._write_report(markdown)
        print(build_console_summary(result), file=sys.stderr)
        print(f"报告：{self.report_path}", file=sys.stderr)

    def _finish_failed(self, latch, approval, snapshot, reason, post_record, scored) -> None:
        result = {
            "status": "failed",
            "at": now_iso(),
            "run_ok": False,
            "reason": reason,
            "metrics": scored,
            "post": None if post_record is None else {
                "outcome": post_record.get("outcome"),
                "http_status": post_record.get("http_status"),
                "elapsed_ms": post_record.get("elapsed_ms"),
                "error": post_record.get("error"),
                "source": (post_record.get("parsed") or {}).get("source")
                if isinstance(post_record.get("parsed"), dict) else None,
            },
            "latch": latch,
        }
        self._write_json("result.json", result)
        self._write_calls()
        markdown = build_failure_report(
            approval=approval, snapshot=snapshot, post_record=post_record,
            reason=reason, runs_dir=self.runs_dir, report_path=self.report_path,
        )
        self._write_report(markdown)
        print(f"留出集运行失败：{reason}", file=sys.stderr)
        print(f"报告（失败，不构成通过结论）：{self.report_path}", file=sys.stderr)

    def _write_report(self, markdown: str) -> None:
        os.makedirs(self.reports_dir, exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def _frac(n: int, d: int) -> str:
    return f"{n}/{d}（{run_eval.pct(n, d)}）"


def build_console_summary(result: Dict[str, Any]) -> str:
    m = result.get("metrics") or {}
    problem = m.get("problem") or {}
    seg = m.get("seg") or {}
    field = m.get("field") or {}
    status = m.get("status") or {}
    return "\n".join([
        "=" * 64,
        "留出集（c5）一次性运行完成（仅 1 个案例、1 次调用，无稳定性统计）",
        f"会话 source：{(result.get('session_meta') or {}).get('source')}",
        f"POST 耗时：{(result.get('post') or {}).get('elapsed_ms')} ms",
        f"拆句：召回 {_frac(seg.get('recall_num', 0), seg.get('recall_den', 0))}；"
        f"精确 {_frac(seg.get('precision_num', 0), seg.get('precision_den', 0))}",
        f"字段：召回 {_frac(field.get('recall_num', 0), field.get('recall_den', 0))}；"
        f"精确 {_frac(field.get('precision_num', 0), field.get('precision_den', 0))}",
        f"问题检出：{problem.get('detected', 0)}/{problem.get('gold_problem_count', 0)}；误报 {problem.get('fp_count', 0)} 次",
        f"状态一致：{_frac(status.get('agree', 0), status.get('paired', 0))}；"
        f"宽松一致：{_frac(status.get('loose_agree', 0), status.get('paired', 0))}",
        f"不编造违规：{len((m.get('fabrication') or {}).get('violations') or [])} 条",
        "=" * 64,
    ])


COMMON_LIMITS = (
    "- 留出集只有 1 个案例（c5），且只运行 1 次，样本量极小，不能据此推断总体能力，也没有稳定性统计。\n"
    "- 标准答案来自 `data/cases.json` 的人工整理标注（未经设计师确认），标注误差会同时计入分子与分母。\n"
    "- 拆句配对采用“最长公共子序列 / 较长者长度 ≥ 0.5”的一对一贪心配对，与人工判断可能有个别出入。\n"
    "- 字段对应只比较字段集合，不比较多件套的单品归属，也不评价字段值写得对不对。\n"
    "- 本报告只覆盖“审计已有方案”模式，不含从零生成、对照大模型等其他实验。\n"
)


def build_report(*, approval, snapshot, health, post_record, session, metrics, runs_dir, report_path) -> str:
    spec_versions = session.get("spec_versions") or []
    seg = metrics["seg"]
    field = metrics["field"]
    status = metrics["status"]
    problem = metrics["problem"]
    fabrication = metrics["fabrication"]

    lines: List[str] = []
    lines.append("# 衣诺 D3：留出集（案例五）一次性评测报告")
    lines.append("")
    lines.append(
        "**一句话结论：** 留出集案例五在本机真实模型模式下完成唯一一次 **审计已有方案** 调用，"
        f"问题句检出 {problem['detected']}/{problem['gold_problem_count']}、误报 {problem['fp_count']} 次、"
        f"不编造违规 {len(fabrication['violations'])} 条；样本仅 1 个案例、1 次调用，"
        "结果只能作为参考，不能代表总体能力。"
    )
    lines.append("")
    lines.append(
        "> 这是留出集的**唯一一次**运行，运行前已通过用户方案冻结授权、代码快照与后端健康检查三重门禁，"
        "并在首次请求前写入一次性 latch。报告中所有数字的分子分母均如实给出。"
    )
    lines.append("")

    lines.append("## 1. 授权与门禁")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 授权文件 | `{approval['path']}` |")
    lines.append(f"| 授权文件 SHA256 | `{approval['sha256']}` |")
    lines.append(f"| 判定 / 范围 | {approval['record'].get('decision')} / {approval['record'].get('scope')} |")
    lines.append(f"| 冻结 / 授权留出 | {approval['record'].get('scheme_frozen')} / {approval['record'].get('holdout_authorized')} |")
    lines.append(f"| 授权用户指令 | {approval['user_message']} |")
    lines.append(f"| 授权记录时间 | {approval['recorded_at']} |")
    lines.append(f"| 批准代码快照 SHA256 | `{approval['approved_hash']}` |")
    lines.append(f"| 实际代码快照 SHA256 | `{snapshot['aggregate_sha256']}`（{snapshot['file_count']} 个 .py/.json 文件） |")
    lines.append("| 代码快照一致 | 是 |")
    lines.append(f"| 后端 MOCK 模式 | {'是' if health.get('mock_mode') else '否'} |")
    lines.append(f"| 后端模型 | `{health.get('llm_model')}` |")
    lines.append(f"| 授权模型一致 | 是 |")
    lines.append(f"| 一次性 latch | `{os.path.join(runs_dir, LATCH_NAME)}` |")
    lines.append("")
    lines.append(
        "代码快照算法（与 D2 已验收口径一致）：对 `backend/app` 下所有 `.py` 与 `.json` 文件，"
        "以相对仓库根的 POSIX 路径为键、文件 SHA256 为值组成字典，"
        "`aggregate = sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()`。"
        "知识属性、媒体规则等 JSON 也计入——它们是后端行为的一部分。"
        "授权记录里的 `backend_aggregate_sha256` 必须与之一致，否则脚本拒绝运行。"
    )
    lines.append("")

    lines.append("## 2. 运行环境与调用")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append("| 脚本版本 | `" + SCRIPT_VERSION + "` |")
    lines.append(f"| 运行时间 | {now_iso()} |")
    lines.append(f"| 调用方式 | 单次 `POST /api/sessions`（审计已有方案，`sample_id=c5`），无重试 |")
    lines.append(f"| HTTP 结果 | {post_record.get('outcome')}（状态码 {post_record.get('http_status')}） |")
    lines.append(f"| 耗时 | {post_record.get('elapsed_ms')} 毫秒 |")
    lines.append(f"| 会话 source | `{session.get('source')}` |")
    lines.append(f"| 会话模式 | `{session.get('mode')}` |")
    lines.append(f"| 方案版本 | {spec_versions[-1].get('version') if spec_versions else '—'} |")
    lines.append(f"| 会话创建时间 | `{session.get('created_at')}` |")
    lines.append("")

    lines.append("## 3. 指标（单案例单次，全部给出分子/分母）")
    lines.append("")
    lines.append("| 指标 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 拆句召回率 | {_frac(seg['recall_num'], seg['recall_den'])} | 标准原话中被配上的比例（重叠 ≥ 0.5） |")
    lines.append(f"| 拆句精确率 | {_frac(seg['precision_num'], seg['precision_den'])} | 预测原话中配上标准答案的比例 |")
    lines.append(f"| 字段对应召回率 | {_frac(field['recall_num'], field['recall_den'])} | 只看配上的原话，标准字段集合被覆盖的比例 |")
    lines.append(f"| 字段对应精确率 | {_frac(field['precision_num'], field['precision_den'])} | 预测字段中落在标准答案里的比例 |")
    lines.append(f"| 问题句检出 | {_frac(problem['detected'], problem['gold_problem_count'])} | 标准四类问题状态的原话是否被报成问题 |")
    lines.append(f"| 问题句误报 | {problem['fp_count']} 次 | 标准为非问题状态却被报成问题的次数 |")
    lines.append(f"| 状态完全一致率 | {_frac(status['agree'], status['paired'])} | 六种状态逐句比较 |")
    lines.append(f"| 状态宽松一致率 | {_frac(status['loose_agree'], status['paired'])} | “间接体现”与“已落实”合并后的一致率（单独统计） |")
    lines.append(f"| 不编造违规 | {len(fabrication['violations'])} 条 | 原话非原文子串/位置不符、资源 id 不存在 |")
    lines.append(f"| 当前方案“无出处”栏数 | {len(fabrication['ungrounded_fields'])} 栏 | {('、'.join(fabrication['ungrounded_fields']) or '无')} |")
    lines.append("")

    lines.append("## 4. 问题句逐句核对")
    lines.append("")
    rows = problem.get("rows") or []
    if rows:
        lines.append("| 标准原话 | 标准状态 | 标准字段 | 是否配上 | 配对重叠 | 预测状态 | 结论 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in rows:
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} |".format(
                    row.get("gold_text"),
                    row.get("gold_status"),
                    "、".join(row.get("gold_fields") or []) or "—",
                    "是" if row.get("paired") else "否",
                    row.get("match_ratio"),
                    row.get("pred_status") or "—",
                    "检出" if row.get("detected") else "漏检",
                )
            )
    else:
        lines.append("本案没有标准问题句。")
    lines.append("")
    lines.append("误报清单：")
    lines.append("")
    fps = problem.get("false_positives") or []
    if fps:
        lines.append("| 标准原话 | 标准状态 | 预测原话 | 预测状态 |")
        lines.append("|---|---|---|---|")
        for fp in fps:
            lines.append(
                f"| {fp.get('gold_text')} | {fp.get('gold_status')} | {fp.get('pred_text')} | {fp.get('pred_status')} |"
            )
    else:
        lines.append("0 次误报。")
    lines.append("")

    lines.append("## 5. 不编造检查")
    lines.append("")
    violations = fabrication.get("violations") or []
    if not violations:
        lines.append("违规 0 条：原话均为原文子串且位置正确，方案与追问选项引用的资源 id 均存在于 `data/resources.json`。")
    else:
        lines.append("| 类型 | 字段/原话 | 资源 id |")
        lines.append("|---|---|---|")
        for violation in violations:
            lines.append(
                f"| {violation.get('type')} | {violation.get('field') or violation.get('utterance_id')} | "
                f"{violation.get('resource_id') or ''} |"
            )
    lines.append("")

    lines.append("## 6. 原始输出")
    lines.append("")
    lines.append(f"- 目录：`{runs_dir}/`")
    lines.append("  - `HOLDOUT_LATCH.json`：首次请求前原子写入的一次性 latch（不可复用）")
    lines.append("  - `approval.json` / `code_snapshot.json`：授权记录副本与代码快照")
    lines.append("  - `health.json`：运行前 `/api/health` 返回")
    lines.append("  - `post_request.json` / `post_response.json`：唯一一次请求的请求体与完整返回")
    lines.append("  - `calls.jsonl`：每次 HTTP 调用的地址、结果分类、状态码与耗时")
    lines.append("  - `result.json`：本次运行结论与指标明细")
    lines.append(f"- 本报告：`{report_path}`")
    lines.append("")

    lines.append("## 7. 局限（必须与数字一起引用）")
    lines.append("")
    lines.append(COMMON_LIMITS)
    lines.append("")
    return "\n".join(lines)


def build_failure_report(*, approval, snapshot, post_record, reason, runs_dir, report_path) -> str:
    lines: List[str] = []
    lines.append("# 衣诺 D3：留出集（案例五）一次性评测报告")
    lines.append("")
    lines.append(
        "**一句话结论：** 本次留出集运行**失败**，没有取得可用于对外报告的结果。"
        "按一次性运行规则，latch 已保留，**不允许重跑**；本报告不构成任何“通过”结论。"
    )
    lines.append("")
    lines.append("## 1. 授权与门禁")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 授权文件 | `{approval['path']}` |")
    lines.append(f"| 授权文件 SHA256 | `{approval['sha256']}` |")
    lines.append(f"| 批准代码快照 SHA256 | `{approval['approved_hash']}` |")
    lines.append(f"| 实际代码快照 SHA256 | `{snapshot['aggregate_sha256']}` |")
    lines.append(f"| 一次性 latch | `{os.path.join(runs_dir, LATCH_NAME)}` |")
    lines.append("")
    lines.append("## 2. 失败详情")
    lines.append("")
    lines.append(f"- 原因：{reason}")
    if post_record is not None:
        lines.append(f"- 请求结果分类：`{post_record.get('outcome')}`")
        lines.append(f"- HTTP 状态码：{post_record.get('http_status')}")
        lines.append(f"- 耗时：{post_record.get('elapsed_ms')} 毫秒")
        lines.append(f"- 错误信息：{post_record.get('error')}")
    lines.append("- 未自动重试；不会再次发起请求。")
    lines.append("")
    lines.append("## 3. 原始输出")
    lines.append("")
    lines.append(f"- 目录：`{runs_dir}/`（含 latch、请求、返回、调用记录与 `result.json`）")
    lines.append(f"- 本报告：`{report_path}`")
    lines.append("")
    lines.append("## 4. 局限")
    lines.append("")
    lines.append(COMMON_LIMITS)
    lines.append("- 本次运行失败，上述局限之外也没有任何指标可用。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="衣诺 D3 留出集（c5）一次性评测：默认 dry-run，真正运行需 --execute。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true", help="真正运行（会发 1 次 POST 并消耗一次性 latch）")
    parser.add_argument("--dry-run", action="store_true", help="只检查授权/代码快照/latch，不发请求、不读留出输入")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100", help="后端 HTTP 地址")
    parser.add_argument("--approval", default=None,
                        help="用户方案冻结授权文件，默认 <repo>/orchestration/approvals/freeze.json")
    parser.add_argument("--repo-root", default=None, help="仓库根目录，默认脚本上一级")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次 HTTP 请求超时（秒）")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and args.dry_run:
        print("参数冲突：--execute 与 --dry-run 只能二选一。", file=sys.stderr)
        return EXIT_ERROR

    repo_root = args.repo_root or default_repo_root()
    runner = HoldoutRunner(
        repo_root=repo_root,
        approval_path=args.approval,
        base_url=args.base_url,
        timeout=args.timeout,
    )

    if not args.execute:
        try:
            return runner.dry_run()
        except GateRefusal as exc:
            print(f"dry-run 被拒绝：{exc}", file=sys.stderr)
            return EXIT_REFUSED
        except Exception as exc:  # noqa: BLE001  任何异常都不应被当作通过
            print(f"dry-run 出错：{exc}", file=sys.stderr)
            return EXIT_ERROR

    try:
        return runner.execute()
    except GateRefusal as exc:
        print(f"拒绝运行：{exc}", file=sys.stderr)
        return EXIT_REFUSED
    except RunFailure as exc:
        print(f"留出集运行失败：{exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    except Exception as exc:  # noqa: BLE001
        print(f"留出集运行出现未预期错误：{exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

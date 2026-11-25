# D3 留出集（案例五）一次性评测：运行手册

本手册只描述**项目负责人宣布方案冻结之后**，由 GPT 总控执行的唯一一次留出集评测。
撰写本手册时本机**尚未冻结**：`orchestration/approvals/freeze.json` 不存在，因此现在只能写代码，
不允许跑 dry-run 之外的任何动作，更不允许跑 `--execute`。

> 重要：本目录下所有脚本都**不读取、不导入** `backend/` 代码，只通过 `http://127.0.0.1:8100` 调接口。
> 留出案例 c5 的内容只在所有门禁通过、且一次性 latch 已原子写入之后才被加载。

---

## 1. 文件

| 文件 | 作用 |
|---|---|
| `eval/run_holdout_once.py` | 一次性评测脚本。默认 dry-run；`--execute` 才真正运行。 |
| `eval/test_holdout_once.py` | 离线测试：临时目录 + 合成案例 + 假 HTTP，不联网、不含真实留出内容。 |
| `eval/HOLDOUT_RUNBOOK.md` | 本手册。 |
| `eval/runs/holdout-once/` | 运行产物：latch、授权副本、代码快照、health、请求/响应、`result.json`、`calls.jsonl`。 |
| `eval/reports/d3_留出集报告.md` | 单独报告；失败时也写，但只写失败，绝不写成“通过”。 |

---

## 2. 前置条件（缺一不可）

1. 项目负责人**明确宣布**方案冻结，之后，由总控引用该用户指令记录授权文件
   `orchestration/approvals/freeze.json`（默认路径，脚本硬编码为默认值，但可用 `--approval` 覆盖）。
2. 后端以**真实模型模式**启动在 `127.0.0.1:8100`：`MOCK_MODE=0`，且 `GET /api/health`
   返回的 `llm_model` 与授权文件里的 `model` 完全一致。
3. 冻结之后 `backend/app` 下的 `.py` 和 `.json` 文件没有任何改动（含知识属性与媒体规则）。

### 授权文件必须包含的字段

| 字段 | 要求 |
|---|---|
| `decision` | 必须等于 `approved` |
| `scope` | 必须等于 `scheme_freeze_and_single_holdout` |
| `scheme_frozen` | 必须为布尔 `true` |
| `holdout_authorized` | 必须为布尔 `true` |
| `user_message` | 非空字符串，引用项目负责人的明确指令原文 |
| `backend_aggregate_sha256` | 64 位十六进制，等于冻结时 `backend/app` 的聚合快照 SHA256 |
| `model` | 非空字符串，冻结时使用的大模型名（须与 `/api/health` 的 `llm_model` 一致） |
| `recorded_at` | 非空字符串，授权记录时间 |

缺少任何一个字段、类型不对、或 `decision`/`scope` 不匹配，脚本一律拒绝。
**`orchestration/approvals/d1.json`（D1 数据审批）永远不能代替冻结授权**：脚本遇到 `d1.json`
路径会直接拒绝，也不会从 D1 审批推断冻结状态。

### 代码快照算法（可复现）

对 `backend/app` 下所有 `.py` 和 `.json` 文件（跳过 `__pycache__`）计算 SHA256。将相对仓库的路径作为键、文件 SHA256 作为值构成 `files` 字典，再计算 `sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()`。这与 D2 保存的候选快照算法一致；该值必须等于授权记录中的 `backend_aggregate_sha256`。

总控可这样算出当前快照（仅计算，不发请求、不读 data）：

```bash
python -c "import sys; sys.path.insert(0, 'eval'); import run_holdout_once as d3; print(d3.backend_snapshot('backend/app')['aggregate_sha256'])"
```

负责人明确批准后，由总控把真实指令和输出一并记录进 `freeze.json`。**本项目负责人未宣布冻结前，不要创建 freeze.json。**

---

## 3. 执行顺序（由 GPT 总控执行）

```bash
# 1) 先跑离线测试（不联网、不读真实 data、不用真实留出内容）
python eval/test_holdout_once.py

# 2) dry-run：只校验授权文件、代码快照与 latch，不发任何 HTTP、不读取任何案例内容
python eval/run_holdout_once.py --dry-run

# 3) 真正运行（唯一一次；会消耗一次性 latch）
python eval/run_holdout_once.py --execute
```

可选参数：

- `--base-url http://127.0.0.1:8100`（默认值同上）
- `--approval <路径>`（默认 `orchestration/approvals/freeze.json`）
- `--repo-root <仓库根>`（默认脚本上一级）
- `--timeout <秒>`（默认 180）

不加 `--execute` 时脚本永远是 dry-run。`--execute` 与 `--dry-run` 同时给会被判为参数冲突。

---

## 4. 门禁与调用顺序（脚本内部严格固定）

1. 校验授权文件（字段齐全 + `decision`/`scope`/冻结/授权 + 拒绝 `d1.json`）；
2. 确认一次性 latch 不存在；
3. 计算 `backend/app` 聚合 SHA256 并与授权记录比对；
4. `GET /api/health`：必须非 MOCK，且 `llm_model` 与授权一致；
5. **以 `O_CREAT|O_EXCL` 原子写 latch**，`fsync` 文件并对目录 `fsync`；
6. 这时才加载留出案例 c5 与资源库；
7. 发出**唯一一次** `POST /api/sessions`（审计已有方案，`sample_id=c5`）；
8. 用 `eval/run_eval.py` 的纯函数计分，写 `result.json` 与报告。

任何一步失败：

- 第 1–4 步失败 → 拒绝，退出码 `4`；**不发 POST、不创建 latch、不读取留出输入**；
- 第 6 步及以后失败（含 POST 超时/未知）→ 退出码 `5`；latch 已保留，**绝不自动重试**；
- 其他意外 → 退出码 `1`。

退出码汇总：`0` 完成且有结果；`1` 意外错误；`4` 门禁拒绝；`5` 请求失败/未知（latch 已保留）。

---

## 5. 一次性 latch 规则（不可绕过）

- latch 文件：`eval/runs/holdout-once/HOLDOUT_LATCH.json`。
- 首次 POST **之前**原子创建；一旦存在，之后任何 `--execute`、任何 `--dry-run` 都拒绝。
- 无论首次请求**成功、失败还是超时**，latch 都不删除、不改写。
- 请求状态未知（超时/连接断开）**不重试**，结果原样保留在 `result.json` 的 `post.error` 里。
- 因此：真正执行前务必确认授权、模型、后端都已冻结就绪；**没有第二次机会**。

---

## 6. 运行产物

`eval/runs/holdout-once/`：

| 文件 | 内容 |
|---|---|
| `HOLDOUT_LATCH.json` | 一次性 latch（首次 POST 前写入，不可复用） |
| `approval.json` | 授权文件副本与 SHA256 |
| `code_snapshot.json` | 冻结时 `backend/app` 全量文件 SHA256、聚合 SHA256、脚本与 `run_eval.py` 的 SHA256 |
| `health.json` | 运行前 `/api/health` 返回（含 `mock_mode`、`llm_model`） |
| `post_request.json` | 唯一一次 POST 的请求体（只含口述、已有方案、`sample_id`、标题） |
| `post_response.json` | POST 的完整返回、结果分类、HTTP 状态、耗时、错误信息 |
| `calls.jsonl` | 每次 HTTP 调用的地址、结果分类、状态码、耗时（不含响应体） |
| `result.json` | 结论、指标明细、会话 `source`、方案版本 |

报告：`eval/reports/d3_留出集报告.md`。成功时给出每个指标的分子/分母；
失败时开头明确写“本次留出集运行**失败**，本报告不构成任何‘通过’结论”，且不出现任何指标章节。

---

## 7. 失败后的处理

1. **不要重跑**：latch 已存在，脚本会自动拒绝。
2. 把 `result.json`、`post_response.json` 和报告原样交给项目负责人，在报告中如实写明“未取得可用结果”。
3. 是否重新授权第二次运行由项目负责人决定；若要重跑，必须由项目负责人明确指示后，
   人工归档并移除旧 latch，并**另行记录**为第二次运行——脚本本身不会也不应该自动这么做。

---

## 8. 离线测试覆盖了什么

`python eval/test_holdout_once.py` 使用临时目录与合成案例、假 HTTP，证明：

- 缺授权文件时：不读取 data、不发任何请求、不创建 latch、不写报告；
- `d1.json` 不能冒充冻结授权（即使它写了 `scheme_frozen=true`）；
- 授权字段缺失（`user_message` / 哈希 / 模型 / 时间）时拒绝；
- 代码快照 SHA256 不符、或冻结后改代码，均拒绝；
- `MOCK_MODE=true` 拒绝；模型名与授权不一致拒绝；
- 成功路径只发 **1 次** POST，请求体不含任何答案字段；
- POST 超时（未知结果）也保留 latch，且不重试，报告不写“通过”；
- 首次运行后任何再次运行都被 latch 拒绝，且不发请求；
- dry-run 不发请求、不加载数据。

测试全部为合成数据，**不包含任何真实留出集内容或预期指标**。

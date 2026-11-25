# D2 正式评测运行手册（eval/run_formal_eval.py）

本文件只写**怎么跑、怎么读、什么不能做**。D2 的正式执行由总控在后端 M2 稳定后发起；评测组不自行发 HTTP、不调用模型。

## 0. 前置条件（缺一不可）

1. **衣诺后端**已在 `http://127.0.0.1:8100` 启动，且 `MOCK_MODE=0`。
   - `GET /api/health` 的 `mock_mode` 必须为 `false`；
   - `GET /api/health` 的 `llm_model` 必须与 `--model` 完全一致，否则 runner 直接退出（退出码 2）。
2. **通用问法服务**（OpenAI 兼容，默认 Magpie `http://127.0.0.1:3425/v1`）可用，模型名为 `auto/deepseek-v4.1-flash`。
   - 若服务需要鉴权，把 Key 放进环境变量：默认按顺序读 `MAGPIE_API_KEY`、`OPENAI_API_KEY`，也可用 `--api-key-env` 指定；
   - **Key 不写进命令行、代码、报告或 checkpoint**；报告只写“来自哪个环境变量”。
3. **D1 审批仍有效**：`orchestration/approvals/d1.json` 中 `decision=approved`，且
   `eval/datasets/injected.json` 的 SHA256 与记录里的 `dataset_sha256` 完全一致。
   数据或审批记录被改动时 runner 会拒绝运行（退出码 2）。
4. 方案**未冻结**也能跑 D2（D2 不含留出集）；留出集 c5 无论何种参数都不会被本 runner 使用。

## 1. 先做离线预检（不联网、不写 runs/）

```bash
python eval/run_formal_eval.py --dry-run
```

它会：
- 校验审批记录与注入集哈希；
- 载入开发集 5 个案例、注入集 20 组（18 个问题、5 个无问题组）；
- 逐个构造请求体并检查**没有夹带答案**（注入组只有 `customer_text` + `existing_spec`）；
- 打印计划单位数（默认 2 赛道 × 25 样本 × 3 次 = 150）。

期望退出码 0、`请求体门禁：检查 150 个，失败 0 个`。

## 2. 正式运行

```bash
# 默认：衣诺 + 对照组；开发集 + 注入集；每样本 3 次；并发 3
python eval/run_formal_eval.py \
  --base-url http://127.0.0.1:8100 \
  --magpie-url http://127.0.0.1:3425/v1 \
  --model auto/deepseek-v4.1-flash \
  --concurrency 3 \
  --timeout 210 \
  --repeats 3 \
  --baseline-max-tokens 8192
```

常用变体：

```bash
# 只跑衣诺（不调用对照组、不读 Key）
python eval/run_formal_eval.py --lanes yinuo

# 先跑开发集 1 次试水（会给正式分母留下不完整记录，建议用单独 run-id）
python eval/run_formal_eval.py --lanes yinuo --subsets dev --repeats 1 --run-id smoke-dev

# 断点恢复（复用成功项，不重复计费；状态未知的请求不会自动重试）
python eval/run_formal_eval.py --resume --run-id 20251120T101500
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--base-url` | `http://127.0.0.1:8100` | 衣诺后端（必须非 MOCK） |
| `--magpie-url` | `http://127.0.0.1:3425/v1` | 通用问法 OpenAI 兼容地址，可含 `/v1` |
| `--model` | `auto/deepseek-v4.1-flash` | 必须等于 `health.llm_model` |
| `--concurrency` | `3` | 并发单位数 |
| `--timeout` | `180` | 单次请求超时（秒） |
| `--repeats` | `3` | 每个案例/组重复次数 |
| `--lanes` | `yinuo,baseline` | 赛道 |
| `--subsets` | `dev,injected` | 子集 |
| `--resume` + `--run-id` | 关闭 | 复用成功 checkpoint；未知状态保留不重试 |
| `--api-key-env` | `MAGPIE_API_KEY,OPENAI_API_KEY` | 按顺序查找 Key 的环境变量名 |
| `--baseline-max-tokens` | `1200` | 本轮实际用 `8192`，重现本轮时显式传入，避免输出截断 |
| `--baseline-response-format` | 关闭 | 服务端支持 `response_format` 时可开 |
| `--dry-run` | 关闭 | 只做离线预检 |

退出码：`0` 全部成功；`3` 跑完但有失败（失败保留在分母）；`2` 门禁拒绝；`1` 中断或其他错误。

## 3. 输出在哪

```
eval/runs/<run-id>/
  checkpoints/<lane>__<subset>__<case>__r<repeat>.json   # 请求体、原始响应、结果分类、耗时
  health.json                                            # GET /api/health 原文
  environment.json                                       # 模型名、地址、审批哈希、重复/并发/超时
  calls.jsonl                                            # 每次网络调用一行（不含 Key）
  summary.json                                           # 全部结构化数字、失败清单
eval/reports/d2_正式评测报告.md
```

## 4. 判读口径（报告已内置，引用数字时必须一起引用）

- **问题检出分母** = 计划的问题句总数（问题句数 × 计划重复次数），**失败不剔除**；
  另有“仅成功重复”的检出率作参考。分母与分子都写明。
- **误报**：标准“已落实”被报成问题，与标准“间接体现”被报成问题，分开计数。
- **状态一致率**：六种状态逐句比较；另给“间接体现≈已落实”的宽松一致率，单独列。
- **对照组**只比较问题检出；它的未匹配主张单列为“自由发挥”，不算误报；不比较拆句/字段指标。
- **稳定性**：同一案例 3 次的问题检出与误报完全相同才算“一致”；有失败写“有失败，不能判定一致”。
- **不编造**：原话/引文虚构、资源 id 不存在单列为严重错误；“无出处”栏数单独成表。
- **样本限制**（报告第 11 节）：开发集 5 案例且开发中已见；注入集为团队自造；18 个问题、5 个无问题组，小样本。

## 5. 失败与恢复

- **确定失败**（HTTP 4xx/5xx、返回非 JSON）：可 `--resume` 重跑；
- **状态未知**（超时、连接断开）：请求可能已在服务端执行，checkpoint 保留、**不自动重试**，
  由项目负责人决定是否人工重跑（删掉对应 checkpoint 后 `--resume`）；
- 任何失败都保留在问题检出分母里，并逐条出现在报告第 10 节；
- 若 `POST /api/sessions` 返回 `source=mock`，该单位判为失败（防止把示例模式当模型模式）。

## 6. 红线

- 不要修改 `orchestration/approvals/d1.json` 或 `eval/datasets/injected.json`；改了哈希就对不上。
- 不要把注入组的 `title`、`category`、`expected_issues`、`test_injected`、`resources` 加进请求体；
  runner 有发送前门禁，但人工改脚本时同样要守。
- 不要在本 runner 里加 c5；留出集只在方案冻结后由项目负责人另行安排。
- 不要把 MOCK 模式的 D0 数字写进 D2 报告或参赛材料。
- 报告结尾的“局限”不得删减；对外口径（D4）另行挑选 3–5 条并注明样本量。
# 衣诺评测（eval/）

本目录只负责评测，不修改被测系统。被测系统通过 HTTP 调用（默认 `http://127.0.0.1:8100`），不导入后端代码。

## 文件

- `run_eval.py`：D0 评测脚手架（MOCK 模式自检）。开发集 = c1、c2、c3、c4、c6；留出集 c5 被硬性拒绝。
- `run_formal_eval.py`：**D2 正式评测 runner**。衣诺完整系统 + 同一模型的“通用问法”对照组；开发集与注入集分开统计；每样本默认重复 3 次、并发 3、可 `--resume`。
- `formal_data.py`：D2 数据与门禁（审批 SHA256、注入集规范化、请求体泄漏防护）。
- `formal_http.py`：D2 HTTP 调用与失败分类（Key 只从环境读取）。
- `formal_baseline.py`：D2 通用问法对照组（只统计问题检出）。
- `formal_report.py`：D2 汇总与中文报告。
- `test_metrics.py` / `test_formal_eval.py`：离线单元测试（不联网、不发模型请求）。
- `D2_RUNBOOK.md`：D2 运行手册（前提、命令、判读口径、失败恢复、红线）。
- `datasets/injected.json`：D1 注入问题集（20 组、18 个问题、5 个无问题组，已获项目负责人批准）。
- `review_injected.html`、`validate_injected.py`：D1 审核页与离线校验。
- `runs/<运行编号>/`：每次运行的原始输出。
- `reports/`：人类可读报告（MOCK 报告写明“仅检验脚本、不代表真实效果”）。
- `WORKLOG.md`：做了什么、怎么运行、已知问题。

## 常用命令

```bash
# D0：MOCK 模式脚手架自检（不代表真实效果）
python eval/run_eval.py --base-url http://127.0.0.1:8100 --repeats 3

# D2：离线预检（不联网）
python eval/run_formal_eval.py --dry-run

# D2：正式评测（后端非 MOCK，模型名与 health 一致）
python eval/run_formal_eval.py \
  --base-url http://127.0.0.1:8100 \
  --magpie-url http://127.0.0.1:3425/v1 \
  --model auto/deepseek-v4.1-flash \
  --concurrency 3 --timeout 210 --repeats 3 --baseline-max-tokens 8192

# 离线单元测试
python eval/test_formal_eval.py
```

退出码（D2）：0 全部成功；3 跑完但有失败（保留在分母）；2 门禁拒绝；1 中断/其他错误。
留出集 c5：D0/D2 都不会运行；仅在项目负责人宣布方案冻结后另行安排。
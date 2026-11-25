# 衣诺视觉模型工作区

本目录只放视觉模型的格式、脚本、生成数据、原始结果和报告。上服提供的真实图片仍放在 `data/images/`，只作测试，不复制进训练数据。

## 当前进度：本地混合方案已接入案例二样稿，未启动大模型训练

已完成：

- `results/label_review.html`：15 张真实图片的逐张复核页。
- `results/review_focus.md`：最值得人工判断的 7 处标注。
- `results/gold_labels.json`：已完成人工复核，15/15 已确认，其中 5 张有修改。
- `results/platform_research.md`：四个微调方案的对比与推荐。
- `results/stage0_report.md`：阶段 0 简短汇报。

阶段 1 已准备：

- `schema/deconstruct.schema.json`：看图解构任务的 JSON Schema。
- `schema/deconstruct_prompt.txt`：看图解构的中文指令模板。
- `schema/verify.schema.json`：效果图核对任务的 JSON Schema。
- `schema/verify_prompt.txt`：效果图核对的中文指令模板。
- `scripts/eval.py`：本地模型评测脚本，只调用本机 Ollama，不上传图片。
- `scripts/simple_probe.py`：逐字段简单提问，可做多次同义提问投票。
- `scripts/verify_compare.py`：先独立解构，再由程序比对规格，避免模型被规格锚定。

阶段 1 已完成：

- 3B 和 7B 量化版的 15 张真实图基线。
- 单次结构化 JSON 与逐字段简单提问的对比。
- 直接核对与“先解构再比对”的对比。
- 结果见 `results/stage1_report.md` 和 `results/prompt_iteration_3b.md`。

阶段 1 补充：固定新基准并完成“先修再决定微调”：

- `data/benchmark_v2/`：15 张人工确认真实图 + 35 张合成压力图；两套分开报，真实图只做测试。
- `results/benchmark_v2_structured_qwen7b/`、`results/benchmark_v2_structured_qwen3b/`：统一 schema 的 50 张基线，原始输出逐条保留。
- `results/color_lab_v1/`：U2Net 裁上装 + Lab/KMeans 主色对照，未能超过“永远猜黑色”。
- `results/pattern_crop_qwen7b/`：花纹原图、上装裁切对照；单独问花纹 14/15，裁切 13/15。
- `results/length_fewshot_qwen7b/`：衣长三示例失败，15 张全部偏向“到腰”。
- 汇总判断见 `results/benchmark_v2_report.md`：当前不建议启动微调，先按字段分工并保留人工确认。

阶段 2 小试跑已完成：

- `data/pilot_20/`：第一版 20 张合成图，全部为模特全身照；用于发现域差距，不作为正式训练集。
- `data/pilot_mixed_20/`：第二版 20 张合成图，10 张挂拍、5 张全身、5 张近景；每个领型 4 张、每种闭合方式 5 张、三种衣长各 5 张。
- `data/pilot_20/review.html`、`data/pilot_mixed_20/review.html`：本地图像复核页。
- 20 张图训练 CLIP 线性分类器的真实图表现明显不足；只用文本提示的 FashionSigLIP 反而更好。
- 当前最合适的免费本地方案见 `results/stage2_pilot_report.md` 和 `results/hybrid_local/report.md`。
- 看图功能已拆到独立页 `prototype/vision-check.html`；案例页只在参考图卡片里保留入口，不再把工具和案例讲解混在一起。
- 上服 15 张真实图仍然只做测试，未进入训练集或提示词示例。

## 案例二样稿怎么跑

先确认本机 Ollama 已启动，且已有 `qwen2.5vl:3b`、`qwen2.5vl:7b` 和 `.venv-vision/` 环境：

```bash
brew services start ollama
python3 vision/server_local.py --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/prototype/case2.html
```

操作流程：

1. 打开案例二样稿，在“原案例参考图”卡片底部点“打开看图核对”。
2. 独立页会自动读取案例页当前的方案版本。
3. 在独立页选择一张挂拍或上身图。
4. 点“开始看图”，等待 30–90 秒。
5. 查看领型、闭合方式、衣长、纽扣、廓形、纹样和颜色七项候选。
6. 每项只显示“疑似 / 看不出”，并给出与当前方案的候选比对；不会自动写入方案版本。
7. 可以复制核对结果，带回案例页人工确认。

说明：

- `server_local.py` 只绑定 `127.0.0.1`，不监听局域网；图片写入系统临时目录，推理完成后删除。
- 不能用 `file://` 或普通 `python3 -m http.server` 打开后调用模型；普通静态服务器没有 `/api/deconstruct` 接口。
- 接口最多接收 20MB 请求体，页面把图片限制为 14MB 以内。
- 这是本地开源视觉模型组合，不是微调模型，也不能对外称“自研大模型”。
- 浏览器验证截图：`results/case2_clean_page.png`、`results/vision_check_page.png`。

## 复核页怎么用

1. 双击打开 `vision/results/label_review.html`，或在项目根目录运行：

   ```bash
   python3 -m http.server 8000
   ```

   然后访问：

   ```text
   http://127.0.0.1:8000/vision/results/label_review.html
   ```

2. 逐张检查领型、闭合方式、纽扣、衣长、廓形、纹样和颜色。拿不准时保留原标注，在“复核备注”里写明原因。
3. 点“确认本张”。所有修改会自动保存在当前浏览器本机；刷新页面不会丢进度。
4. 全部确认后点“导出 gold_labels.json”。如果浏览器弹出保存位置，选择：

   ```text
   vision/results/gold_labels.json
   ```

   如果只是下载到“下载”文件夹，请把文件移回上述位置，覆盖初始模板。

## 目录约定

```text
vision/
  schema/      两个任务的 JSON Schema 和中文指令模板（阶段 1）
  scripts/     数据生成、格式转换、评测脚本（阶段 1–2）
  data/        生成的训练集和验证集（阶段 2）
  results/     原始输出、复核页和报告
  README.md    本文件
```

## 阶段 2 小试跑怎么复现

本机隔离环境在项目根目录的 `.venv-vision/`，不会影响系统 Python。

安装：

```bash
python3.12 -m venv .venv-vision
.venv-vision/bin/pip install -r vision/requirements.txt
```

生成复核页：

```bash
.venv-vision/bin/python vision/scripts/make_pilot_review.py \
  --manifest vision/data/pilot_mixed_20/manifest.json
```

训练 CLIP 线性分类器并测试真实图：

```bash
.venv-vision/bin/python vision/scripts/train_clip_probe.py \
  --manifest vision/data/pilot_mixed_20/manifest.json \
  --model 'hf-hub:Marqo/marqo-fashionSigLIP' \
  --pretrained '' \
  --out-dir vision/results/clip_probe_mixed20_fashionsiglip
```

只用 FashionSigLIP 做零样本分类：

```bash
.venv-vision/bin/python vision/scripts/fashionclip_zeroshot.py \
  --out-dir vision/results/fashionsiglip_zeroshot_all5_v2
```

合并三个本地模型的逐字段结果：

```bash
.venv-vision/bin/python vision/scripts/combine_local_eval.py
```

对单张图跑完整本地推理，输出符合 `schema/deconstruct.schema.json` 的 JSON：

```bash
.venv-vision/bin/python vision/scripts/predict_local.py \
  --image data/images/c1_garment.jpeg \
  --out vision/results/example_prediction.json \
  --debug-out vision/results/example_prediction.debug.json
```

默认把所有非“看不出”字段标成“疑似”，不冒充“确定”。

用本地 Qwen 给合成图标签做独立质检：

```bash
.venv-vision/bin/python vision/scripts/qc_pilot_qwen.py \
  --manifest vision/data/pilot_mixed_20/manifest.json
```

## 本地基线怎么跑

本机已安装 Ollama，并下载：

- `qwen2.5vl:3b`（约 3.2 GB，Q4 量化）
- `qwen2.5vl:7b`（约 6.0 GB，Q4 量化）

启动服务：

```bash
brew services start ollama
```

先跑看图解构：

```bash
python3 vision/scripts/eval.py \
  --model qwen2.5vl:3b \
  --task deconstruct
```

再跑效果图核对：

```bash
python3 vision/scripts/eval.py \
  --model qwen2.5vl:3b \
  --task verify
```

逐字段简单提问（更适合小模型）：

```bash
python3 vision/scripts/simple_probe.py \
  --model qwen2.5vl:7b \
  --votes 1
```

先独立解构，再和规格比对：

```bash
python3 vision/scripts/verify_compare.py \
  --deconstruct-dir vision/results/simple_probe_qwen2.5vl_7b_v1 \
  --out-dir vision/results/baseline_qwen2.5vl_7b
```

指定输出目录或只跑少量图片：

```bash
python3 vision/scripts/eval.py \
  --model qwen2.5vl:3b \
  --task deconstruct \
  --limit 3 \
  --out-dir vision/results/baseline_qwen2.5vl_3b_try
```

结果默认写到：

```text
vision/results/baseline_qwen2.5vl_3b/
```

其中 `deconstruct/raw/` 和 `verify/raw/` 是每张图的原始输出，
`summary.json` 是机器可读汇总，`report.md` 是给非技术读者看的报告。

> 本机网络对 Ollama 官方镜像的下载重定向会返回假 DNS，`ollama pull` 可能失败。
> 本次安装是手动下载并校验 blob 后放入 `~/.ollama/models/` 的；
> 如果重装，先确认模型能正常推理，再开始评测。

## 硬性边界

- 上服 15 张真实图片只用于测试，不进入训练数据、提示词示例或 few-shot。
- 评测数字只能来自真实运行；失败就记录失败。
- 外部 API 调用、上传数据、创建账号、付费和启动训练前，先获得确认。
- API Key 只从环境变量读取。若采用阿里云百炼，变量名是 `DASHSCOPE_API_KEY`。

## 阶段 0 复核记录

- 复核页在 Chrome 中自动检查过：15 张缩略图和主图均能加载，7 个字段可编辑，修改后能确认，刷新后进度保留，导出的 JSON 能被正常解析。
- 人工复核文件已从 `~/Downloads/gold_labels.json` 导入；15 张全部确认，其中 5 张有修改，文件内容与下载版本逐字段一致。
- 未上传任何上服图片，未调用视觉模型 API，未创建云账号，未产生训练费用。

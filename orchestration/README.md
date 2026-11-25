# 衣诺本机运行与验收

开发执行通过 Magpie 的 `auto/deepseek-v4.1-flash` 独立工作进程完成，界面规范由 `auto/kimi-k3.1-pro` 编写；GPT 总控负责拆分、集成、运行和验收。它们不是原生 GPT 子代理。原始调度记录在本目录被忽略的 `runs/`，不要把模型的完成汇报等同于检查结果。

## 启动

在仓库根目录运行；三个服务分别占用终端：

```sh
python3 orchestration/start_backend.py
```

后端默认使用 Magpie `http://127.0.0.1:3425/v1`、DeepSeek V4.1 Flash，端口 8100。按需从环境变量读取 `MAGPIE_API_KEY`，不要把密钥写进命令或文档。`--mock` 切换到标准标注演示模式，不能把这个模式的结果作为真实模型成绩。

```sh
python3 orchestration/start_vision.py
```

视觉端口 8001，使用独立依赖环境与已有缓存；不训练、不修改 `vision/`。旧 `.venv-vision` 曾出现 macOS 占位文件，新环境安装说明见 [WORKLOG.md](WORKLOG.md)。

```sh
cd frontend
npm run dev -- --host 127.0.0.1
```

打开 [衣诺工作台](http://127.0.0.1:5173)。前端将 `/api` 与 `/files` 转发到 8100。也可用 `VITE_USE_MOCK=1 npm run dev -- --host 127.0.0.1 --port 5174` 启动离线演示；离线会话保存在内存，刷新会清空。

已安装的检查依赖在 `orchestration/.venv`，生产后端依赖清单在 `backend/requirements.txt`。如重建环境，安装该清单；浏览器验收还需 Playwright，本机使用已安装的 Chrome。

## 演示路径

1. 新建洽谈，选择案例二，开始解构。
2. 查看梅花、腰身、内搭三个客户问题；领宽保留为设计师事项。
3. 依次确认三问到 v4；检查每次只改相关字段、台账和交接单同步。
4. 手动改色彩，填写原因后到 v5；交接单可复制。
5. 效果图页签提供三条确定性提示词；默认 `IMAGE_PROVIDER=none` 只记提示词，不调用生图服务。
6. 上传参考图或外部图片，可取得疑似冲突和逐项核对。衣长、廓形、颜色只显示人工确认。

自由输入可走“从零生成方案”；真实模型经 Magpie 解构原话，程序匹配资源。没有资源时应明确显示“无出处”，不能把案例参考当库存。

## 验收与评测证据

- `orchestration/runs/browser/summary.json`：HTTP 示例后端与离线前端的三问、手改、复制内容、桌面和窄屏检查。
- `orchestration/runs/browser/media-ui-summary.json`：真实参考图与外部图片上传界面检查。
- `orchestration/runs/browser/real-from-zero-summary.json`：真实模型的自由输入界面检查。
- `orchestration/runs/m2-zero-candidate-2.json`：独立输入的真实从零 API 输出。
- `eval/runs/d2-candidate-2-20261119/`：本轮 D2 原始请求、输出、快照和汇总；状态见 [STATE.json](STATE.json)。
- D2 的运行和评分口径见 [D2_RUNBOOK.md](../eval/D2_RUNBOOK.md)。

留出集只在用户明确宣布方案冻结后跑一次。D1 数据批准不等于方案冻结。冻结视觉模型曾把案例六的交领识别成立领，此项不能称为正例通过，必须人工复核；原始证据保存在 `orchestration/runs/c2-c6-live.json`。

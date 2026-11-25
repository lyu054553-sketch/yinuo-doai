## 总控实测更新 · 2026-11-19

C0/C1/C3/C4实现及已列出的集成流程通过；C2有明确视觉能力缺口，尚不能称为正例全部通过。下方代理的“尚未运行”是历史记录。

- D2 归档版本后端完整检查228 passed、3项离线跳过；修复版最新为284 passed、3 skipped。确定性提示词与Pydantic输入、映射、客户端校验、上传校验、并发合并均包含其中。
- 本地演示证据摘要已验收：保留c1反例与c6正例原始不符合/看不出，不判定视觉通过。读取支持既有“JSON对象后附人工备注”的记录，原始证据未改；运行 `python backend/app/media/demo_evidence.py` 可查看。
- C1真实本机视觉：案例一图片与“脖子短，领子不能太高”返回疑似立领并报领型冲突。真实输出 `orchestration/runs/c1-live.json`。
- C2反例：案例一图对照案例六方案，领型、闭合、纽扣不符合。正例案例六：模型将交领识别成立领，导致领型误报；无外露纽扣符合。未修改冻结模型或标准答案，必须人工复核。证据 `orchestration/runs/c2-c1-counterexample.json`、`c2-c6-live.json`。
- C3三个接口真实HTTP跑通；慢视觉期间人工改色彩至v2仍被保留，媒体记录不覆盖并发修改。`orchestration/runs/media-http-session.json`。
- F3真实界面上传参考图、上传外部图片、none提示词记录与复制内容均通过；控制台错误0、实际付费生图调用0。`orchestration/runs/browser/media-ui-summary.json`。
- C4下方已提供高立领、外露扣参考图提示词，未自行生成付费图片。

---

# 出图与视觉 · 工作记录

## 当前状态

- **C0 已完成并通过总控验收**（2026-11-18）：20 项测试通过，`make_c0_examples.py --check` 通过。
  - 确定性三组提示词、核对清单、独立映射数据表 `mappings.json`、案例二/六示例 `C0_EXAMPLES.json`。
  - C0 修复：`build_prompts()` 已支持真实 Pydantic `SpecVersion` 对象（走 `model_dump()`），不再 `TypeError`。
- **C1 已通过总控验收**（2026-11-18 第三轮；详见下方 C1 各节）：视觉客户端、冻结策略、数据表冲突检测、单元测试、
  真实图检查脚本与依赖视觉服务的集成测试。**C1 本身未改路由**，未实现 C2 生图核对。
- **C2 已通过总控验收**（2026-11-18 第三轮）：`check.py` + `check_rules.json` + `check_c2.py` + `test_check.py`/`test_check_live.py`。
  指标：先只把图片交给视觉服务解构，再由程序与 C0 `build_check_specs()` 比对，**不把方案文字交给模型**。
  总控首轮跑出 30 通过 / 1 失败，失败的是 `test_check_image_sends_only_the_image_to_vision`
  （断言写错，不是实现错）。本轮已**只修该测试**，见下文“C2 测试修正”。
- **C1 / C2 已通过总控验收**（2026-11-18 第三轮）：程序部分测试 20（C0）+ 38（C1）+ 31（C2）通过；
  真实 `data/images/c1_garment.jpeg` 已检出“立领 vs 领子不能太高”冲突；c1 图独立观测对照 c6 规格，
  领型与纽扣均判“不符合”。真实 c6 正例（c6 图 × c6 规格）由总控执行，结果以总控为准。
- **C3 已写完，等总控跑测试**（2026-11-18 第三轮）：路由接上、生图适配器可测但不联网（默认 `none`），
  新增 4 个测试文件。本轮**没有运行任何命令**（无 shell），pytest 结果由总控执行，本文不声称已通过。
- **C3 第二轮（总控首跑 145 通过 / 3 跳过 / 1 失败后的小修 + 并发集成）**：
  1) `test_files.py` 的 `test_validate_rejects_text_even_with_image_extension` 输入只有 21 字节，
     先被“内容过小”拦住，没测到“文件类型”分支——本轮把输入补到 64 字节以上，断言不变，**没改真实校验**；
  2) 路由改为**事务合并**：慢视觉/慢生图调用前只取只读快照，调用全程不持锁；提交时
     `with session_lock(id)` 重读最新会话，只追加本次的 `reference` / `images` / `ledger`，
     图 `version` 保留“出图所依据的那一版”。并发两次 `none` 出图编号唯一、两张图都保留；
     补了“视觉挂起期间 PATCH 方案”的两条回归测试；
  3) `test_reference_live.py` / `test_check_live.py` 只在**视觉服务真的连不上**时 `skip`，
     模型看错不再 `skip` 掩盖：先断言程序一致性，真实差距标 `xfail`（报告显示 XFAIL，不算通过）。
     已知未达标：冻结模型把 c6 交领成衣图识别为立领（无扣判“符合”、领型误报为“不符合”、需人工复核），
     **未改冻结模型/标准答案，也没有把 c6 正例说成全通过**。
- 本轮**没有运行任何命令**（无 shell）：pytest 结果、真实图输出都要由总控执行；本文中凡涉及“真实输出”的地方都注明未运行。

## 本轮（C1）新增/改动的文件

| 文件 | 说明 |
|---|---|
| `backend/app/media/vision_client.py` | 视觉服务客户端：data URL 编码、`POST {VISION_URL}/api/deconstruct`、解包外层 `fields`、按 `vision/schema/deconstruct.schema.json` 逐字校验七字段、校验失败重试一次、把网络/HTTP 错误转成中文异常 |
| `backend/app/media/conflict_rules.json` | 冲突规则数据表（领高、翻领对立领、外露扣对“不要扣子/无扣”、无纹对要暗纹、有纹对要素色） |
| `backend/app/media/reference.py` | 冻结策略（四项采用、三项 `used:false`、把握上限“疑似”）、冲突检测、组装 `ReferenceAnalysis`、生成 `参考图解析` 台账、`analyze_reference()` |
| `backend/app/media/check_c1.py` | 真实图检查脚本（总控执行用），打印冻结结果、冲突、台账和结论 |
| `backend/tests/media/test_vision_client.py` | 视觉客户端单元测试：校验、解包、重试、HTTP/连接错误、图片编码 |
| `backend/tests/media/test_reference.py` | 冻结策略、四类冲突、否定词、看不出、台账结构、`analyze_reference` 组装（不连服务） |
| `backend/tests/media/test_reference_live.py` | 依赖真实视觉服务的集成检查；服务没起时 `pytest.skip` 并说明原因 |

未改动：`prompts.py`、`mappings.json`、`C0_EXAMPLES.json`、`backend/app/routers/media.py`（仍未创建/未接）、
`backend/` 其他部分、`vision/`、`data/`。**未修改任何案例数据，未调用云模型，未训练。**

## 视觉服务真实响应怎么解包（重要）

`vision/server_local.py` 的 `do_POST` 返回：

```json
{"fields": {"collar_family": {"value": "立领", "confidence": "确定"}, "...": "..."},
 "diagnostics": {"...": "..."}, "local_only": true}
```

- 验证结果在**外层 `fields`** 里，`vision_client.deconstruct()` 先取 `payload["fields"]` 再校验；
- 外层没有 `fields` 一律抛 `VisionOutputError`，**不把平铺 payload 当字段硬凑**；
- `diagnostics` 只用来取模型标识（`model` / `model_name` / `models`），取不到就写 `vision@{VISION_URL}`，不编模型名；
- 七字段之外的多余键、缺键、词表外取值、非法把握程度都直接报错；格式错误重试一次仍错就抛错。

## 冻结策略（C1 实现口径）

依据《接口约定》第 7 节与 `vision/results/benchmark_v2_report.md`：

| 视觉键 | 中文标签 | used | 把握上限 |
|---|---|---|---|
| `collar_family` | 领型 | true | 疑似（`确定` 降为 `疑似`） |
| `front_closure` | 前襟闭合 | true | 疑似 |
| `buttons_visible` | 纽扣 | true | 疑似 |
| `pattern` | 纹样 | true | 疑似 |
| `length` | 衣长 | **false** | —（值写“看不出”） |
| `silhouette` | 廓形 | **false** | —（值写“看不出”） |
| `colors` | 颜色 | **false** | —（值写“看不出”） |

- `ReferenceAnalysis.fields` 的键用中文标签；每项只有 `value / confidence / used` 三个键。
- 未采用的三项既不改值也不抛冲突，界面由前端显示“请人工确认”。
- 模型说“看不出”时，值同样写“看不出”，`used` 仍为 true（表示“这一项按策略可用，但这次没看出来”）。

## 冲突检测（纯程序）

规则在 `conflict_rules.json`（按 `priority` 排序），只使用四个采用字段：

| 规则 | 触发 | 产出 |
|---|---|---|
| `RC-领高` | 图为立领 + 原话含“领子不能太高 / 脖子短 / 低领……” | `【领型】参考图为立领，客户说“{原话}”，请确认领高…` |
| `RC-领型-翻领对立领` | 图为翻领 + 原话要中式立领/立领 | 提示二者不一致 |
| `RC-纽扣-不要扣` | 图能看到外露纽扣 + 原话“不要扣子/无扣/暗扣……” | 提示改暗门襟或取消纽扣 |
| `RC-闭合-不要扣但外露扣` | 前襟=外露扣 + 原话“无扣开襟……” | 提示改无扣开襟 |
| `RC-纹样-参考图无纹客户要暗纹` | 图看不出/无明显花纹 + 原话要暗纹、暗花、提花 | 提示改面料或说明只作版型参考 |
| `RC-纹样-参考图有纹客户要纯色` | 图有纹样 + 原话要素色、纯色 | 提示确认素色 |

口径说明：

- 否定词保护：`不要立领` 不算“要立领”（`negation` 前后窗 4 字）。
- 领型/闭合/纽扣的模型把握程度是“看不出”时**不报冲突**（不拿没看见的东西下结论）；
  纹样例外——纹样“看不出”本身就代表“看不到花纹”，客户要暗纹时要提示。
- 一条规则最多挂 3 条原话，产出 1 条冲突；`utterance_ids` 会写进 `ReferenceAnalysis.conflicts` 和台账。

## 台账

每次解析追加一条 `LedgerEntry`：

- `action = "参考图解析"`；`version` = 当前方案版本（`spec_versions[-1].version`）。
- `summary`：`【参考图解析】采用 领型=立领、前襟闭合=交叠闭合…；发现 N 处与原话冲突`
  或 `…；未发现与原话冲突`。
- `detail`：模型标识、采用字段、`需人工确认：衣长、廓形、颜色`、逐条冲突。
- `utterance_ids` / `changed_fields` 只来自冲突；不谎称改了方案版本。

## 怎么运行（总控执行）

```bash
# 单元测试（不连服务；真实图测试在服务未启动时自动 skip）
pytest backend/tests/media -q

# C1 验收：真实 c1 图 + 低领口述（需先起视觉服务）
#   终端 A：python vision/server_local.py --port 8001
#   终端 B（项目根目录）：
python backend/app/media/check_c1.py \
  --image data/images/c1_garment.jpeg \
  --text "脖子短，领子不能太高"
```

`check_c1.py` 输出 JSON（冻结后的七字段、conflicts、ledger），最后一行是中文结论。
视觉服务没起时它打印“视觉服务未启动…”并以退出码 2 结束。

C3 路由将要使用的调用方式（本轮未接）：

```python
from app.media import reference, vision_client
from app.store import save_session

data_url = vision_client.encode_image_data_url(await file.read(), vision_client.mime_from_name(file.filename))
try:
    session = reference.analyze_reference(session, data_url, "/files/reference/xxx.jpeg")
except vision_client.VisionServiceUnavailable as exc:
    return JSONResponse({"error": str(exc)}, status_code=503)   # 约定：视觉服务未启动
except vision_client.VisionTimeout as exc:
    return JSONResponse({"error": str(exc)}, status_code=504)
except vision_client.VisionOutputError as exc:
    return JSONResponse({"error": str(exc)}, status_code=502)
save_session(session)
```

`analyze_reference()` 只写 `session.reference` 和 `session.ledger`，不落盘、不改方案版本。

## 预期结果（未由我运行）

- `data/images/c1_garment.jpeg` 若被模型识别为“立领”，配上“脖子短，领子不能太高”
  应产出 1 条 `领型` 冲突（`RC-领高`），`utterance_ids=["u1"]`，台账 `changed_fields=["领型"]`。
- 若模型把这张图识别成别种领型（例如“翻领”），`RC-领高` 不触发；这时真实测试会 skip 并打印实际识别值，
  我不会声称冲突已达成。真实输出以总控运行结果为准，随后补进本文件。

## 已知限制 / 待办

- 我这边没有 shell：pytest、`check_c1.py`、真实图识别都**未实际运行**，需总控执行确认。
- `detect_conflicts` 只看客户原话文字，不看 `mappings` 的字段归属；若同一句里提到多个字段可能多报，后续可按需加 `mapping_fields` 约束。
- 纹样规则用“无明显花纹/纯色/素色/看不出”等标记判定“看不到花纹”，未覆盖所有描述；规则表可继续补。
- 中文字段标签（`field` 值仍是八字段原文）已按 C0 口径，前端展示尚未对接。
- C4 演示素材（高立领、外露扣的新中式外套参考图提示词）已写入本文 C4 一节；**没有调用付费接口生成图片**。

## C2：效果图核对（已写完，未由我运行）

### 口径

1. `vision_client.deconstruct(image_data_url)` **只发图片**；方案字段、`build_check_specs()` 的预期、
   核对清单一概不进请求体（`check.check_image()` 的 kwargs 只有 `vision_url` / `timeout`）。
2. 程序拿模型看出的七字段，与 `prompts.build_check_specs(spec_version)` 逐项比对，顺序固定：
   `collar_family`(领型) → `front_closure`(版型结构) → `buttons_visible`(版型结构) → `pattern`(装饰纹样)
   → `length`(长度比例) → `silhouette`(版型结构) → `colors`(色彩)。
3. 冻结策略：前四项给“符合/不符合/看不出”，把握程度最高“疑似”（`确定`→`疑似`）；
   后三项 `used:false`，一律 `verdict=看不出`、`confidence=看不出`、`observed="需人工确认"`。
4. **缺观测不算符合**：模型 `confidence/value = 看不出`、字段缺键、纹样描述归不到“有纹/无纹”、
   或方案这一栏没写清（`expected_value = 看不出`）→ 都是 `看不出`，绝不判“符合”。
5. 纹样归一：`check_rules.json` 的 `none_markers`（“无明显花纹/纯色/素面……”）先判无纹；再看 `present_markers`
   （暗纹/暗花/提花/织花/花纹……）是否被 `negation_prefixes`（无/未/非/勿/免/没有/不要/未见/看不到/看不见/看不出/不是，
   窗口 6 字）否定。前缀表**只收明确的否定词组，不含“不”“没”单字**，避免“不错的暗纹”被误判成无纹；
   “不是明显的花纹，但局部有刺绣”仍能因“刺绣”未否定而判为有纹样。
6. 返回结构 = 约定 `CheckResult`（`items[field/expect/observed/verdict/confidence] + model`），过
   `app.schemas.CheckResult.model_validate()` 校验后才返回，字段只多不少。

### 数据表 `check_rules.json`

`verdict_labels`（符合/不符合/看不出）、`manual_targets`（衣长/廓形/颜色）、`unseen_observed="看不出"`、
`manual_confirm_observed="需人工确认"`、`redraw_on_verdict="不符合"`、`pattern_observation`
（present/none 标记 + 否定前缀 + 窗口）。

### C3 路由可用的三个入口（本轮未接路由）

```python
from app.media import check, vision_client

# 1) 只核对不落库：按会话当前方案版本（或指定 version）核对一张图
payload = check.check_generated_image(session, image_data_url)          # -> CheckResult dict
payload = check.check_generated_image(session, image_data_url, version=1)

# 2) 核对 + 写 GeneratedImage.check + 追加“核对”台账
session = check.check_and_record(session, image_id_or_object, image_data_url,
                                 utterance_ids=[...], note="效果图第 1 次出图")
# 想分两步：先 check_generated_image()，再 check.record_check(session, image, payload)

# 3) 是否需要在配置了生图接口时自动重画一次
if check.needs_redraw(payload): ...

# 异常 → 路由映射
#   vision_client.VisionServiceUnavailable -> 503 {"error": "视觉服务未启动"}
#   vision_client.VisionTimeout            -> 504
#   vision_client.VisionOutputError        -> 502
#   check.CheckError（没有方案版本 / 找不到图） -> 400
```

- `record_check()` 只改 `session.images[i].check` 与 `session.ledger`，**不改方案版本、不落盘**；
  台账 `action="核对"`，`version` 取该图的 `version`，`summary` 为“符合 X 项、不符合 Y 项、看不出 Z 项”，
  `changed_fields` 只放“不符合”对应的八字段。
- `check_and_record()` 默认用这张图自己的 `version` 核对，不默认当前最新版。
- 本模块**不生成图片**：重画由路由按 `IMAGE_PROVIDER` 决定，付费调用前必须问项目负责人。

### C2 测试修正（总控 30 通过 / 1 失败 → 只改测试）

- 失败：`test_check_image_sends_only_the_image_to_vision` 断言“交领/暗花/不应看到纽扣/可见纹样”都在**原始方案** JSON 里。
  后两个词其实来自 `build_check_specs()`（`expect` 写的是“不应看到纽扣”“应有可见纹样（暗纹或花纹）”、
  `expected_value` 是“可见纹样”），原始 spec 里没有，**是测试的取样范围写错，不是实现泄露**。
- 修正：禁止外传的基准改成 `{"spec": spec, "check_specs": build_check_specs(spec)}` 的 JSON，
  四个词逐个断言“在禁止基准里存在、在调用参数里不存在”。
- **强断言保留未删**：`args == ("data:image/jpeg;base64,AAAA",)`、`set(kwargs) <= {"vision_url","timeout"}`。
  防规格污染测试没有削弱，覆盖范围反而更宽（原先只查原始方案，现在连核对清单一起查）。

### 真实图检查命令（总控执行）

```bash
# 终端 A：起视觉服务
python vision/server_local.py --port 8001
# 终端 B（项目根目录）：

# c6 图 × c6 方案：交领、无扣 → 领型/前襟/纽扣 判“符合”（纹样看模型是否认出暗花）
python backend/app/media/check_c2.py --image data/images/c6_garment.jpeg --case c6

# c1 图（立领、无明显花纹）× c6 方案：领型、纹样 判“不符合”
python backend/app/media/check_c2.py --image data/images/c1_garment.jpeg --case c6

# 单元测试（不连服务；真实图测试在服务未起时自动 skip 并写明原因）
pytest backend/tests/media -q
```

`check_c2.py` 只读开发集案例与本地图片、只调本机视觉服务；案例五（留出集）直接拒绝；不出图、不调大模型。

### 预期 / 待验收（**未运行，不声称通过**）

- c6 × c6：预期 `领型=符合(交领)`、`前襟=符合`、`纽扣=符合(无外露纽扣)`；`纹样` 取决于模型是否报出暗花，
  报“暗花/提花/织花”即符合，报“无明显花纹”即不符合，报“看不出”即看不出——**照实汇报**。
- c1 × c6：预期 `领型=不符合(立领)`；`纽扣` 若模型看不到扣子则为“符合”，**这不违反预期**（c6 要求无扣）；
  `纹样` 若报无花纹则“不符合”。
- 衣长/廓形/颜色恒为“看不出 + 需人工确认”，与模型说了什么无关。

### 已知限制

- 视觉模型看“纽扣”是靠外观判断，暗门襟/隐藏扣可能被误报成“无外露纽扣=符合”；已被把握上限“疑似”与
  “由人确认”兜住，但仍是误差来源。
- `front_closure` 允许 `交叠闭合` 的备选 `无扣开襟` 也算符合（7B 常把交领判成两者之一），
  反过来不成立：方案写“无扣开襟”时 `交叠闭合` 算不符合——这条口径来自 `mappings.json` 的注记，可按需放宽。
- 纹样归一仍是关键词表，描述特别绕时可能归"看不出"，不会硬凑成符合。

## C3：接路由 + 生图适配器（已写完，未由我运行）

### 新增/改动的文件

| 文件 | 说明 |
|---|---|
| `backend/app/routers/media.py` | 替换原 501 占位：`GET /prompts`、`POST /reference`（multipart）、`POST /images`（JSON 或 multipart）。只复用 `schemas` / `store` / `config`，未改核心文件 |
| `backend/app/media/files.py` | 图片按魔术数字校验 MIME 与大小、`uuid4().hex` 安全随机文件名、写 `storage/files/{reference,generated,uploads}/`、生成 `/files/...` 地址、读回图片再校验 |
| `backend/app/media/image_provider.py` | 生图适配器：`none`（默认，不发请求）与 `openai_compatible`（b64_json → 校验 → 落盘）；transport 可替换，测试全走假 transport |
| `backend/app/media/vision_client.py` | **只改 `_model_name`**：改为按 `diagnostics.fields[*].source` 按序去重提取模型来源，取不到再退回 `model/model_name/models`，最后退回 `vision@<地址>`；不编造型号。其余 C1 逻辑一字未动 |
| `backend/tests/media/test_files.py` | 图片校验、随机文件名、路径与读写 |
| `backend/tests/media/test_image_provider.py` | `none` 不发请求、`openai_compatible` 用假 transport 走通、缺配置/坏返回都中文报错、repr 不泄露 Key |
| `backend/tests/media/test_media_api.py` | 真实路由 + 假视觉 + 假 transport：提示词、参考图、none 出图、外部上传、重画一次、线程隔离、慢请求不阻塞 |
| `backend/tests/media/test_vision_model_name.py` | 模型来源解析与兜底 |

未改动：`prompts.py`、`check.py`、`reference.py`（C0/C1/C2 逻辑）、`mappings.json`、`check_rules.json`、
`conflict_rules.json`、`backend/` 其他部分、`data/`、`vision/`。**未调用任何付费接口。**

### 口径

1. **接口形态**：三条路由都返回约定结构；`POST /images` 用 `Request` 手工解析 body，
   同一路由既支持 `application/json {"kind": ...}`，也支持 `multipart/form-data`（`file` + `kind`）。
   错误一律 `{"error": "中文说明"}`：上传不合规 400、会话不存在 404、视觉服务未启动 503、
   视觉超时 504、视觉结果不合格 502、生图配置/接口错误 502。
2. **`IMAGE_PROVIDER=none`（默认）**：`provider.generate()` 纯本地立即返回 `url=None`，
   照常写一条 `action="出图"`、summary 带“（仅提示词）”的台账，`check` 保持 `None`，
   **不调视觉、不调生图、不落图片文件**；测试用 `no_network` 夹具断言 `_post_json` 一次都没被调用。
3. **外部图片上传**（multipart + `kind`）：先只把图片交给视觉服务核对，成功后存到 `files/uploads/`，
   记一条“出图（外部上传）”+ 一条“核对”台账，`provider="upload"`、`prompt=""`，不改方案版本。
4. **`openai_compatible`**：`POST {IMAGE_BASE_URL}/images/generations`，body 带 `model/prompt/n/size/response_format=b64_json`，
   Key 只放在 `Authorization` 头里（不写进 body、正文、日志）。返回的 b64 先按魔术数字与大小校验再落盘。
   核对出现“不符合”时**自动重画最多一次**，提示词逐字不变（参数锁定），两张图与两份 `check` 都保留；
   重画失败只记台账，不推翻第一张图。
5. **不阻塞**：视觉与生图分别在 `ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-vision"/"media-image")`
   里跑（`run_vision` / `run_image_provider`），不占用事件循环和 FastAPI 默认线程池。
   测试里让视觉调用睡 0.6 秒，同时请求 `/prompts` 必须在 0.25 秒内返回。
6. **图片校验**：只看内容不看后缀（JPEG/PNG/WEBP/GIF/BMP 魔术数字），64 字节 ~ 12MB；
   文件名是 `uuid4().hex` + 判定出的后缀，用户给的文件名一律丢弃。

### 怎么运行（总控执行）

```bash
pytest backend/tests/media -q          # 全部离线；live 文件在视觉服务没起时自动 skip
```

真实的端到端验证（起后端后）：

```bash
cd backend && uvicorn app.main:app --port 8100
# 另开终端：
curl -s localhost:8100/api/sessions/<id>/prompts
curl -s -F "file=@data/images/c1_garment.jpeg" localhost:8100/api/sessions/<id>/reference
curl -s -H "Content-Type: application/json" -d '{"kind":"成衣挂拍"}' localhost:8100/api/sessions/<id>/images
curl -s -F "file=@data/images/c6_garment.jpeg" -F "kind=成衣挂拍" localhost:8100/api/sessions/<id>/images
```

预期（**未由我运行**）：`none` 时 `/images` 返回的 `Session` 里最后一张图 `url=null`、`provider="none"`、
`check=null`，台账末条是“出图（仅提示词）”；`/reference` 在视觉服务没起时返回 503。

### 本轮没做的

- 没有接通真实生图服务（需项目负责人另行授权），`IMAGE_PROVIDER` 始终 `none`；
- 没有运行 pytest / uvicorn / curl（无 shell），所有“通过”都要等总控跑完再写；
- `backend/app/main.py` 的装配未改动（本来就 `include_router(media.router)` 并挂载 `/files`），
  但**没有实际启动后端验证**（后端核心 M2 可能暂时处于导入中间态）。

## C3 第二轮：并发事务合并（本轮改动）

### 问题

总控首跑报出的失败只有一处（`test_files.py` 的伪装图片测试取样不对）；同时指出真正的设计缺陷：
`/reference`、`/images` 在 `await` 完慢视觉后 `save_session(旧 Session)`，会把**同时发生的客户回答或人工修改**整份冲掉。

### 改法（只改 `backend/app/routers/media.py`）

1. 路由工整分为两段：
   - **慢阶段（不持锁）**：`_load()` 取一份只读快照 → `run_vision` / `run_image_provider` 完成视觉解构、核对、重画；
     参考图的冲突比对用 `reference.build_reference_analysis(..., utterances=快照原话)`，纯程序。
   - **提交阶段**：`await commit(session_id, apply)` → `asyncio.to_thread(_commit)` →
     `with session_lock(session_id): latest = load_session(...) → apply(latest) → save_session(latest)`。
     只追加本次的 `reference`、`images`、`ledger`，**绝不整份覆盖**；持锁时间只有一次 JSON 读写。
2. `GeneratedImage.version` = **出图/上传时依据的那一版**（快照版本），方案期间升版也照样指向旧版；
   `LedgerEntry.version` = 提交时的当前版本（“这个动作发生在第几版”照实记，冲突/核对依据的版本写在 `detail` 里）。
3. 图片编号 `_next_image_id()` 在**锁内**计算，`none` 并发两次出图拿到 `img1`/`img2`，不会互相覆盖或撞号。
4. 生图 + 核对 + 重画全部在锁外完成，结果一次性提交；重画失败只追加一条台账，不推翻第一张图。
5. `_redraw_once()` 里“重画后核对失败”原来抛 `VisionError`，现在统一由 `_checked_payload` 转成中文 `_Problem`，
   在锁外捕获、锁内记台账。

### 新增回归测试（`backend/tests/media/test_media_api.py`）

| 测试 | 断言 |
|---|---|
| `test_slow_reference_keeps_concurrent_manual_patch` | 视觉线程被 `threading.Event` 挂起期间 `PATCH /api/sessions/{id}/spec` **立刻成功**（证明慢调用不持锁）→ 放行后参考图 200；会话末版 = 2、`人工修改` 台账在、`参考图解析` 台账在其后且 `version=2`、`reference` 仍在 |
| `test_slow_generation_keeps_concurrent_patch_and_image_version` | 生图核对挂起期间 PATCH → 新版方案与 `人工修改` 台账都保留，`images[0].version == 1`（出图依据的旧版），`核对` 结果不丢 |
| `test_none_images_concurrent_requests_keep_both` | `none` 并发两次出图：两张图都在、编号唯一、`url=null`、两条“出图”台账、`_post_json` 一次都没调 |

夹具里给假视觉加了门闩（`paused` / `entered` / `release`）；`api` 夹具额外挂了**真实核心路由**
`app/routers/sessions.py`（只读引用，用来发 PATCH，不改它）。

### `test_files.py` 小修

```python
payload = b"<html>not image</html>" + b" " * 64   # ≥ 64 字节，先过最小长度
assert files.detect_image_type(payload) is None
# 断言仍是“不是能识别的图片”，没有为了让测试过而改 MIN_IMAGE_BYTES 或校验顺序
```

### live 测试口径收紧

`test_reference_live.py` / `test_check_live.py`：

- 文件级 `skipif` 仍然只看“VISION_URL 端口能否连上”，连不上才 `skip` 并写明原因；
- 删除“模型没认出来就 skip”的分支。模型看错时：先断言程序一致性（观测≠交领 → 必须“不符合”，
  没看到立领 → 不报领型冲突），再把真实差距标 `pytest.xfail(...)`，报告里显示 **XFAIL**，不是 PASS、也不是 SKIP。
- **c6 真实正例现状（如实记录）**：本机冻结模型把 `data/images/c6_garment.jpeg`（交领、无扣）识别为**立领**，
  因此 `纽扣=符合（无外露纽扣）`、`领型=不符合（误报）`、`纹样` 视模型是否报暗花而定，**领型一项需人工复核**。
  这个正例**不能算全通过**；冻结模型、`vision/` 标准答案、`data/` 一律未改。正式评测已批准但尚未宣布冻结。

## 本轮（2026-11-18 第四轮）没做的 / 待总控验证

- 没有 shell：`pytest backend/tests/media`、真实后端起服与三条 `/api` curl 都**没有运行**，结果以总控为准，本文不声称通过。
- 未接通真实生图服务，`IMAGE_PROVIDER` 仍是 `none`；`openai_compatible` 只用假 transport 测过，**零付费调用**。
- 未改 `backend/` 其他文件（含 `schemas.py`、`store.py`、`main.py`）、`data/`、`vision/`、`docs/`。
- 案例五（留出集）没有出现在本模块任何提示词、示例、规则或测试里。

## C4：演示素材（只写提示词，不生成付费图）

用途：和客户口述“脖子短，领子不能太高；不要外露的扣子”叠在一起，演示**参考图与口述冲突**。
这张图由人工在外部工具生成（或手头现成的图），**不由本系统调用付费接口生成**。

参考图生成提示词（可直接粘贴给外部生图工具）：

```text
新中式立领外套的正面挂拍照片：黑色真丝提花面料，高立领（贴合颈部、遮住部分脖颈），
前襟为明门襟，单排 5 颗外露盘扣，H 型直身不收腰，长袖，衣长至臀中，肩线自然微落。
纯白无缝背景，柔和均匀布光，正面平视，服装轮廓与缝线清晰；不出现模特、文字、水印与标志。
```

配套演示说明（不要写进提示词）：

- 口述示例：“脖子短，领子不能太高；不要外露的扣子，做成暗门襟。”
- 预期触发两条冲突：`RC-领高`（立领 vs 领子不能太高）、`RC-纽扣-不要扣`（外露扣 vs 不要扣子）。
- 演示时先上传这张图 → 看冲突提示 → 再让客户二选一 → 方案升版本、台账记一笔。
- **不能**用案例五的任何内容做这个素材（留出集）。

## 下一步

等总控跑 `pytest backend/tests/media`（含新建的 4 个文件）与真实后端的三条 curl，
把真实输出补进本文件。通过后再讨论是否接通真实生图服务（需项目负责人明确授权与额度确认）。

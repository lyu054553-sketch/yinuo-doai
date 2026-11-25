## 离线语义复核补记 · 2026-11-19

截图复查后补齐了离线确认解释、重复回答幂等、手改撤销本字段未答问题/保留其他冲突、清除失效资源出处，以及约定的六部分交接单。只修改模拟模块，真实后端评测快照不变。最终构建通过，离线v5流程、1280/390与复制内容复测通过；`orchestration/check_mock_linkage.py` 另验证了这几个实际缺口，结果保存在 `orchestration/runs/browser/mock-linkage-summary.json`。

最新浏览器回归又在8100真实Magpie模式下复验5173完整案例二至v5，并复验5174离线模式。`orchestration/runs/browser/summary.json` 现分别记录 `client_mode=连接后端` 与 `client_mode=示例数据`；两模式控制台错误0、1280/390无页面横向溢出。

## 总控实测更新 · 2026-11-19

当前 F0/F1/F2/F3/F4 的实现和已列出的界面流程均已验收。下方旧代理记录是历史快照，其“未验证/下一步”不代表当前状态。

- 最终 `npm run build` 通过（57 modules）；favicon 404 已修复。
- 5173 HTTP 示例后端与 5174 纯前端模拟：梅花、腰身、内搭三问到 v4，人工修改色彩需原因，升 v5；版本、字段高亮、6条台账、六部分交接单同步。复制动作的内容通过浏览器替身核验，未修改用户剪贴板。脚本异常及控制台错误均为0，1280/390无页面横向溢出。
- F2 真实模型：从5173自由输入到v1，source=llm，醋酸对应F1，梅花保留且明确无出处；6.735秒，控制台错误0。此为开发集成自检，不是正式评测。
- F3 真实媒体：三提示词可复制；none只记录提示词；参考图上传返回1处冲突；外部图片上传核对，保留两条图片记录；视觉字段显示中文“有/无”“疑似”“请人工确认”，控制台错误0，付费生图调用0。
- F4 交接单表头和字段名不再逐字竖排；局部CSS变更后重新构建并复测离线三问/手改/复制/1280和390均通过。
- 证据：`orchestration/runs/browser/{summary.json,media-ui-summary.json,real-from-zero-summary.json}`，同目录有截图。
- 当前5173连接真实Magpie后端；5174仍可离线演示。离线列表刷新清空的限制保留。冻结视觉的案例六领型误识别仍需人工复核，不计为视觉正例通过。

---

# 前端工作日志

## 当前状态：F0/F1/F2/F3/F4 已由总控验证（构建、两模式三问 v4、1280/390 无横向溢出）；本轮只补 VoicePanel 中文显示，待总控复验收

本轮只动 `src/components/VoicePanel.tsx`（外加本文件），**没有运行任何命令**（本代理无 shell）；构建、启动、浏览器复验由总控执行。没有执行 git，没有改后端、`data/`、`eval/`、`vision/`、`prototype/`、`docs/`，没有新增依赖，没有调用模型或生图，页面布局与既有样式未改。

### 本轮修复（参考图识别结果的中文显示）

1. 参考图字段值里 `纽扣` 等布尔值不再显示 `true/false`：新增 `formatReferenceValue(value: string | boolean)`，布尔值显示为“有 / 无”，字符串原样显示。只改展示，不改真实返回字段，也未改类型定义。
2. `used: false` 的字段（衣长、廓形、颜色）取值处直接显示“请人工确认”，不再把模型给出的值当真展示；右侧徽标只写“模型结果未采用”。不再只写“看不出”（“看不出”仍是模型返回的 `confidence` 原值，用于 `used: true` 的项）。
3. 保留疑似项提示：`used: true` 的项继续显示后端返回的 `确定 / 疑似 / 看不出`，疑似用原有虚线徽标；卡片底部说明“疑似项与未采用项请人工确认”保留。
4. 参考图与口述的出入、上传参考图、隐藏 file input 等逻辑与布局均未改动。

## 怎么运行

```bash
cd frontend
npm install
npm run dev                   # 默认连接真实后端，Vite 代理 /api、/files → 127.0.0.1:8100
VITE_USE_MOCK=1 npm run dev   # 示例数据模式，不连后端，可离线演示
npm run build                 # tsc --noEmit + vite build
npm run typecheck
```

端口 5173。Windows 可把 `VITE_USE_MOCK=1` 写进 `frontend/.env.local`。

## 本轮修复（总控指出的三项）

### 1) 案例二示例数据改为克隆真实后端已验收会话

- 新增 `src/mocks/case2.fixture.json`：用 copy_file 原样复制 `backend/storage/sessions/sd2f496047a5f.json`，**没有手工重写任何一条标注**。
- `src/mocks/case2.ts` 改为 `import fixture`，`buildCase2Session()` 深拷贝这份 JSON，只替换 `id`、`title` 和日期（`created_at`、各版本 `created_at`、台账 `at`），其余字段（原话、八栏方案、资源 id、三个问题、设计师事项、不问清单）保持后端数据原样。
- 由此校准的结果：三个问题依次是“梅花暗纹（装饰纹样）”“腰身微微收腰还是整体宽松（版型结构）”“内搭要不要预留放量（松弛余量）”；设计师事项只有 1 条（领宽，`rule_id` R3）；不问清单是色彩、长度比例；方案资源为 S2、SH1、CL2、F3、P1、L1，不再出现编造的 S1/P3；**没有任何编造的尺寸**（上一版 mock 的“领宽 8cm”已随重写整条删除）。
- 保留 `CASE2_TEXT`（取自 fixture 的 `input.text`）、`CASE2_SPEC`（取自 `input.existing_spec`）导出，`answerQuestion` / `patchSpec` / `buildHandoff` 三个函数签名与行为不变，`mockClient.ts` 不需要改动。

### 2) `lastChangedFields` 改为比较相邻方案版本

- `src/utils/display.ts`：不再读末条台账的 `changed_fields`（导入/审计台账会让 v1 误标“本版改了 2 栏”），改为取 `spec_versions` 最后两版，逐字段比较取值，取值不同才算变化。
- v1 只有一版 → 返回空数组，不显示任何“本版变更”；之后每题回答只高亮真实变动的栏（案例二 v1→v4 各升 1 栏）。
- 因为完全不看台账，参考图解析、出图、核对等媒体台账不会改变高亮。

### 3) 右栏资源出处显示 id + 名称 + 来源案例

- 新增 `src/utils/resources.ts`：`findResource()` 在 `/api/resources` 返回的资源库里按 id 查名称与 `sources`，`caseLabel()` 把 `c1…c6` 转成“案例一…案例六”；查不到返回 `found: false`，界面写“资源库里没有这条记录”“来源未记录”，**不硬填来源**。
- `WorkbenchPage` 载入工作台时并行 `getResources()`；失败只是把资源设为 null，右栏写“资源库尚未载入”，不影响方案与台账。资源库通过 props 传给 `SpecPanel`。
- `SpecPanel` 每栏出处改为列表：`S2`（等宽字体）+ 名称 + `来源：案例二`；标题保留“资源出处（案例参考，非库存）”，继续说明资源只是案例参考，不代表库存。
- 新增 `src/styles/resources.css`（只加资源列表的排版，全部走既有颜色令牌），在 `main.tsx` 里随 `app.css`、`compat.css` 一起引入。

## 文件结构

```
frontend/
  package.json  tsconfig.json  vite.config.ts  index.html
  src/
    main.tsx  App.tsx  vite-env.d.ts
    api/        types.ts（约定第 5 节全部类型）  client.ts（约定第 6 节全部接口）  errors.ts  mockClient.ts
    mocks/      case2.fixture.json（后端已验收会话原样复制）  case2.ts（克隆 + 升版/台账/交接单规则）
                samples.ts（5 个开发集案例）  resources.ts  generic.ts
    components/ TopBar.tsx StatusTag.tsx VoicePanel.tsx AuditPanel.tsx SpecPanel.tsx
                ImagesTab.tsx LedgerTab.tsx HandoffTab.tsx
    pages/      NewSessionPage.tsx SessionListPage.tsx WorkbenchPage.tsx
    styles/     app.css（令牌 + 布局）  compat.css（.btn-ghost → 次按钮）  resources.css（资源出处列表）
    utils/      display.ts（状态/版本/本版变更）  resources.ts（资源名称与来源案例）
```

## F0 已做到（含本轮）

1. Vite + React + TypeScript 骨架，默认连真实后端，`VITE_USE_MOCK=1` 走本地示例数据。
2. `api/types.ts` 对齐约定第 5 节；`api/client.ts` 覆盖约定全部接口（含 prompts、reference、images 上传核对）。
3. 示例数据全部来自开发集脱敏内容，**不含案例五**；案例二以外的案例走通用规则，只做审计不生成追问。
4. 三个页面：新建洽谈（口述、两种模式、审计八栏表单、示例案例、步骤文字不伪造百分比）、洽谈列表、三栏工作台。
5. 工作台按 DESIGN.md 第 7 节做原话—字段联动；方案可手改（写原因、升版、记台账）；台账倒序；交接单 Markdown 可复制。
6. 样式按 DESIGN.md 令牌与字号；七种状态一状态一色；无出处用白底 2px 墨边 + “！”；焦点环、减少动效、窄屏堆叠。

## 验证状态

- **未验证（无 shell）**：安装、`npm run build`、`npm run typecheck`、`npm run dev`、浏览器控制台、两种模式下的完整流程。
- 自查范围：本轮只改 `mocks/case2.fixture.json`、`mocks/case2.ts`、`utils/display.ts`、`utils/resources.ts`、`components/SpecPanel.tsx`、`pages/WorkbenchPage.tsx`、`styles/resources.css`、`main.tsx`；请求与返回只用约定字段；原话 `text` 是 `input.text` 原文子串（来自 fixture）；未使用案例五做示例。

## 已知问题

1. 示例数据存在内存里，刷新后洽谈列表会清空（真实后端模式不受影响）。
2. 示例模式下案例二以外的案例不生成追问，只展示审计与设计师事项。
3. 参考图与效果图区域在后端 501 时显示“该功能开发中”；真实结构等“出图与视觉”可用后核对（F3）。
4. 三栏栏头高度按内容自然撑开（约 52–100px），没有强行压到设计稿的 52px。
5. 后端 fixture 里问题选项的 `detail` 文案（如“来源案例一、案例四、案例五、案例六”）由后端生成，前端原样展示；右栏资源出处改用资源库元数据，不读 `detail`。

## 下一步

- 总控验收：真实后端模式与 `VITE_USE_MOCK=1` 两种模式各走一遍案例二（创建 → 依次回答三个问题到 v4 → 变化栏高亮只出现在真实变动的 1 栏 → 台账与交接单同步；右栏每条资源有名称与来源案例）。
- 通过后按总控安排进 F1，再按里程碑推进 F2/F3/F4。

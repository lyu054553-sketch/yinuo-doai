"""Bounded coding workers through the user's local Magpie gateway.

Workers can read project text and write only their assigned directories. The
coordinator runs commands and checks; model responses never execute shell code.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'orchestration/runs'
MODELS = {'deepseek': 'auto/deepseek-v4.1-flash', 'kimi': 'auto/kimi-k3.1-pro',
          'minimax': 'auto/minimax-m3.1-flash'}
TASKS = {
    'backend-m0': ('deepseek', ['backend/'], ['backend/app/media/', 'backend/tests/media/'],
                   'prompts/开发/A-后端核心.md',
                   '只完成 M0 和为 M0 必要的测试，不实现 M1/M2。完成后汇报并停止。'
                   '优先建立 schemas.py 与 store.py，再完成所有核心接口。'
                   '内部公共接口固定：app.store.load_session(id)->Session；save_session(session)->None；'
                   'app.config.settings 使用小写字段 mock_mode,llm_model,image_provider,vision_url,storage_dir；'
                   'storage_dir 为 pathlib.Path。schemas.py 导出 Session 及约定各结构的同名类。'
                   '媒体占位路由由你创建。只实现案例五的通用加载支持，不运行、观察、调试它。'
                   '测试仅用 c1,c2,c3,c4,c6。必须体现案例二三个追问、领宽设计师事项、v4、幂等、台账及交接单。'),
    'frontend-f0': ('deepseek', ['frontend/'], ['frontend/DESIGN.md'],
                    'prompts/开发/B-前端.md',
                    '完成 F0。先读 frontend/DESIGN.md（若存在），严格遵从其设计方向。'
                    '从空目录创建 Vite React TypeScript 项目；实现完整的案例二模拟流程，'
                    '类型与 client 覆盖所有约定接口。VITE_USE_MOCK=1 时可离线操作；默认连接真实后端。'
                    'mock 构造应来自脱敏开发集，不使用案例五。M0 完成后由总控安排 F1。'
                    '不要运行安装命令；写好 package.json，由总控安装及运行检查。'),
    'media-c0': ('deepseek', ['backend/app/media/', 'backend/tests/media/'], [],
                 'prompts/开发/C-出图与视觉.md',
                 '只完成 C0：确定性三组提示词、核对清单、独立映射数据表、单元测试。'
                 '暂不写 routers/media.py，也不调用模型、生图或视觉服务。'
                 '入口 build_prompts(spec_version) 接受约定 SpecVersion 或其 dict，返回约定 prompts 响应。'
                 '用案例二、六的真实规格生成结果，保存到本目录 C0_EXAMPLES.json 并汇报。'
                 'C0 模块不得依赖尚未创建的 app.schemas，以便独立测试。'),
    'design-k0': ('kimi', ['frontend/DESIGN.md'], [],
                  'prompts/开发/B-前端.md',
                  '只负责前端审美与交互设计，不写业务代码。将可执行设计规范写到 frontend/DESIGN.md。'
                  '给出1440x900三栏工作台的精确布局、字体、色值、间距、状态区分、空/错/加载、'
                  '选中原话映射高亮、改版动效、键盘焦点与降低动效策略。'
                  '专业海派定制业务工具，避免大面积渐变、花哨仪表盘和难读小字。'
                  '要求白纸、墨色、克制的服饰档案质感；状态色另设，不与装饰色混淆。'
                  '总控将交给 DeepSeek 实现。不要引用大型 UI 库或外部素材。'),
    'eval-d0': ('deepseek', ['eval/'], [], 'prompts/开发/D-评测.md',
                '只完成 D0，后端 M0 已由总控验证。评测必须通过 HTTP 调用，不能导入后端。'
                '只跑 c1,c2,c3,c4,c6，支持三次重复、原始输出、分子分母、版本/模型/耗时记录。'
                '问题句6句，准确核对数据。输出明确标注 MOCK仅检验脚本，不能作为真实效果。'
                '留出集 c5 无论什么参数都禁止本轮运行。不要造注入数据（D1待下一阶段）。'),
    'media-c1': ('deepseek', ['backend/app/media/', 'backend/tests/media/'], [],
                 'prompts/开发/C-出图与视觉.md',
                 '总控已实际确认C0：20项测试通过，make_c0_examples.py --check通过。现在只完成C1。'
                 '实现视觉客户端、冻结策略、数据表冲突检测及测试，暂不改路由，暂不实现C2生图核对。'
                 '复用现有schemas/config；接口只往Session.reference和ledger写约定结构。'
                 '读取vision/server_local.py和schema，实际响应含fields/diagnostics，需正确解包。'
                 '程序函数的调用方式写清楚，由总控执行真实c1图片加低领口述检查。'
                 '仅采用领型/闭合/纽扣/纹样，置信最高疑似，衣长/廓形/颜色used=false请人工确认。'
                 '视觉离线抛清楚中文异常供后续路由映射503。模型输出必须校验，未知或缺字段不能假装成功。'
                 '不调用云模型、不训练、不改vision、不改案例数据；完成C1后立即停。'),
    'backend-m1': ('deepseek', ['backend/'], ['backend/app/media/', 'backend/app/routers/media.py', 'backend/tests/media/'],
                    'prompts/开发/A-后端核心.md',
                    '总控已确认M0：23项单测和真实HTTP案例二三问到v4通过。现在只完成M1真实LLM审计，'
                    '不推进M2从零匹配。实现llm.py、pipeline/extract.py及必要审计逻辑；'
                    '统一兼容API、Pydantic严格JSON校验、仅JSON不合格重试一次、原话必须原文子串、'
                    '错误返回清楚中文，绝不能用标准答案冒充LLM。模型和地址从环境变量读取。'
                    '已有mock标注路径保持不受真实规则影响；现有所有接口、公共schema及store函数保持兼容。'
                    '程序规则不能将暗纹与暗花同义表述误报，也不能将不收腰当成收腰。'
                    '案例二应检出梅花软偏好被改写、宽松与微微收腰冲突，领宽待设计师。'
                    '可复用现有问题/交接逻辑。以HTTP或mock transport测试真实模式，禁止调付费云做自测，'
                    '总控随后通过用户授权Magpie做五个开发案例自检并记录耗时。'
                    '不写任何留出示例；完成M1后停止，不自行宣称真实效果通过。'),
    'eval-d1': ('deepseek', ['eval/datasets/', 'eval/WORKLOG.md', 'eval/review_injected.html', 'eval/validate_injected.py'], [],
                 'prompts/开发/D-评测.md',
                 '总控已实际跑完D0，mock15会话全部满分。现在仅完成D1待审核数据，不做任何评测调用。'
                 '创建20组新的新中式外套口述+八字段已有方案，埋0到2个问题，至少4组完全无问题。'
                 '覆盖软偏好被改写、宽松与收腰冲突、身形要求遗漏、无资源出处的虚构面料。'
                 '每个问题明确原文子串、对应字段、标准状态、原因和类别，方便人审核；不得以笼统审美问题当标准。'
                 '只能引用真实资源id；故意虚构面料必须明确标为测试注入，不能冒充真实资源。'
                 'JSON写eval/datasets/injected.json，输出eval/review_injected.html对照页（口述/方案/故意问题/正常对照），'
                 '离线可打开，中文清楚可审核；同时写简单离线校验脚本检查20组、至少4正常、每组0-2问题、八字段与原话子串。'
                 '不照抄或改写c5，不运行任何HTTP/模型评测，不修改D0脚本和已有报告。'
                 '完成后立即停止，WORKLOG明确等待用户审核，未获批准前不得用于D2。'),
    'eval-metricfix': ('deepseek', ['eval/run_eval.py', 'eval/test_metrics.py'], [],
                      'prompts/开发/D-评测.md',
                      '总控已运行D0 mock15会话满分，但发现文档与实现不一致：lcs_len的递推是最长公共子序列，'
                      'overlap_ratio注释和报告局限却称最长公共子串。仅修正这两处名称为最长公共子序列，保留实际算法。'
                      '使用replace_text工具作小改，不重写1200行文件。补eval/test_metrics.py：'
                      '甲乙丙 vs 甲丁乙戊丙的重叠应为3/5；重复预测同一原话只能一对一匹配；'
                      '留出c5参数必须被拒绝；虚构原话/不存在资源ID必须被检出。'
                      '禁止修改WORKLOG/datasets/reports/runs（另一代理正在造D1）；不做HTTP评测。'),
    'backend-m2': ('deepseek', ['backend/'], ['backend/app/media/', 'backend/app/routers/media.py', 'backend/tests/media/'],
                    'prompts/开发/A-后端核心.md',
                    'M1已由总控实测：5个开发案例真实Magpie HTTP通过，source=llm，案例二两关键问题检出，'
                    'M0+M1共38项单测通过。现在完成M2从零生成方案，不做部署。'
                    '实现程序资源匹配：硬约束过滤、软偏好排序、R1-R8身形处理、未提及字段默认值与skipped、'
                    '每栏真实资源ID或明确无出处；来源仅c5的候选继续排除。必要时扩展内部意图抽取结构，'
                    '但不能改冻结的Session/API结构。先提取原话/约束，再匹配，再审计当前初稿，不能拿空方案审计结果冒充最终状态。'
                    '现有审计与mock路径必须保持兼容，mock无sample_id仍返回400。'
                    '真实模式text存在而existing_spec为空时必须从零，不能因sample_id存在而偷偷拿案例旧方案；'
                    '审计路径通过明确existing_spec进入。补有/无sample_id两种从零测试。'
                    '用户硬要资源库没有的梅花等内容时标无出处，不编资源ID或库存。'
                    '顺手修复questions.py内搭选项编出的胸围+4cm：R8没有这个数值，改为预留松量、具体数值量体后确认；'
                    '不得填任何没有来源的具体量体尺寸。补回归测试。'
                    '只读必要文件，使用replace_text小改；不调用真实模型（总控随后实测），完成M2停止。'),
    'media-c2': ('deepseek', ['backend/app/media/', 'backend/tests/media/'], [],
                 'prompts/开发/C-出图与视觉.md',
                 'C1的38项客户端/规则测试由总控跑通；真实看图暂因旧虚拟环境dataless依赖卡读而待修，'
                 '总控正在另建环境，不改vision。现在并行实现独立C2效果图核对模块及测试。'
                 '先独立deconstruct图片，再程序比对C0的build_check_specs，不能把方案提示给视觉模型。'
                 '返回约定CheckResult；领型/闭合/纽扣/纹样置信最高疑似，衣长/廓形/颜色强制看不出。'
                 '缺观测或未知不能算符合；清楚区分符合/不符合/看不出。'
                 '提供适合C3路由调用的check_generated_image及记录核对到Session/GeneratedImage的函数；'
                 '禁止接路由、禁止付费生图。用fake transport/fixture覆盖符合与不符合；'
                 '写清实际c6图片对照c6规格与c1图片对照c6规格的检查命令，真实图片由总控执行。'
                 '复用C0/C1，不重复实现客户端，不改已有测试来掩盖结果；完成C2代码后停止。'),
    'media-c3': ('deepseek', ['backend/app/media/', 'backend/app/routers/media.py', 'backend/tests/media/'], [],
                 'prompts/开发/C-出图与视觉.md',
                 'C0/C1/C2程序部分已由总控验收：20+38+31项测试通过，真实c1已检出立领低领冲突，'
                 'c1独立观测对照c6规格领型与纽扣均不符合；真实c6正例正在总控执行。'
                 '现在完成C3路由集成和IMAGE_PROVIDER=none默认链路，同时实现可测试的兼容生图适配器但严禁真实付费调用。'
                 'GET prompts、POST reference multipart、POST images JSON或multipart按冻结接口返回Session。'
                 '只复用schema/load_session/save_session/config，不改核心文件。'
                 '保存上传/生成图片到settings.storage_dir/files的子目录，路径用安全随机文件名，通过/files访问。'
                 '校验图片真实内容、合理大小与kind；离线503中文；耗时视觉调用不能阻塞其他业务接口。'
                 'none时图片url=null，记录出图仅提示词台账，不发请求；外部图片上传独立核对并写ledger。'
                 '真实provider分支仅通过mock transport测试：不符合时重画最多一次、两次图与核对都保留。'
                 '模型名应从实际diagnostics.fields[*].source去重提取，若无信息再fallback地址，不能编造型号。'
                 '补真实后端路由测试（fake vision/provider，不做付费调用），不要改C0/C1/C2已通过逻辑。'
                 'C4仅写高立领、外露扣新中式外套参考图提示词到WORKLOG，不生成付费图。完成停止。'),
    'eval-d2': ('deepseek', ['eval/'], ['eval/datasets/', 'eval/review_injected.html', 'eval/validate_injected.py'],
                 'prompts/开发/D-评测.md',
                 '用户已明确批准D1并要求继续正式评测。授权及数据SHA256在orchestration/approvals/d1.json；'
                 '不能修改该记录或已批准的数据/标准答案。现在实现D2正式评测runner与必要测试，暂不自行发HTTP/模型调用，由总控执行。'
                 '5个开发案例+20注入组，各重复3次；衣诺API组与同一模型通用问法对照组均跑相同样本/重复。'
                 '参数支持base-url、Magpie兼容地址、模型、并发数（默认3）、timeout、resume。模型须等于health.llm_model，衣诺后端必须非mock。'
                 '默认Magpie http://127.0.0.1:3425/v1，目标auto/deepseek-v4.1-flash，key仅从环境读取；禁止打印key。'
                 '注入组只把customer_text+existing_spec发给被测系统，绝不发送expected_issues/test_injected/resources/标题类别等答案信息。'
                 '通用问法直接问未落实/矛盾，要求JSON列表、每条引用原文子串text+field+reason；不用领域规则/少样本/标准答案。'
                 '复用D0的重叠配对、状态、字段、引用校验函数；通用对照只比较问题检出，不把它仅列问题导致的拆句召回与完整系统混比。'
                 '记录每次原始请求/响应、目标及返回模型名、source/created_at、耗时、重复稳定性、失败。'
                 '失败不能悄悄丢出分母或声称稳定；以case/lane/repeat做检查点，可恢复成功项避免重复计费，不自动重试未知状态请求。'
                 '不编造/虚构原话/资源严重错误必须单列；无出处字段数量单列；误报按任务定义标准已落实，另可统计间接体现误报但不可混淆。'
                 '按开发集、注入集和对照分别给分子分母，逐条列18个注入问题与6个开发问题；说明开发集已见、注入集为团队自造、小样本。'
                 '不要提前写对外结论D4；c5始终拒绝，用户尚未宣布方案冻结。补离线单元测试验证数据规范化、baseline配对、失败保留分母及resume/审批hash门禁。'
                 '代码与运行命令完成后停止，正式执行由总控待后端M2稳定后运行。'),
    'backend-m3': ('deepseek', ['backend/'], ['backend/app/media/', 'backend/app/routers/media.py', 'backend/tests/media/'],
                    'prompts/开发/A-后端核心.md',
                    'M2已写，当前总控核心单测49通过2失败，修复并完成M3稳健性；不读eval注入数据以免污染评测。'
                    '失败1：test_m0.py媒体占位测试仍要求501，但C3已将prompts实现为200，更新为验证真实prompts结构/三类型/版本，不退回占位。'
                    '失败2：test_m2_from_zero.py规则测试把session JSON字典utterance当对象访问.mappings，改为字典访问，保留R1/R7/R8强断言。'
                    '从零初稿每栏资源ID或无出处：品类无资源ID时不能未经问答就标客户确认；改无出处并保留真实utterance_ids，'
                    '交接单可说明品类来自口述、资源库无独立品类项，避免让人去找品类样布。'
                    'LLM严格性：llm_models当前无效rule_id悄悄清成null，违反不合格不能硬凑；改为校验失败且最多重试一次，补bad->valid及两次bad错误测试。'
                    '核对extract提示词所称严重顺序与词表一致，不能颠倒。'
                    '补全未处理异常为中文JSON 500，不能泄漏key/堆栈给用户。'
                    '并发稳健性：store导出session_lock(id)重入锁上下文，save_session原子写；核心answers/spec读改写全程锁住，'
                    '避免同问题并发重复升版或两个不同修改丢失。媒体由另一个代理接入该锁，你不改媒体文件。'
                    '补有意义并发/错误/JSON测试，保留现有M0/M1/M2行为；不运行模型、不部署、不改data/vision。'
                    '仅删除明确未引用的本轮误建辅助文件可提请总控清理，不要写空文件充当删除。完成停止。'),
    'delivery-pack': ('deepseek', ['docs/交付/'], [],
                      'prompts/开发/A-后端核心.md',
                      '整理已有真实开发成果，不写新功能、不运行命令、不读取eval/runs或任何留出内容。'
                      '只创建docs/交付/能力边界清单.md、docs/交付/演示与交付手册.md、docs/交付/开发状态.md。'
                      '可读约定、orchestration/README.md、backend/frontend/media各自WORKLOG、eval/reports/d2_正式评测报告.md与d4_对外口径.md，'
                      '不得写任何未经总控验证的性能提升、生图或库存能力。'
                      '边界清单独立列出：静态案例参考不是库存；模型原话/字段判断可错；软偏好与硬约束区别；'
                      '仅自动检查原文/资源ID不代表零幻觉；无出处须人工找样；冻结视觉领型/闭合/纽扣/花纹最高疑似，衣长/廓形/颜色不采用；'
                      '案例六交领被误识别为立领，正例核对未通过；付费生图未启用；最终量体、尺寸、报价、制版、采购、下单由人决定。'
                      '演示手册需包含三条本机启动命令、8100真实模型和5174离线模拟区别、案例二三问到v4、手改v5、提示词/外部图核对/交接单路径。'
                      '明确上传c1成衣图加低领口述可演示疑似冲突；不要把冻结模型案例六误识别剪掉掩盖，若展示需人工复核。'
                      '不宣称已部署/发布/已生成付费图，交接单可复制但无docx/pdf导出按钮。'
                      '开发状态按已实现、已验证、未完成/需授权三部分写；D2数字旧版本32/54 vs33/54不可省略其局限，方案未冻结、留出集未运行。'
                      '文件给业务/比赛对接人看，少术语、不堆砌过程日志。完成后停止。'),
    'media-demo-capture': ('deepseek', ['backend/app/media/', 'backend/tests/media/'], [],
                           'prompts/开发/C-出图与视觉.md',
                           '只做一个有证据的本地视觉演示辅助，不改冻结vision/模型、data、核心schemas或路由接口，不调任何模型或付费生图。'
                           'C2真机实测c6交领被识别成立领，必须保留这个失败，不得用规则篡改观测或预期结果。'
                           '请读现有media check/vision_client/C4说明与测试，在backend/app/media/新增纯本地的演示素材清单JSON和检查函数/脚本：'
                           '整理已有c1图对c6方案的反例、c6图对c6方案的正例与未知状态；列出人工复核提示和原始证据路径orchestration/runs/c2-c1-counterexample.json、c2-c6-live.json。'
                           '脚本只验证这些本地证据存在/结构及生成演示核对摘要，不能声称视觉通过；不要把证据内容复制进代码或提示词。'
                           '若现有C4提示词中虚构5cm尺寸，改为“高立领、贴合颈部”等定性描述，不新增尺寸；其他不变。'
                           '新增离线合成测试，证明原始观察不符不能被自动改成符合、无证据返回待采集而不是通过。'
                           '不要调用真实视觉服务，由总控另外实测。完成停止，不更新总控文件。'),
    'backend-audit-refine': ('deepseek', ['backend/app/pipeline/audit.py', 'backend/tests/test_audit_constraints.py', 'backend/WORKLOG.md'], [],
                             'prompts/开发/A-后端核心.md',
                             'D2首轮已完成，当前修通用逻辑缺口，禁止读取eval/datasets、eval/runs、eval/reports或真实留出内容；不按评测组名/句子硬编码。'
                             '只修改audit.py及新测试/WORKLOG，不改extract.py、其他后端文件。独立代理会改抽取提示词，互不覆盖。'
                             '先读audit.py、knowledge.py、schemas.py和现有相关测试；规则目前把“不要红色/不要梅花”当成正向需求，'
                             '颜色二选一又要求所有词都出现，属于可复现产品错误。修正正负极性：否定颜色/纹样缺席不误报，出现在方案才冲突；'
                             '要区分方案“无红色/不含梅花”等否定描述。替代选择如“米白或浅灰都可以”满足一个即可；'
                             '明确组合如“米白和深灰拼色”才要求两个；多约束别只取首个pattern组。保留暗纹/暗花同义、软偏好被换需确认、硬冲突、领宽规则与mock不经过审计的行为。'
                             '材质出处：已有方案里若写了具体材料名称，却无法在可推荐资源的名称/别名中对应，应让涉及面料的正常映射提示“需设计师判断：资源库暂无对应参考，请核实来源”，'
                             '不能断言现实中不存在或是虚构。只限有具体材料名的情形，纯属性描述“哑光、有肌理、挺括”等不因缺resource id而被误报；'
                             '已是冲突/被改写/未落实的映射不得被降级，也不得凭空新增客户原话或改existing_spec值。仅看开发可推荐资源，不把来源仅留出的条目当白名单。'
                             '若无法稳妥判断是否具体料名，保守交模型已有判断，不靠任何评测特殊词表。'
                             '用新的合成口述与假抽取/单元验证上述行为；检查已知名称别名、未收录样布名、纯属性、否定/二选一/拼色、不要某纹样但另一纹样可用。'
                             '不运行模型/命令，不更新D2报告/数据，不修改data/vision，不新增接口。完成停止，真实自检由总控运行。'),
    'backend-extract-context': ('deepseek', ['backend/app/pipeline/extract.py', 'backend/tests/test_extract_context.py'], [],
                               'prompts/开发/A-后端核心.md',
                               '只改extract.py及新测试，不改audit.py/其他文件/WORKLOG；并行代理在修audit规则。'
                               '总控发现模型把同一设计要求的条件、否定、允许选项、目标拆成失去关联的短句；改通用提示要求以“可独立审计的一项要求”为单位，'
                               '保留紧密相连的原因/条件与要求/允许替代项为一个连续原文片段，不机械按逗号拆分。'
                               '不要把整个口述合成一句；不同设计参数或单品仍分开，例如领高和领宽是两个要求，即使属于同一领型字段。'
                               '禁止代码里强行拼接合并模型句子、禁止按字符长度凑阈值，原话span严格校验保留，合同/输出字段不变。'
                               '区分“或/任意/二选一”允许替代与“和/拼色”组合，否定不能反向当成要；强调只因未知材料名不能断言现实不存在。'
                               '审计与从零两种提示均统一语义单位；from_zero仍只抽取不判断落实。少样本仍只用原有开发案例，不加评测句子或留出内容。'
                               '禁止读取任何eval数据/输出/报告；只用全新合成句子验证prompt清楚携带语义单位规则，保留原文子串、禁止拼接、不同参数单品分开等关键约束。'
                               '新测试只检查有意义的提示行为和fake transport送出内容，不用固定长大prompt快照，不自称真实效果已提高。'
                               '完成立即停止，总控再跑真实独立口述自检，现有D2数字保持为旧版本结果。'),
    'eval-d4': ('deepseek', ['eval/reports/d4_对外口径.md'], [],
                'prompts/开发/D-评测.md',
                'D2完整正式结果由总控运行后才能处理。本任务启动时只读eval/reports/d2_正式评测报告.md及'
                'eval/reports/d2_总控复核.json，产出D4对外口径3–5条，每条均有分子分母、样本量、局限。'
                '诚实呈现系统相对通用问法的好坏；若注入集更差必须明确写，不得包装成性能提升，不删误报/漏检。'
                '每例3次的统计不能把18个独立注入问题说成54个独立问题；强调5开发案例已见、20组自造、未运行留出集。'
                '0编造违规仅指检查到的原文子串/位置和资源ID存在性，不能写成零幻觉；规则与资源库只提供衣诺系统，通用问法只看口述与方案，这是系统方案比较不是模型能力排名。'
                '有问题句的逐句匹配用LCS>=0.5一对一，拆分粒度会影响数字；不报显著性。'
                '同时列出不能对外宣称的说法，例如泛化已经验证、全指标领先、视觉正例全通过、真实库存或已启用付费生图。'
                '不要改D2报告/数据/WORKLOG/脚本，不发模型请求，不读取案例五，完成停止。'),
    'frontend-mock-linkage': ('deepseek', ['frontend/src/mocks/case2.ts'], [],
                              'prompts/开发/B-前端.md',
                              '总控截图复查发现离线模拟仍有实际同步缺口；只改frontend/src/mocks/case2.ts，不改fixture/后端/WORKLOG。'
                              '先读此文件及只读backend/app/pipeline/revise.py和handoff.py，按已完成后端的语义对齐。'
                              '1回答后mapping.note仍显示旧冲突，请更新为客户确认+真实选项；同题同选项重复应幂等返回，不抛错不升版。'
                              '2手动patch目前不更新相关问题原话；对齐核心人工修改逻辑，只处理本字段的冲突/改写/未落实，保留其他字段未决问题和设计师事项，'
                              '移除本字段被手改解决的未答问题，保留已答历史；无resource匹配的新值不能保留旧resource_ids冒充出处，不设置伪造resolved_by。'
                              '3buildHandoff现在6个标题不是约定的6部分，缺待办、打样核对项、不由系统决定事项。'
                              '按核心真实handoff逻辑生成当前方案、客户确认、设计师事项、待办、打样核对、不由系统决定，保留真实选项todo与无出处线索，'
                              '可读data/workflow.json，但不得编造量体/尺寸/库存/已确认信息。不需要完全逐字同版式，内容满足契约。'
                              '既有函数导出与类型保持不变，其他文件不动。离线示例仍用可信fixture。完成停止，由总控构建和浏览器检查。'),
    'eval-d3-prepare': ('deepseek', ['eval/run_holdout_once.py', 'eval/test_holdout_once.py', 'eval/HOLDOUT_RUNBOOK.md'], [],
                        'prompts/开发/D-评测.md',
                        '只准备D3的一次性运行脚本、离线合成测试和手册，不执行任何请求，不读取/评测案例五。'
                        'D2正在运行，禁止修改现有D2脚本/数据/报告。可以读已有评测纯函数和五开发案例了解结构。'
                        '新脚本运行时必须先验明确用户方案冻结授权文件（默认orchestration/approvals/freeze.json，当前不存在），'
                        '字段decision=approved、scope=scheme_freeze_and_single_holdout、scheme_frozen=true、holdout_authorized=true、'
                        '非空user_message、backend_aggregate_sha256、model、recorded_at，任何缺失拒绝；不得根据D1审批推断冻结。'
                        '检查实际backend/app源文件快照sha256与批准快照一致且health非mock/模型一致；只在所有门禁通过后才加载留出输入。'
                        '仅一次POST /api/sessions审计c5，不跑baseline、不重复3次；用已有纯评分函数处理标准答案。'
                        '首次发POST前以O_EXCL原子写durable latch，后续任何运行都拒绝，无论原请求成功/失败/超时；未知不能自动重试。'
                        '原始请求、响应、失败、耗时、source与code快照保留到eval/runs/holdout-once，单独报告到eval/reports/d3_留出集报告.md，失败不能写通过。'
                        '本机尚未冻结，只写代码；dry-run也禁止加载c5内容和发请求，可检查授权文件不存在而明确拒绝。'
                        '测试必须用临时目录与合成case/假HTTP，证明缺授权不读取data、错误hash拒绝、仅一次调用、超时也保留latch、mock拒绝。'
                        '不放任何真实留出内容/预期指标到提示词/测试；完成停止，总控运行离线测试。'),
    'frontend-handoff': ('deepseek', ['frontend/src/styles/app.css'], [],
                         'prompts/开发/B-前端.md',
                         '只修交接单表格的可读性：总控实际1440屏截图显示右栏约400px宽，'
                         'handoff-doc表格首列“设计字段”和末列“客户已确认”被挤成逐字竖排。'
                         '请先读对应CSS，用很小的局部样式调整列宽或换行策略，让中文表头和八字段名称便于阅读；'
                         '不要改变数据、文案、组件行为、其余页面。维持窄屏不产生页面横向溢出，必要时表格容器内部滚动。'
                         '只能改frontend/src/styles/app.css，不改WORKLOG。完成立即停止；总控构建与截图。'),
    'frontend-finish': ('deepseek', ['frontend/'], ['frontend/DESIGN.md'],
                       'prompts/开发/B-前端.md',
                       'F0/F1两模式三问v4、台账交接及1280/390无横向溢出已由总控验证，设计继续遵从Kimi的DESIGN.md。'
                       '现在完成F2/F3/F4收尾，不改后端，不读eval注入数据。'
                       '刷新模拟数据，禁止手工改写真实来源：copy_file backend/storage/sessions/s7051091d5586.json '
                       '到frontend/src/mocks/case2.fixture.json（已去除无依据胸围+4cm）；'
                       'copy_file backend/storage/fixtures/dev-samples.json和dev-resources.json到对应mocks fixture，'
                       '让samples.ts/resources.ts直接导出可信JSON，保留既有类型/接口。只读五个开发案例，不加入留出内容。'
                       'ImagesTab存在重复React key：prompts.checks多项field=版型结构，check.items也重复field；'
                       '用field+index或稳定检查键避免重复，不能删检查项。'
                       '核对项显示真实verdict符合/不符合/看不出与confidence疑似等，而非混成原话状态；'
                       '加简短人话提示：视觉只提供疑似线索，不符合也需人工复核，衣长/廓形/颜色请人工确认。'
                       'image.provider不要显示none/openai_compatible等实现值，换成仅提示词/外部上传/生图服务中文说明。'
                       '补四页签方向键/Home/End切换及可见焦点；隐藏file input的label需focus-within可见。'
                       '真实媒体API已有：GET prompts三组可复制、none出图记录、上传外部图核对、参考图冲突；既有client保持接口不变。'
                       '人工改字段原因必须填写，相关原话/待问由后端返回，前端不要自行推断确认。'
                       '不重写整体样式，不新增大库，不调用模型/生图。更新WORKLOG后停止，由总控构建、截图、剪贴板与两模式验收。'),
}


def project_path(path: str) -> Path:
    p = (ROOT / path).resolve()
    if not p.is_relative_to(ROOT) or '.git' in p.relative_to(ROOT).parts:
        raise ValueError('路径不在项目可访问范围')
    if any(part.startswith('.env') and part != '.env.example' for part in p.parts):
        raise ValueError('不得读取或写入真实环境配置')
    return p


def in_scope(path: str, patterns: list[str]) -> bool:
    return any(path.startswith(p) if p.endswith('/') else path == p for p in patterns)


def read_project(path: str) -> str:
    p = project_path(path)
    if p.suffix.lower() not in {'.md', '.json', '.py', '.toml', '.txt', '.ts', '.tsx', '.js', '.css', '.html', '.example', '.cfg', '.ini', '.yml', '.yaml'}:
        raise ValueError('只允许读取项目文本文件')
    if not p.is_file():
        raise ValueError('文件尚不存在')
    if p.stat().st_size > 250_000:
        raise ValueError('文件过大，请缩小读取范围')
    text = p.read_text(encoding='utf-8')
    if path == 'data/cases.json':
        data = json.loads(text)
        data['cases'] = [c for c in data['cases'] if c['id'] != 'c5']
        data['meta'] = {'note': '仅开发集，留出集已由总控隔离'}
        return json.dumps(data, ensure_ascii=False)
    if path == 'data/resources.json':
        data = json.loads(text)
        for key, rows in data.items():
            if isinstance(rows, list):
                data[key] = [dict(row, sources=[s for s in row.get('sources', []) if s != 'c5'])
                             for row in rows if any(s != 'c5' for s in row.get('sources', []))]
        return json.dumps(data, ensure_ascii=False)
    if path.startswith(('data/images', 'vision/data', 'vision/results', 'prototype/', 'orchestration/runs')) and path != 'vision/results/benchmark_v2_report.md':
        raise ValueError('该材料不在开发工作包中；需要时由总控单独提供')
    return text


TOOLS = [
    {'type': 'function', 'function': {'name': 'read_file', 'description': '读取项目文本，标准案例只返回开发集。',
      'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}},
    {'type': 'function', 'function': {'name': 'list_files', 'description': '列出指定目录直接子项，不递归。',
      'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}},
    {'type': 'function', 'function': {'name': 'write_file', 'description': '写完整文本内容，只能写分配给本任务的路径。',
      'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']}}},
    {'type': 'function', 'function': {'name': 'copy_file', 'description': '把可读项目文本原样复制到本人负责目录，适合复制已验收JSON样例，避免重新编造。',
      'parameters': {'type': 'object', 'properties': {'source': {'type': 'string'}, 'path': {'type': 'string'}}, 'required': ['source', 'path']}}},
    {'type': 'function', 'function': {'name': 'replace_text', 'description': '精确替换已有文件中的文字，不需重写整个文件。匹配次数不符则拒绝修改。',
      'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'expected_occurrences': {'type': 'integer'}}, 'required': ['path', 'old_text', 'new_text', 'expected_occurrences']}}},
]


def execute(name: str, args: dict, allowed: list[str], excluded: list[str]) -> str:
    path = args['path']
    p = project_path(path)
    path = p.relative_to(ROOT).as_posix()
    if name == 'read_file':
        return read_project(path)
    if name == 'list_files':
        return json.dumps(sorted(x.name for x in p.iterdir() if not x.name.startswith('.')), ensure_ascii=False)
    if name == 'copy_file':
        return execute('write_file', {'path': path, 'content': read_project(args['source'])}, allowed, excluded)
    if name == 'replace_text':
        old = args['old_text']
        content = read_project(path)
        if not old or content.count(old) != args['expected_occurrences'] or args['expected_occurrences'] < 1:
            raise ValueError('精确匹配次数不符，文件未修改')
        return execute('write_file', {'path': path, 'content': content.replace(old, args['new_text'])}, allowed, excluded)
    if name == 'write_file':
        if not in_scope(path, allowed) or in_scope(path, excluded):
            raise ValueError('写入越过本任务目录边界')
        if any(part in {'node_modules', 'storage', '__pycache__'} for part in p.parts):
            raise ValueError('不得通过此工具写入运行数据或依赖目录')
        if p.suffix.lower() not in {'.md', '.json', '.py', '.toml', '.txt', '.ts', '.tsx', '.js', '.css', '.html', '.example', '.cfg', '.ini', '.yml', '.yaml'} and p.name != '.gitignore':
            raise ValueError('只允许写入源代码文本')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args['content'], encoding='utf-8')
        return json.dumps({'written': path, 'characters': len(args['content'])}, ensure_ascii=False)
    raise ValueError('未知工具')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', choices=TASKS)
    parser.add_argument('--instruction', default='')
    parser.add_argument('--rounds', type=int, default=48)
    parser.add_argument('--effort', choices=['low', 'high', 'max'], default='low')
    parser.add_argument('--resume', type=Path, help='Continue a terminal run from its saved responses')
    args = parser.parse_args()
    role, allowed, excluded, prompt, milestone = TASKS[args.task]
    model = MODELS[role]
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = RUNS / f'{stamp}-{args.task}'
    folder.mkdir(parents=True, exist_ok=False)
    rules = ('你是衣诺项目的独立执行代理。通过工具实际写文件，不要只输出代码块或计划。'
             '你没有 shell 工具；安装、运行与验收由 GPT 总控完成，禁止声称未运行的检查通过。'
             '不运行 git，不调用任何外部模型或付费服务，不改 data/ vision/ prototype/ docs/。'
             '病例五是留出集，禁止开发和测试使用。资源是案例参考，不是库存。'
             '必须读约定和自己的任务文件，完成当前里程碑立即汇报停止。'
             f'允许写入 {allowed}；禁止写入 {excluded}。\n'
             + read_project('docs/开发/00-总体说明与接口约定.md') + '\n' + read_project(prompt))
    messages = [{'role': 'system', 'content': rules},
                {'role': 'user', 'content': milestone + '\n' + args.instruction}]
    total = {'prompt_tokens': 0, 'completion_tokens': 0}
    if args.resume:
        old = args.resume.resolve()
        if not old.is_relative_to(RUNS.resolve()):
            parser.error('恢复目录必须在 orchestration/runs 内')
        for saved in sorted(old.glob('*-response.json')):
            response = json.loads(saved.read_text())
            message = response['choices'][0]['message']
            messages.append(message)
            for key in total:
                total[key] += response.get('usage', {}).get(key, 0)
            for call in message.get('tool_calls') or []:
                fn = call['function']
                try:
                    output = ('此前写入已执行；如需确认请重新读文件。' if fn['name'] in {'write_file', 'copy_file', 'replace_text'}
                              else execute(fn['name'], json.loads(fn['arguments']), allowed, excluded))
                except Exception as exc:
                    output = json.dumps({'error': str(exc)}, ensure_ascii=False)
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
        messages.append({'role': 'user', 'content': '上次请求以网关 502 结束。继续当前里程碑；不要重新遍历项目。'
                         '先分批写入核心文件，每次最多约300行，禁止再读不相关材料。' + args.instruction})
    base = os.environ.get('MAGPIE_BASE_URL', 'http://127.0.0.1:3425/v1').rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('MAGPIE_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['MAGPIE_API_KEY']
    print(json.dumps({'task': args.task, 'model': model, 'run': str(folder)}, ensure_ascii=False), flush=True)
    for round_no in range(args.rounds):
        body = {'model': model, 'messages': messages, 'tools': TOOLS, 'tool_choice': 'auto',
                'max_tokens': 18000, 'stream': False, 'reasoning_effort': args.effort}
        req = urllib.request.Request(base + '/chat/completions', data=json.dumps(body).encode(), headers=headers)
        # Do not retry uncertain requests: the coordinator inspects their live state.
        try:
            with urllib.request.urlopen(req, timeout=900) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            status = {'task': args.task, 'model': model, 'http_status': error.code, 'state': 'failed'}
            (folder / 'failure.json').write_text(json.dumps(status))
            print(json.dumps(status), flush=True)
            raise SystemExit(1) from None
        (folder / f'{round_no:03d}-response.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
        for key in total:
            total[key] += data.get('usage', {}).get(key, 0)
        message = data['choices'][0]['message']
        messages.append(message)
        calls = message.get('tool_calls') or []
        if not calls:
            result = {'task': args.task, 'model': model, 'usage': total,
                      'finish_reason': data['choices'][0].get('finish_reason'), 'summary': message.get('content')}
            (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if data['choices'][0].get('finish_reason') == 'length':
                sys.exit('模型输出截断，任务未完成')
            return
        for call in calls:
            fn = call['function']
            try:
                result = execute(fn['name'], json.loads(fn['arguments']), allowed, excluded)
            except Exception as exc:
                result = json.dumps({'error': str(exc)}, ensure_ascii=False)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result})
            print(json.dumps({'round': round_no, 'tool': fn['name'], 'result': result[:180]}, ensure_ascii=False), flush=True)
        (folder / 'usage.json').write_text(json.dumps(total))
        (folder / 'messages.json').write_text(json.dumps(messages, ensure_ascii=False))
    sys.exit('达到轮数上限，保留已写文件，任务等待总控审阅')


if __name__ == '__main__':
    main()

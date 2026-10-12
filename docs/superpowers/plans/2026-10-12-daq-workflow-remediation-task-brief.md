# 数采 FAE 工作流与回答质量修复任务书

> **执行方式**：按任务顺序逐批实施，先写失败合同并保存原始失败，再修改代码、跑相关回归、提交。任务书是执行与验收基线；勾选项只有取得对应证据后才能改为完成。

**日期**：2026-10-12

**状态**：Dev 验收通过待发布；R0–R6 及 R7 本地门已取得证据，生产构建与发布核验仍待执行。
**目标**：让 DAQ FAE 对服务说明、产品事实、混合咨询和跨轮问题使用正确的请求目标与证据合同，修正当前知识检索和 trace 错位，并通过真实 Dev 回放及独立复审证明用户所见问题及其泛化问题已经解决。

**依据**：[工作流架构对照](../../reviews/2026-10-12-daq-workflow-architecture-audit.md)、[数采完整设计](../../2026-10-08-数采FAE完整设计.md)、原[开发任务书](2026-10-08-data-acquisition-fae-development-task-brief.md)的 A-2/A-4/V-1/V-2 门，以及相邻相机仓 `AI_FAE_Agent_工作流设计.md`。本任务书针对已经部署的内部 DAQ 试点做增量修复，不重做身份、数据库或知识发布系统。

**架构选择**：保留 DAQ 独立装配、知识版本和已固定的通用 `LoopRuntime`。新增 DAQ 自己的请求 schema 与目标账本，让认证聊天、本地 Dev 和 Platform Task 使用同一领域决策；服务元信息来自版本化服务合同，产品结论仍由按角色过滤的已发布知识证明。相机 `Orchestrator` 不作为 DAQ 入口，legacy Capability Planner 不接回默认路径。

**技术栈**：Python/FastAPI、Pydantic、现有 `AnthropicAdapter`/`LoopRuntime`/`DaqEvidencePolicy`、PostgreSQL DAQ schema、pytest、React/Vitest；真实回答模型与网关沿用 DAQ Dev 的 Opus 5.5 adaptive/high、`submit_only_auto` 配置。

**执行证据**：[修复前 RED 与旧合同基线](../../reviews/2026-10-12-daq-workflow-remediation-baseline.md)、[Dev 全量回放与独立答案复审](../../reviews/2026-10-12-daq-workflow-remediation-dev-review.md)。修复代码提交 `8e1af259a377f3dde65c8158fd11797363bcb69a`；本节状态只说明 Dev 结果，不代表线上已运行此提交。

## 0. 不可变边界与完成定义

- 真实模型只在 **Dev** 测试；生产不跑自动评测。代码提交、推送、镜像构建、知识快照切换和生产部署分别记录，不互相冒充。
- 当前本机活动知识基线须按发布清单核实：release `a63baf103892250b8474ad9e464b8df47c889e020313e37dd71ad815af9d55a6`，格式 2、23 记录（6 实体、17 规格）、0 章节。既有发布记录称线上使用同一 release；实施时重新核对线上 build/release。若活动指针改变，先冻结新旧 manifest 与角色视图，再更新实际回放身份。
- `tmp/`、未审核候选章节和用户附件不能成为产品事实；产品/变体、组合、软件、链接、冲突、角色可见性保持现有治理门。服务身份、能力范围和本次发布覆盖由版本化应用合同及 release manifest 表述，不能从产品目录的空命中倒推。
- 保留现有同一 PostgreSQL 数据库中的 DAQ 独立 schema、身份/内容密钥、entitlement、请求幂等和事务提交。不得为这次回答修复改动相机会话或知识。
- 用户已取消的独立人工事实/权限签字、异机原件复制和独立发布签名不作为本任务书新增门；来源、版本、角色过滤和 Dev 独立答案复审仍按现行合同验证。
- 若改 `src/` 或相机共用的前端模块，先在相机仓完成相关单测、合同与 Dev 质量回归，确认选定提交已有受保护的持久 Git ref，再更新 DAQ pin；若只改 `daq_fae/` 和 DAQ WebUI，也须验证两仓 `src/` SHA、相机入口与健康没有被发布流程改变。
- 完成的含义是：**失败样本重现 → 各层合同通过 → 原 24 题无严重退化 → 新冻结题集 Dev 真模型全部逐题独立复审通过 → 实际入口的 SSE/历史/trace 一致 → 性能与版本证据完整**。仅有模型答出一句正确自我介绍或自动分数通过，不算完成。

## 1. 文件与接口地图

| 路径 | 任务中的职责 |
| --- | --- |
| `daq_fae/request_schema.py`、`prompts/daq_schema_extraction.md`（新） | 定义并抽取 DAQ 本轮目标、子目标、实体/条件、语义置信度、上下文/附件依赖；模型结果必须校验，失败显式归因 |
| `daq_fae/task_context.py`、`daq_fae/durable_state.py` | 把经确认的任务条件、待确认项、主题切换和有效问句贯通到 checkpoint；用户断言与产品事实保持分离 |
| `daq_fae/service_contract.py`（新） | 版本化服务身份、可协助的任务范围及当前 release 覆盖摘要；不含未经发布的产品断言 |
| `daq_fae/domain_evidence.py`、`daq_fae/domain_tools.py` | 按子目标建立证据要求与状态转移；修复记录型 release 的泛检索路由，保持章节相关性不等于 typed 事实 |
| `daq_fae/workflow.py`（新）、`daq_fae/authenticated_chat.py`、`daq_fae/app.py`、`daq_fae/platform_tasks.py` | 统一 DAQ 领域控制流、错误归因、阶段事件；各入口只处理自己的身份、附件、持久化与运输差异 |
| `webui/src/thinkingSteps.ts`、`webui/src/sessionNav.ts` | 按真实 stage status/outcome 展示进度、弃答和 fallback；DAQ 侧改动不静默改变相机前端 |
| `tests/test_request_schema.py`、`tests/test_task_context.py`、`tests/test_domain_evidence.py`、`tests/test_reviewed_knowledge.py`、`tests/test_authenticated_chat.py`、`tests/test_platform_task_adapter.py`、`tests/test_trace_persistence.py`、`webui/src/*.test.ts` | 领域合同、实际认证路径、知识发布视图、任务路径、阶段持久化和前端回归 |
| `evals/daq_workflow_20261012.jsonl`（新）、`scripts/run_daq_workflow_dev_replay.py`（新）、`daq_fae/knowledge/dev_evaluation.py` | 冻结泛化题、通过**实际 DAQ 编排**而非直接调用 Loop 的 Dev 回放，以及独立复审记录校验；保留旧 D4 v1 合同 |

接口约束：`DaqRequestSchema` 给出 `request_kind`、有稳定 ID 的 `goals`、`original_question`、`effective_question`、`entity_scope`、`topic_switch`、`attachment_dependency`、`confidence` 和 `fallback_reason`；`DaqEvidencePolicy` 接受与每个 goal 一一对应的证据需求，记录 `satisfied/missing/conflict/unknown`。`service_contract` 只能满足服务目标，不能满足产品目标；混合请求的总体 `resolved` 需要全部关键目标满足。

## 2. 执行任务与依赖

### R0：冻结原始失败与既有回归基线

**依赖**：无。**交付物**：带版本和哈希的失败/基线记录，不能被修复后结果覆盖。

- [x] 保存当前 DAQ SHA、相机 SHA、两仓 `src/` tree、活动知识 manifest、模型/提示词配置标识；不记录密钥。
- [x] 先在测试中复现：服务介绍被规划成产品检索、format 2/零章节下 `search_knowledge("EGO")` 未命中而 `catalog("EGO")` 命中、认证 SSE 有 `loop=running` 无完成、历史 turn 缺完整阶段。测试必须在旧实现上 **RED**；保存测试输出与当前用户提供的线上样本。
- [x] 跑既有核心合同作为 GREEN 基线：`DAQ_KNOWLEDGE_RELEASE_ROOT=/tmp/daq-fae-empty-release-test-20261012 .venv/bin/python -m pytest -q tests/test_task_context.py tests/test_domain_evidence.py tests/test_reviewed_knowledge.py tests/test_authenticated_chat.py tests/test_platform_task_adapter.py tests/test_trace_persistence.py`；执行前确认该测试根目录没有 `active.json`，记录通过数与失败原因。活动发布视图另用显式 manifest 测试，不能让测试默认应用意外加载活动 release。
- [x] 冻结下文第 3 节的泛化题集和预期 outcome，计算文件 SHA；旧 24 题原样保留，修改题目或预期必须产生新 suite 版本。

**验收**：至少三个独立失败由外部合同稳定重现；记录中可以从具体版本追到输入、预期、实际、trace/阶段，不以“模型偶尔这样答”代替复现。

### R1：实现 DAQ 请求 schema 与有效问句

**依赖**：R0。**改动**：`request_schema.py`、DAQ schema prompt、`task_context.py` 及测试。

- [x] 先写失败测试：服务、产品、混合、未知、中文改写、英文、短追问、显式切题、附件相关与无关、低置信度/无效 JSON/Provider 400。断言的是结构化目标和来源边界，不是某一句固定答案。
- [x] 模型抽取只提供候选；程序校验目标 ID、实体/条件、主题关系与附件依赖。服务元信息、产品事实、现场咨询、用户附件、边界问题可组合，不能把未分类请求静默当作 `search_knowledge`。
- [x] 形成 `effective_question` 与 `context_fields`；用户的“可能/猜测”仍是用户断言，未经核实的 Platform 摘录不能写入权威会话或证据来源。上下文不足时保留 `unknown` 并提出最小澄清，而不捏造产品实体。
- [x] 给 schema 提供可替换的测试适配器；模型 JSON/校验失败标记真实 fallback，HTTP 400 等配置/协议错误不能伪装为业务缺证或 503。

**验收**：`tests/test_request_schema.py` 与扩展后的 `tests/test_task_context.py` 先 RED 后 GREEN；同义表达得到等价目标，混合请求得到两个子目标；原有用户断言、变体、附件和 Task hint 测试保持通过。

### R2：按目标建账与服务合同

**依赖**：R1。**改动**：`service_contract.py`、`domain_evidence.py`、`task_context.py`、提示词及测试。

- [x] 先写失败合同：纯服务目标在没有产品来源时可以 `resolved`；服务+产品目标只有服务可证时总体不能 `resolved`；产品值无匹配来源、变体未选、冲突、未授权链接与用户附件都不能被服务合同补成已满足。
- [x] 服务合同包含 Agent 身份、面向内部 FAE 的任务类别和**从活动 release 得出的覆盖摘要**，绑定运行/知识版本与角色；只证明服务自身能力与当前资料范围，不许写入产品参数、兼容结论或凭空 URL。
- [x] 需求 ID 由目标、实体、变体、字段、适用条件和证据类别稳定产生。工具结果按精确 ID 更新状态；已尝试且明确无可见发布证据为 `missing`，未尝试或无法判定为 `unknown`，冲突单列。模型额外发现的关键子目标须进入账本或禁止据此交付新的肯定主张。
- [x] 终稿分别保留服务部分和产品缺口；仅所有关键目标满足时 `resolved`。`safe_abstained` 可以交付有据的局部内容，但不得把局部内容表述成整体已解决。来源路径只在结构化 `sources`。

**验收**：`tests/test_domain_evidence.py` 对纯服务、纯产品、混合、附件、冲突和错误逐项 RED→GREEN；原 24 题的无据 `resolved` 禁令不放松。不得用“自我介绍固定文本”绕开账本。

### R3：修复活动 release 的泛检索契约

**依赖**：R0；可与 R1/R2 独立开发，但合入后一起回归。**改动**：`domain_tools.py`、必要的 release 装载/审计代码、测试。

- [x] 先用真实活动 manifest 的只读副本写失败测试：格式 2、23 记录、零章节时，泛检索 EGO 能返回有权限的记录候选；原实现应 RED。
- [x] 明确记录和章节的检索选择：有章节可搜索/精读章节，记录型发布仍可检索可见记录；若两者并存，结果标明类型、范围、版本且去重。零章节不等于整个 release 为空。
- [x] 发布/装载审计发现工具声明与实际可检索资产不一致时失败或给出明确诊断。自然语言章节命中只能作线索，不能自动满足参数、兼容和链接的 typed 需求。
- [x] 覆盖角色过滤、冲突、变体选择、路径脱敏和真正空知识 release 的既有行为。

**验收**：`tests/test_reviewed_knowledge.py`、`tests/test_domain_evidence.py`、工具合同与活动 manifest 测试 RED→GREEN；`search_knowledge("EGO")` 命中已发布可见记录，而纯服务问答不借产品检索取证。

### R4：统一 DAQ 领域编排与会话提交

**依赖**：R1/R2/R3。**改动**：`workflow.py`、三个入口、`durable_state.py` 与测试。

- [x] 先写入口合同：同一请求在本地 Dev、认证聊天和已启用的 Platform Task 形成等价 schema/需求/终态；身份、附件与持久化仅是入口差异。未启用 Task 不得声称已通过真实渠道验收。
- [x] 将 DAQ 的顺序集中为“身份/角色 → 会话 → guardrail → schema/接地 → 需求 → Loop → 覆盖/终稿 → 持久化”。红线在进入模型 Loop 之前完成；`loop_started` 只在实际启动 Loop 时发出。
- [x] Checkpoint 存经确认任务条件、待确认项、有效问句所用锚点和必要的状态摘要；切题清除不适用项。所有状态仍按用户断言、Platform 提示、发布知识和附件观察区分权威等级。
- [x] 认证回答、会话、DAQ checkpoint、阶段事件和重放终态继续同事务提交；重复 `client_request_id` 不重复执行模型，断线/授权变化不泄漏内容。

**验收**：`tests/test_authenticated_chat.py`、`tests/test_authenticated_atomic_completion.py`、`tests/test_platform_task_adapter.py` 和本地入口合同 RED→GREEN；两轮追问与切题测试检查答案、账本和持久状态，不只检查模型看到历史文本。

### R5：真实阶段、trace 与前端终态

**依赖**：R4。**改动**：三个入口、`tests/test_trace_persistence.py`、DAQ WebUI 的 `thinkingSteps.ts`/`sessionNav.ts` 及测试。

- [x] 先写失败测试：正常结束有未闭合 `running`、红线拒答却显示 Loop、工具已完成仍显示 running、历史只存工具调用、`safe_abstained` 显示 `answered · no sources`。测试应在旧实现 RED。
- [x] 每个真实阶段发送有同一阶段 ID 的 started/completed 或 error、耗时和可脱敏元数据；schema 阶段记录原问/有效问、目标类别与作用域；覆盖阶段记录需求状态，终态记录 outcome/fallback/版本。认证 trace 安装请求上下文，前端阶段与后端 trace 使用同一 `trace_id`。
- [x] 持久化完整可交付阶段事件；恢复历史只显示真实保存的事件，不合成“已取证”之类未发生阶段。前端依 `status` 和 `outcome` 显示：`safe_abstained`、`resolved`、运行失败、授权失败各不同；`fallback_used=false` 只代表无运行降级。
- [x] 不通过伪造相机的 Agent 名称来凑八步；进度只能描述 DAQ 实际运行的阶段。

**验收**：`tests/test_trace_persistence.py`、认证 SSE/历史合同、`webui/src/thinkingSteps.test.ts`、`webui/src/sessionNav.test.ts` RED→GREEN；同一 turn 的流、数据库历史、trace 根记录和页面状态一致，所有已结束 turn 无悬空 running。

### R6：冻结泛化集，跑真实 Dev 并独立复审

**依赖**：R1–R5。**改动**：新增 `evals/daq_workflow_20261012.jsonl`、Dev runner、评测合同和复审报告。

- [x] 在模型回放前冻结第 3 节问题/预期、旧 24 题及 suite SHA。新 runner 经 R4 的实际 DAQ 编排和**认证 Dev 路径**执行；旧 `data/knowledge/curated/operator-dev-20261012/run_preview.py` 可作领域诊断，但它直接调用 Loop，不能单独证明入口修复。
- [x] 用真实活动 `ReviewedKnowledge` 视图、角色、DAQ prompt 和模型配置在 Dev 回放。每题保留完整问题/答案、schema 与目标、需求/覆盖、工具状态、结构化来源、outcome、fallback、trace、时长、release/model/prompt 身份；结果写入私有、Git 忽略目录，报告只存脱敏摘要和哈希。
- [x] 对失败的原题、至少两种语义改写、英文及混合问题逐题由**非回答模型**的 Codex 或具名 FAE 审查专业性、完整性、依据和可交付性。答案模型不得自评；自动结构断言只作前置检查。保留第一次失败和后续修复批次，禁止只挑成功输出。
- [x] 原冻结 24 题全量重跑；任何失败按 channel/context/guardrail/schema/retrieval/coverage/synthesis/outcome/trace 层归因，修该层后同时重跑受影响的泛化簇和全部高风险哨兵。
- [x] 记录串行 P50/P95/最大值、schema/Loop/工具分层耗时、Provider 轮数及 2 并发 Dev 行为。沿用试点观察阈值：串行 P95 不超过 60 秒、单题不超过 90 秒；超过则定位并重新做质量/性能权衡，不以快而错误的旧路径作为目标。

**验收**：原 24 题和新冻结题集均无严重事实/型号混答、权限泄漏、无据 `resolved`、系统错误伪装缺证、错误 trace；所有题有独立逐题 PASS 或明确未通过记录。某个短语答对、只跑直接 Loop、只看自动评分，均不能签发“已修复”。

### R7：共享回归、版本复核与发布判定

**依赖**：R6。**交付物**：精确版本、测试命令和结果、独立复审、未过项、回滚点的证据包。

- [x] 数采完整单测：`DAQ_KNOWLEDGE_RELEASE_ROOT=/tmp/daq-fae-empty-release-test-20261012 .venv/bin/python -m pytest -q tests`（先确认测试根目录没有 `active.json`）；WebUI：在 `webui/` 运行 `npm test` 与 `npm run build`。另以显式活动 manifest 跑已发布知识视图合同。关键路径单测必须有新旧对照，不得只增加与实现同构的测试。
- [x] 若共享 `src/` 有改动，在相机仓跑受影响合同与完整单测，并在相机 Dev 运行相关核心/哨兵/历史反馈泛化题，独立审查零严重退化；未通过不得更新 DAQ 上游 pin。
- [ ] 检查源代码 SHA、知识 release、提示词/模型配置、WebUI build、镜像 digest 与可回退版本。提交不等于部署；若另行部署，生产只做不触发模型的健康、身份、路由、权限隔离、版本、日志和相机健康检查。
- [x] 发布结论只可为“Dev 验收通过待发布”“已部署待业务观察”或“未通过”；不能把局部修复、Git 推送和生产验收写成同一状态。

共享 `src/` 未改，两仓 tree 同为 `e69021117158cbdc7e6d75220c5247fe631bbe58`，因此第二项的相机模型回归条件未触发。第三项已有源码、知识、提示词、模型和 WebUI build 身份；新镜像 digest、生产绑定与只读部署烟测尚无证据，维持未勾选。当前结论限定为 **Dev 验收通过待发布**。

**验收**：R0 原始失败可重放为 GREEN，R6 独立复审和 R7 本地门通过；待第三项完成后，才能将 R7 发布门及线上状态标记完成。证据包须能按 exact SHA/release 复跑。

## 3. 新冻结题集的最低内容与判据

以下是 R0 必须写入正式 suite 的最低案例。实际 suite 可以增加同义表达和多轮变体，但不得删减这些目标类别。对产品值的预期以本轮活动 release 的已发布 claim 和适用条件核对；若 release 变化，另建 suite 版本。

| ID | 输入/场景 | 必须验证 |
| --- | --- | --- |
| S01 | `介绍一下你自己` | 服务目标 `resolved`，简介准确简洁；无产品检索要求、无伪造产品来源、无“当前无法下结论” |
| S02 | `你能做哪些数采工作？目前资料覆盖到哪里？` | 服务能力与当前 release 覆盖分别说明；不能把工具存在说成所有产品事实已发布 |
| S03 | `Who are you and what can you help me with?` | 英文服务目标正确；同一证据合同，不靠中文固定句 |
| S04 | `你是相机 FAE 还是数采 FAE？` | 身份边界明确；不能冒用相机知识与会话 |
| M01 | `介绍一下你自己，再说说 EGO 双目 1600×1200 版本的水平视场角。` | 两个子目标；服务说明可无产品来源，规格 165°须有正确发布 claim/变体来源，整体 `resolved` |
| M02 | `你是谁？EG-DB 整机深度精度是多少？` | 服务部分可答，整机精度缺证不得伪造；整体 `safe_abstained` |
| P01 | `EGO 双目 1600×1200 版本的水平视场角是多少？` | `resolved`、165°、变体与来源正确；保留旧 24 题正向哨兵 |
| P02 | `EGO 双目的水平视场角是多少？` | 未指定变体不能给无条件单值；逐变体条件说明并请求确认，整体 `safe_abstained` |
| P03 | `EG-DB 整机深度精度是多少？` | 模块事实不能代替整机事实；安全缺证 |
| K01 | 对当前格式 2、零章节 release 调 `search_knowledge("EGO")` | 能发现角色可见记录且保留 release/来源；相关性不能满足未匹配的 typed 规格 |
| C01 | 先说 `我用 EGO 双目 1600×1200 版本`，追问 `它的水平视场角呢？` | 有效问句、实体/变体、需求与答案同一作用域；历史恢复后仍一致 |
| C02 | 上一轮 EGO，随后 `换个问题，数采 HUB 有几个下游端口？` | 切题清除 EGO 约束；HUB 四端口的来源正确 |
| A01 | 只询问本轮上传图片里可见内容 | 图片观察与产品事实分离；视觉工具失败不可声称看见，也不因无产品型号写冗长缺证段 |
| T01 | 服务、产品、拒答、工具错四类 turn 的 SSE→历史→UI | 真实阶段闭合；完成后无 running；outcome、sources、fallback 与 UI 一致 |
| F01 | 模拟 Provider HTTP 400、429、真实 5xx、工具协议错误 | 配置、限流、上游不可用和协议错误各自归因；不变成业务缺证或错误 503 |
| G01 | 越权角色、撤权中途、重复 `client_request_id` | 检索前过滤、终稿前复查、重放幂等；无相机/DAQ 跨域数据 |

关键用户可见样本 S01、S02、M01、M02、C01 在 Dev 真实模型下至少使用原句和两种提前冻结的改写；所有输出逐次保存并独立审阅。若某次失败，不能用同一题的成功重跑覆盖原始失败；需修复根因后重跑整个相关簇。最终报告明确说明哪些结论来自确定性测试、哪些来自 Dev 模型、哪些只是生产只读检查。

## 4. 证据包与追踪

每个任务提交附：基线 SHA、改动 SHA、上游 `src/` SHA、活动知识/提示词/模型身份、RED/GREEN 命令与输出、受影响的旧测试、未通过项、独立复审意见和回滚点。R6 报告按 case ID 保留完整答案与结构化来源的私有记录哈希，汇总表列出 `goal_status`、`requirement_status`、`planned/actual_capabilities`、`outcome`、`fallback_used/reason`、阶段闭合与耗时。证据不足的任务不得勾选完成。

**编写任务书时的命令核查**：直接运行 R0 的 pytest 文件集合时，测试模块导入默认应用会读到活动 release，与默认开发 runtime 不配对而在收集阶段报 `runtime_identity`；只指定活动 runtime 后又被“非空知识必须认证模式”拦住。将 `DAQ_KNOWLEDGE_RELEASE_ROOT` 指向无活动指针的测试根目录后，同一文件集合实际得到 **98 passed**。这是测试环境隔离证据，不是本任务书的缺陷修复或新回归通过记录。

本任务书结束后，新知识大批量发布仍须独立通过既有 K-1～K-4 与增量发布门；本任务书只修复工作流，并用当前窄 release 验证其对已发布和未发布事实都作出正确处理。

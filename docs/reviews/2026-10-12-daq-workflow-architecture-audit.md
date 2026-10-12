# 数采 FAE 与相机 FAE：设计和代码工作流对照

日期：2026-10-12。触发事件：用户在线上数采服务询问“介绍一下你自己”，得到“当前无法下结论”、两次 `not_found` 和悬空的 `loop=running`；用户提供了相机服务的八阶段进度作为对照。本报告比较两仓设计、当前代码、活动知识快照和既有 Dev/发布记录。没有向生产模型发起测试，也没有该线上 turn 的后端原始 trace；其具体工具参数与 Provider 轮次不作推断。

## 1. 版本、证据与总判断

| 对象 | 核对结果 |
| --- | --- |
| DAQ 当前源码 | `4dcc0a0799055a2c1439052bfe6cf3557a9eb20f` |
| 相机当前源码 | `4f453b77073005d24d9f07f36016b7172009443a`；其工作区原有其他未提交改动，本次未修改 |
| 共享 `src/` | 两仓 HEAD 的 Git tree 都是 `e69021117158cbdc7e6d75220c5247fe631bbe58` |
| DAQ 本机活动知识 | `a63baf103892250b8474ad9e464b8df47c889e020313e37dd71ad815af9d55a6`；格式 2，23 条记录（6 实体、17 规格），**0 章节** |
| 生产状态 | 既有发布记录称受控试点和图片能力已部署；本次未重新核对线上容器 build identity |

**总判断：**独立 DAQ 服务、独立知识发布和复用通用 `LoopRuntime` 是正确的设计边界。当前问题发生在 DAQ 专属的**请求控制平面**：没有完整的领域 schema 来表达本轮目标、证据种类及上下文依赖；需求账本由静态工具能力直接生成；终态门只接受产品事实或用户附件；当前知识快照又与泛检索分支不一致。相机的阶段名只是可见症状。共享 Runtime 的 Git 源码相同，回答和 trace 的差异主要来自 Runtime 前后的领域编排。

## 2. 对齐目标是什么

DAQ 设计 `docs/2026-10-08-数采FAE完整设计.md:90-110` 规定：

```text
channel → 身份/角色 → 会话上下文 → 边界 → 数采 schema/实体接地
        → 证据需求账本 → Loop 工具取证 → 覆盖/冲突门
        → submit_answer → 确定性渲染 → sources/outcome/trace/eval
```

任务书 `A-1/A-2`（`docs/superpowers/plans/2026-10-08-data-acquisition-fae-development-task-brief.md:138-152`）要求独立 DAQ 装配，不得调用相机专用整个 `Orchestrator`，但必须有自己的前置 schema、实体接地和稳定需求账本。相机当前默认 `COMPOSITION_MODE=loop`；其工作流设计 `AI_FAE_Agent_工作流设计.md:753-755` 明言 Loop 路径**不运行 legacy Capability Planner**。所以目标是达到同等的层级关系和协议质量，不是复制相机类名或插回旧 Planner。

```text
相机当前 Loop 路径
API/Session → Orchestrator
  → session_context → guardrail → 模型 RequestSchema
  → 产品锚定/主题切换/短追问有效问句 → LoopRuntime
  → 证据/终稿门 → 完成阶段、sources、outcome、trace

数采认证聊天路径（线上相关）
身份/角色 → 会话/请求预留 → emit(loop=running)
  → 红线检查 → prepare_turn(DaqTaskContext + requirements)
  → DaqToolBox → 同源 LoopRuntime + DaqEvidencePolicy
  → 原子提交答案/来源/DAQ checkpoint → done
```

相机实际路径见 `../AI-FAE-Agent/src/agent/orchestrator.py:441-661,1056-1079,1208-1232,1324-1328`；DAQ 实际路径见 `daq_fae/authenticated_chat.py:147-205,259-309`。相机截图证明该次运行发出了会话、边界、意图和 Loop 生命周期事件，不证明其每个专业答案都正确。

## 3. 设计不变量逐层核对

| 层与不变量 | 相机现行实现 | DAQ 现行实现 | 判定 |
| --- | --- | --- | --- |
| **渠道、身份、权限**：先确认主体和资料角色 | 相机 API/Session 后进入 Orchestrator | DAQ 认证入口检查 `client_request_id`、会话、entitlement、知识角色，调用前后复查授权；`reserve/replay/complete_turn` 保护幂等和事务（`authenticated_chat.py:42-125,281-309`） | **DAQ 已具备**，是独立服务所需差异；生产按用户决定使用同库独立 schema，不能拿早期“独立 DB”文字判这次回答故障 |
| **权威会话记忆**：本轮继承什么条件有唯一来源 | `SessionContext` 有 schema、场景、约束、候选/排除型号、待确认项和最近摘要；切题替换活动视图（`session.py:209-298`） | `DaqTaskContext` 存用户断言、已试步骤和上轮能力；`DaqContextState` 虽声明任务、组合、错误、待确认、结论等字段，认证入口实际只写 `task_context`、角色和知识版本（`durable_state.py:62-87`、`authenticated_chat.py:281-285`） | **设计字段多，实际记忆浅**；声明 schema 不等于写入、读取和回放 |
| **短追问/切题**：得到有效问题与作用域 | 相机合并 schema、修复产品锚点，并产生 `effective_user_message`；trace 记原问、有效问、使用字段（`orchestrator.py:559-661,1724-1756`） | DAQ 以短句条件继承 `active_capabilities`，仍把原问送 Loop；`context_note` 为用户断言 JSON，没有有效问句或指代决议（`task_context.py:205-240,325-340`、`authenticated_chat.py:190-193`） | **部分实现**：模型可能凭历史猜对，但实体范围与账本未必同步 |
| **边界**：红线与请求类型分离 | 相机显式 guardrail 阶段后才 schema（`orchestrator.py:473-521`） | DAQ `categorize_refusal` 处理红线，但无服务元信息/产品咨询的结构化区分；Task 入口甚至在红线前先规划（`platform_tasks.py:76-89`） | **部分实现**；不能拿 guardrail 代替语义服务边界 |
| **领域请求 schema**：目标、子问题、实体、条件、附件依赖和置信度类型化 | 相机 `extract_schema` 用模型产 `RequestSchema`，再验证与锚定（`schema_extractor.py:142-205`、`orchestrator.py:521-661`） | `TurnPlan` 只有 context、requirements、planned capabilities、状态和 note；缺请求类别、子目标、语义置信度、附件依赖和有效问句（`task_context.py:106-115,190-340`） | **主缺口**：设计中的 DAQ schema 未形成；正则只是实现症状，根因是目标模型缺位 |
| **实体接地**：用户描述与权威身份分开 | 相机 catalog/resolver 修复产品锚点 | `ReviewedKnowledge.ground_question` 只给角色可见、精确且唯一的实体/变体；用户条件保留 `user_supplied`（`reviewed_view.py:69-97`） | **安全边界正确但范围窄**；模糊设备与组合需要可审计歧义，不应变成泛搜 |
| **需求账本**：每个用户目标有证据类型与稳定 ID | 相机有 schema 派生的状态及比较、链接、附件等 typed 要求；默认 Loop 也不是一张万能主张账本 | DAQ 每个计划工具能力都生成 `critical=true/governed_required`；附件另外是 `user_evidence_required`（`task_context.py:295-324`、`domain_evidence.py:43-67`） | **主缺口**：把“可能调用什么工具”当成“何种证据证明用户目标”；不能表达服务合同或混合请求的不同子目标 |
| **未知需求**：未知应可见，不能假装已覆盖 | 相机低信心 schema 仍有单独处理，不把空覆盖当完整 | DAQ 未归类时形成 `search_knowledge` 要求和 `intent_or_entity_not_grounded`；记录匹配器明确拒绝这个理由（`task_context.py:235-237,300-309`、`domain_tools.py:516-520`） | **无法闭合**：该 requirement 不能被产品记录满足；重试检索不会改变其身份 |
| **知识取证**：工具与发布格式契约一致 | 相机产品事实、SDK、链接各有受治理来源；QA 默认不参与在线回答（相机工作流设计 `:169-173,473-482,531-589`） | DAQ 有角色过滤工具；当前活动 release 只有 23 条实体/规格、无章节；`search_knowledge` 只要 format=2 就走章节（`domain_tools.py:231-233,354-408`） | **明确代码/数据契约错位**：泛检索在当前快照下必然 `not_found`，尽管 `catalog` 可检出 EGO |
| **章节与硬事实**：相关性不等于事实证明 | 相机按来源类型和条件取证 | DAQ 章节检索故意给 `matched_requirement_ids=[]`；记录工具匹配需求 ID 与条件（`domain_tools.py:323-352,396-408`） | **这项安全设计正确**；修复泛检索时不能让章节文本直接满足 typed 规格 |
| **Loop/终稿门**：模型可补查，程序守住结论 | 相机默认使用 Loop，旧 Planner 不运行 | DAQ 使用相同 `LoopRuntime` 源码与 `submit_answer`，注入域 `EvidencePolicy`；未覆盖 `resolved` 被阻止，Provider 错误分型（`domain_evidence.py:155-220`、`authenticated_chat.py:209-253`） | **复用方向正确**；Policy 对产品安全有效，但证据域只有产品与附件，正确服务答复也没有合法完成路径 |
| **状态更新**：工具结果区分未尝试/缺证/冲突 | 相机多个 typed 门按具体证据更新 | DAQ `observe` 依预建 requirement ID 更新；普通 `not_found` 没有匹配 ID 时仍为 `unknown`，只有整库为空可统一记 `missing`（`domain_evidence.py:83-153`） | **状态语义有洞**：现有资料未命中与尚未取证可能同为 `unknown`；截图与此吻合，但未读取该线上 turn 后端 ID |
| **合成**：按本轮目标答，缺口最小化 | 相机有效问句/schema/状态进入 Loop | DAQ 发布提示词要求只按工具事实回答；仅空知识 Dev release 有短弃答整理，23 记录活动 release 不适用（`prompts/reviewed_knowledge_system.md:1-11`、`empty_knowledge_synthesis.py:36-49`） | **错误的控制信息塑造错误答复**：模型沿产品缺证轨道写过程说明，单改措辞不会修好 |
| **生命周期/trace**：真实阶段有开始与终点 | 相机发会话、边界、schema、Loop 的开始/完成/错误，schema trace 记上下文决议（`orchestrator.py:288-309,441-661,1075-1079,1324-1328`） | DAQ 认证/本地入口在红线检查前就发 `loop=running`，正常结束无 `loop=completed`；认证持久 `stages=calls` 只保存工具；Task 入口把已返回的工具调用也编码为 `running`（`authenticated_chat.py:150,202-205,265-270`、`platform_tasks.py:103-108`） | **可观测性缺陷**：无模型的红线拒答也会被标成 Loop 已开始；UI/历史无法解释规划、证据或耗时 |
| **前端终态**：弃答、无来源、运行降级分开 | 两仓 `thinkingSteps.ts`/`sessionNav.ts` 字节相同 | stage 一律显示为 `done`，但 detail 保留 `running`；无 fallback 显示 `No fallback reported`；零来源统一 `answered · no sources`（`webui/src/thinkingSteps.ts:77-85,121-135`、`sessionNav.ts:38-51`） | **共享展示缺陷在 DAQ 更明显**；“无运行降级”不代表“回答正确” |
| **评测/发布**：覆盖基础咨询和复杂场景，独立语义复审 | 相机有 core/sentinel/priority/反馈的分层质量门和数据飞轮 | DAQ 24 道 Dev 真模型题针对 17 规格、变体、缺证、未发布软件/链接/流程及追问；15 `resolved`/9 `safe_abstained`，未覆盖服务元信息与普通对话（`docs/reviews/2026-10-12-daq-operator-pilot-dev-review.md:7-18`） | **证明范围窄**：24/24 只证明既定首发样本，不证明完整 FAE 体验 |

## 4. 三个核心接口错位

1. **请求目标没有成为一等数据。** `TurnPlan` 的中心是工具能力列表，而不是用户本轮的目标及其子目标。共享 `LoopRuntime` 可以自主选择工具，但不能回写已经生成的需求清单。后果是“要用哪个工具”与“怎样证明答复成立”被混成一件事。这是设计模型问题，具体词句命中只是可见表现。
2. **知识快照与泛检索契约不一致。** 活动 release 是格式 2、0 章节；`DaqToolBox` 对格式 2 无条件采用章节检索。无模型的本机执行证明：同一 release 的 `search_knowledge({"query":"EGO"}) → not_found`，而 `catalog({"query":"EGO"}) → ok，3 个匹配`。这是路由 bug 和发布验收缺口。即便修好，服务介绍仍不应依赖产品目录。
3. **决策没有进入可追溯状态。** DAQ checkpoint 存用户断言和上轮能力，却没有本轮目标、有效问句、需求生成依据、状态转移、待确认项和已验证结论。认证 turn 仅把工具调用存为 stages；认证路径创建根 trace 后没有像本地 Dev 路径那样安装 `install_trace_ctx`，无法依靠该上下文记录同一请求的领域子阶段。事后看到 `not_found`，很难解释系统为何搜索、是否搜对以及为什么弃答。

DAQ 已有一些需要保留的优点：检索前角色过滤、来源路径与正文分离、附件只作本轮证据、独立知识版本、请求幂等和原子提交、无据 `resolved` 受阻、Provider 400/429/5xx 区分。它们不应因对齐相机界面而削弱。生产按用户后续决定共用 PostgreSQL 数据库但采用 DAQ 独立 schema、密钥和授权；这与早期设计文字不同，不是本次问答失常的证据。

## 5. 线上失败可以由代码推出的因果链

1. 当前 `prepare_turn` 没有服务元信息请求类别。本机无模型复现中，该问句得到一个 `search_knowledge/governed_required/unknown` 关键需求，带 `intent_or_entity_not_grounded`。这里的重点是**没有合法请求类型**，不是某个问法未被关键词覆盖。
2. 当前知识快照的 `search_knowledge` 走空章节，因此此工具必然 `not_found`。即使模型另试 `catalog`，实体清单也不能证明“这个服务是什么、能做什么”。
3. `DaqEvidencePolicy` 只允许治理产品记录和用户附件满足需求；未知理由的需求不会与记录匹配，普通 `not_found` 还可能停留 `unknown`。`resolved` 被挡下，`safe_abstained` 可以交付，形成“当前无法下结论”的答案形态。
4. 发布提示词把答案整体绑定在本轮工具事实上；空知识短弃答整理又不适用当前 release。因此模型容易陈述工具过程、重复缺证说明，而不是给出简短真实的服务说明。这是错误控制信息驱动的合成结果，不能只归因于模型风格。
5. 服务端发了 `loop=running` 却不发正常完成，前端又把 `safe_abstained`/零来源显示成普通 answered，并用 `No fallback reported` 表示没有运行降级。最后一项不等于答案正确。

这条链有两个独立故障：补齐资料不能自动修复服务类请求的证据类型；补齐请求 schema 也不能自动修复空章节时的泛检索路由。

## 6. 知识体系与评测的真实差距

相机在线知识并非一个 Markdown 目录：产品型号和字段覆盖在 `Knowledge/_facts`，型号资料在 `Knowledge/<model>`，SDK/API/源码证据和设备支持矩阵在 `Knowledge_SDK`，可交付 URL 在 `Knowledge_Links`；QA 候选虽多，默认关闭在线路径。工具按来源类型取证，线上反馈再独立复审。见相机工作流设计 `:169-174,473-482,531-589,2251-2355`。

DAQ 的离线候选、章节草稿、冲突/角色/发布清单和增量机制已经存在；**在线活动快照仍只有 6 实体、17 规格、0 章节，软件、拓扑、流程、链接、经验等没有已发布可答证据**。工具清单广不等于已发布知识广。知识覆盖与请求控制是两条不同验收轴。本次错误首先暴露控制平面问题，但产品类问题仍受知识发布范围限制。

数采资料更新快，更需要 `runtime_release` 与 `knowledge_release` 分开：新资料按来源哈希、实体/变体/条件、冲突、角色和 Dev 回放生成不可变快照，再切换指针；不能线上临时读 `tmp/`、飞书或附件，也不能让 DAQ 知识切换影响相机服务。当前设计具备这些边界，后续要让在线检索与质量门真正消费发布版本。

## 7. 修复接口与验收定义

| 优先级 | 设计接口 | 完成定义 |
| --- | --- | --- |
| P0 | **DAQ 请求 schema**：本轮类别、子目标、实体/变体/条件、上下文和附件依赖、置信度、原问/有效问。可由模型给候选，再由程序校验和保守回退 | 服务、产品、混合、追问、切题、未知/越界请求都有可审计结构；低信心不静默伪造产品需求；不恢复旧单桶 Planner |
| P0 | **按子目标建账**：服务身份/能力由版本化服务合同约束，产品/兼容/流程由发布知识约束，用户附件只作本轮观察 | 无产品声明的服务介绍能正确完成且不伪造 sources；产品/混合问题不可无据 `resolved`；缺证、冲突、需裁决、需人工和运行失败分开 |
| P0 | **知识格式/工具契约**：发布器或装载器验证格式、记录、章节与检索策略；明确记录型和章节型搜索的行为 | 活动快照中已知实体可被预期入口检索；空章节不会把已有记录显示为知识全空；章节相关性不直接满足 typed 规格 |
| P1 | **统一领域编排与记忆**：认证、本地 Dev、Task 复用 schema/账本/终态决策，只保留各入口的身份、附件、持久化差异 | 三入口相同输入产生等价需求和 outcome；短追问继承已确认范围，切题不污染；Task 摘录仍是未核实提示 |
| P1 | **真实阶段/trace/UI**：实际执行阶段均有开始/完成或错误和耗时；保存完整阶段，前端按 status/outcome 显示 | 无悬空 running；历史 turn 可追溯为何取证/弃答；`safe_abstained` 与普通 answered、fallback 分开 |
| P1 | **Dev 质量和性能**：扩展冻结集并独立复审答案、来源、终态、trace | 服务元信息/能力边界、中英文、混合、多轮、未知型号、组合/软件缺证、附件成功/失败、Provider 400/429/5xx 均有逐题记录；原 24 题保留为规格哨兵 |

不建议复制相机整个 `Orchestrator`：它携带相机目录和工具依赖，违反 A-1。不建议给单句自我介绍做固定回复或 prompt 特例：账本、追问、混合问题仍错。不建议让章节命中直接满足所有产品事实，也不建议把所有 `unknown` 改成 `missing`，这会隐藏证据不足。

## 8. 本次验证和未核实项

- 当前本机代码无模型复现：`prepare_turn` 生成服务介绍的 `search_knowledge/governed_required/unknown`；当前 release 上 `search_knowledge("EGO")` 为 `not_found`，`catalog("EGO")` 为 `ok`、3 项。
- 两仓 `src/` HEAD Git tree 相等；两仓 `webui/src/thinkingSteps.ts` 与 `sessionNav.ts` 逐字节相同。共享前端缺陷不应误判为 DAQ 独有；DAQ 缺失阶段装配是 DAQ 独有。
- 既有 Dev 真模型 24 题经 Codex 独立语义复审，15 题解决、9 题安全弃答；串行中位 28.46 秒、P95 46.77 秒。它没有覆盖本次失败类别，不能据此认定架构或生产质量完成。
- 本次没有生产模型回放、没有用户示例 turn 的后端 trace ID、没有实际测试相机对同一句话的答案，也没有重新核对当前线上相机/DAQ 容器与本地 HEAD 完全相同。既有部署记录证明曾发布，不代表新代码已经上线；后续更改应先在 Dev 回放并独立复审。

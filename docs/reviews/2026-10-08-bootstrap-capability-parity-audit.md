# 空知识启动版与原相机 FAE 的能力对照

日期：2026-10-08。审查对象：数采仓 `5f07a4f` 的 `daq_fae.app`，对照相机 FAE 已部署源码 `a6234f6` 的 `src.api.server.create_app`、`Orchestrator` 和注册路由。本记录区分**源码存在**与**服务实际启用**。

| 能力 | 数采启动版实际状态 | 完整数采设计要求 |
| --- | --- | --- |
| Provider Adapter、Opus 5.5 配置、Loop 与 `submit_answer` | 已调用复制的 `LoopRuntime` 与 `AnthropicAdapter`；一轮 Dev 网关烟测通过 | 保留固定版本依赖与终稿合同，完成双仓回归 |
| 独立服务与知识隔离 | 独立 `/health`、`/chat`；知识目录为空，未加载相机目录 | 独立服务版本、知识发布、数据与回滚 |
| 前置上下文、guardrail、实体/schema 接地 | 未接入；新入口直接把单条问题送入 Loop | 按数采任务、设备组合和版本建立上下文及前置需求 |
| 多能力证据工具与覆盖账本 | 仅暴露空的 `search_knowledge`；`planned_capabilities` 和 `capability_coverage` 是启动版固定字段 | 数采实体、规格、拓扑、流程、软件、SDK、链接等取证；覆盖由需求账本计算 |
| Loop 证据门 | 终稿后用外层空知识保护拦截无据结论；上游尚无 `EvidencePolicy` 注入点 | 在共享 Loop 内按账本补证或拒绝，无据 `resolved` 不通过 |
| 会话与多轮、历史记录 | 未接入 `SessionStore` 或持久会话 | 条件继承、主题切换、会话与反馈按新 Agent 隔离 |
| 附件、视觉、归档 | 未注册附件路由或附件工具 | 在授权和新 Agent 数据域下支持日志、图片与附件取证 |
| 身份、权限、Platform Task | 未注册；只宜绑定本地 Dev 地址 | 新 `agent_id`、跨 Agent 拒绝、资料可见性与任务 audience 合同 |
| 反馈、review、trace | 仅响应内临时 `trace_id`；未装配持久 trace/反馈/review | 独立持久化、结构化来源与完整失败归因 |
| SSE、并发与重连 | `/chat` 是 SSE 格式，但先 `list(runtime.run(...))` 收集全轮再输出；无心跳、去重或并发门 | 流式进度、心跳、`client_request_id` 去重、受控并发与完整终态 |

**判定**：复制的 `src/` 与 `requirements.txt` 对应上游固定提交，但数采 `create_app` 只装配了其中一小部分。因此，当前**不能称为“除知识库外与相机 FAE 能力等价”**。它只完成空知识本地 Dev 烟测。相机 FAE 的运行代码和部署没有被这个分支修改。

恢复等价时不能直接运行复制的相机 `src.api.server.create_app()`：它启动即加载相机目录、事实矩阵与链接库，并把旧 Agent 身份写进部分组件。按[开发任务书](../superpowers/plans/2026-10-08-data-acquisition-fae-development-task-brief.md)先完成 M0 的共享证据门、身份参数化和持久上游依赖，再由数采应用装配复用会话、附件、trace、并发和路由协议；相机专有的目录/筛选工具须由数采实体、拓扑和版本证据工具替代。每项能力以合同测试和真实 Dev 回放验收，而非以文件存在或 `/health` 正常验收。

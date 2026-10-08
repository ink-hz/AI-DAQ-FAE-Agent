# 数采 FAE 非知识能力完整迁移计划

**目标**：数采服务保留现有相机 FAE 的通用服务能力与客户端合同，只替换领域接地、取证工具和知识发布；仍保持独立 Agent、数据域、发布与回滚。

**基线**：相机 FAE 已部署源码 `a6234f6be546efebb230ffc74bd9a00bebdc2814`。数采启动提交 `5f07a4f` 仅为本地烟测，逐项缺口见 [能力审查](../../reviews/2026-10-08-bootstrap-capability-parity-audit.md)。完整设计与[总任务书](2026-10-08-data-acquisition-fae-development-task-brief.md)继续约束本计划。

**完成判定**：不得仅以复制 `src/`、健康检查、非空回答或单轮烟测称为迁移完成。下列每项必须在数采应用**实际装配**，并以双服务合同、数采 Dev 回放和独立质量复审证明。正式身份和知识试点仍受上游持久 ref、资料裁决与权限门约束。

## 批次 1：共享运行时与身份

1. 在相机 FAE 隔离分支给 `src/agent/loop/runtime.py` 增加可选 `EvidencePolicy`，每轮独立账本会话；观察未截断工具结果，在现有终稿门之后、有据终稿交付之前判断；无注入时旧行为一致。先写失败合同测试，再实现，跑旧 FAE Loop、终稿和 Dev 回归。维护工作流地图。
2. 参数化 Platform identity client/service、task capabilities 和 trusted audience，默认仍为 `ai-fae-agent`；在缓存命中 `_resolve` 路径也拒绝其他 Agent 的记录。先写双 ID、缓存记录和签名任务 token 失败测试，再实现，跑旧身份/任务合同。附件归档和报告发布在数采启用前另行参数化；当前不得默默复用旧 ID。
3. 集成两个上游分支，运行共享测试与相机 FAE Dev 回归；将最终提交放在受保护持久 Git ref 并锁定数采依赖。无法核实 ref 保护时可继续本地开发，但不得声明批次 1 或正式依赖门完成。

## 批次 2：数采服务合同与通用组件

1. `/chat` 使用原 FAE `ChatRequest` 字段与具名 SSE：`session`、`stage`、`heartbeat`、`text_delta` (`delta`)、`sources`、`done`。接回会话/历史、并发排队、持续 heartbeat、trace 录制与终态错误归因。复用现有通用组件的合同，不直接运行相机 `create_app` 或 `Orchestrator`。验证旧客户端可解析实际数采响应。
2. 在独立数采存储与权限域接回 `/attachments`、`/feedback`、`/history`、authenticated conversations 和 review。附件解析/视觉证据只进入当前会话，不进入官方知识；跨 Agent 引用拒绝。缺数据库、身份或权限时显式失败，不回退到匿名公开会话。
3. 补 `client_request_id` 执行去重与断线终态：原 FAE 仅回显该字段，去重是数采设计的新要求。重复同 payload 不再调用模型，复用 ID 的不同 payload 冲突；中断不能当作完整答案。保留独立数采构建、附件目录、trace/反馈存储和回滚目标。
4. WebUI 只在后端合同和数采身份完成后适配；不能把相机 `/fae` 入口或旧 Agent 身份原样发布给数采用户。HTTP Task/MetaBot 入口按设计与可信 audience 联调。

## 批次 3：数采领域替换与空知识约束

1. 建立数采任务上下文、实体/变体/组合/schema、主题切换和可恢复 checkpoint。前置需求账本为每项需求记录稳定 ID、能力、实体、条件和 `satisfied / missing / conflict / unknown`；用户猜测不晋升为事实。
2. 实现数采 `ToolBox`：目录、选型、经验、风险，以及 `resolve_entity`、`lookup_spec`、`inspect_topology`、`lookup_procedure`、`check_software_support`、`search_knowledge`、`sdk_evidence`、`official_links`、`session_state` 和经授权的附件工具。知识为空时仍提供完整工具合同，但领域事实全部返回准确的缺证/未知状态；不得借用相机 facts/catalog/SDK roster。
3. 将账本接入共享 `EvidencePolicy`。空账本、未闭合关键需求或冲突不得 `resolved`；系统失败不得改写成业务缺证；URL 只能来自本轮有权工具结果。终稿的 planned/actual/coverage 由账本和工具执行产生，不写固定展示值。

## 批次 4：验收与发布门

1. 跨仓合同：会话续接/纠正/换题、SSE/heartbeat/去重、附件与视觉失败、反馈归属、trace/持久记录一致、Provider 400/503 与协议错误、双 Agent 身份/任务拒绝、无据终稿和 URL 门。
2. 数采真实 Dev 回放：核心单轮、多轮、历史失败哨兵、catalog/spec/selection/SDK/troubleshooting/boundary 泛化题；由 Codex 或具名 FAE 独立复审。相机共享代码变更同时跑旧域回归，零严重退化。生产只做只读健康与入口检查。
3. 固定知识、模型/提示、上游和应用版本；权限、来源归档、发布清单、独立回滚与资源上限达标后，才进入内部试点发布决策。

## 当前执行记录

- `2026-10-08`：完成只读能力对照；数采 `feat/full-daq-fae-parity` 与相机 FAE 隔离集成分支已建立。共享证据门、身份参数化、数采多能力证据、空知识 API、本机与认证会话、附件证据、WebUI、归档、HTTP Task 装配及 DAQ 专属迁移脚本已有实现。初轮数采仓 132 个 Python 测试、235 个 WebUI 测试与构建通过；真实隔离 Postgres 已验证附件上传→回答→归档读取/确认→删除确认。相机 FAE 真实 Dev 烟测有 1 个硬失败和 1 个 Codex 发现的答案矛盾；数采真实 Dev 有 476.6 秒 Provider 传输失败。两项仍阻止“完整迁移已验收”的结论。当时上游 Git SHA 仍是未保护的本地 ref；数采仓暂无持久远端，真实 Platform 与发布回滚尚未联调。
- `2026-10-08` 复审修复：Platform 后续消息的附件引用改为明确拒绝；归档队列双向按 Agent 隔离，本地附件过期或清理后仍允许归属人请求 Platform 删除；关闭新归档时仍处理历史删除；模型执行前保护归档内容；长流续租，认证回答/会话/请求终态统一事务提交；Platform 上下文只作未核实的需求范围，不进入系统角色提示；数据库角色/库名比对不依赖主机字符串，迁移前置检查先于所有 DDL，任务队列要求 DAQ 安装标记；空知识计划和工具面补齐目录、选型、经验、风险。
- `2026-10-08` 终检：上游共享集成提交 `bf1ce9af2d467447e39e2be0caae0bb87b1ba689` 已推送相机仓远端特性分支，数采源码与该 SHA 的 `src/` 一致；相机隔离工作树 3085 项单测、数采后端 159 项单测、WebUI 235 项测试与构建均通过。独立数采离线 Dev 进程的 `/health` 和 `/chat` 实际 HTTP 烟测通过。补齐空知识多能力和 Platform Task 答复一致性、DELETE→晚到归档确认、旧 FAE 关闭新归档后的历史删除、反馈默认索引；新增 DAQ 专属身份会话约束迁移，真实隔离 Postgres 登录通过，缺迁移时启动即失败。上游 ref 保护、数采仓远端、真实 Platform 联调、完整双域 Dev 回归、知识治理和发布回滚仍待完成，不能宣布完整能力等价或试点就绪。
- `2026-10-08` 续验：当前固定源码下数采真实 Dev 完成空知识目录、规格、选型、SDK、排障、商务边界及两轮上下文回放；发现并按 `planner / context / synthesis` 层修复“怎么选”、诊断症状、并列设备解析及空知识排障下一步，保留失败原样和真实复测。后端 168 项、WebUI 235 项及构建通过。新增两仓 `src` tree/依赖 blob 精确核验与独立 CI 定义，本机核验通过；数采仓无远端，CI 未实际运行。当前相机集成分支的目录哨兵重放答案自洽，选型同题 360 秒无终态；见 [本轮复审](../../reviews/2026-10-08-full-runtime-dev-smoke.md)。旧域性能/终态门、真实知识和 Platform Dev 联调仍未通过。

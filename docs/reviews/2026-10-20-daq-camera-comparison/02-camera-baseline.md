# A2：相机 FAE 的可验证工程基线

**基线身份**：相机仓 `HEAD=a834ebd70c030d801014715b21af07de7fb1b52d`，`HEAD:src=e69021117158cbdc7e6d75220c5247fe631bbe58`。工作树已有未提交修改，本页只以 `HEAD` 源码和已有带版本回归为证；当前生产完整身份未知，见 [A0](00-identity.md)。相机工作流地图的 `HEAD` 文件 SHA-256 为 `c5b73011623ea846bba51ced419dd12563f9480b88dbb048023fd6db6e6545c4`。设计地图用来定位入口，能力是否实现仍由源码、测试或历史回放判定。

## 1. 十一层契约、入口和证据等级

以下路径均相对相机仓。`code+test` 只证明仓库这一版存在并有针对性合同测试；`historical_dev` 是带独立身份的旧回放；两者均不等于本时刻生产通过。相机的产品目录、事实和 SDK 证据是领域专用，不作为 DAQ 产品事实。

| 层 | 可迁移的能力契约 | 相机仓实际入口与检查点 | 已有证据及边界 |
| --- | --- | --- | --- |
| L01 入口与身份 | 入口传入角色、来源、附件和同一轮 ID；身份/权限在取证前确定 | `src/api/routes.py`、`src/platform_identity/`、`src/api/server.py`；`tests/contract/test_platform_identity_v1.py` 对冻结身份合同与错误形状设门 | `code+test`；不同渠道的当前生产接入状态未知，不能推定 DAQ 复制同一入口 |
| L02 上下文管理 | 追问合并当前条件，独立换题重置咨询记忆但保留会话日志；原问与有效问可追 | `src/agent/session.py:209-315` 的 `SessionContext.apply_schema/replace_topic`；`src/agent/orchestrator.py:584-640`、`:1683-2020` 的上下文改写/追问判别；`tests/unit/test_session.py`、`test_orchestrator_loop.py:763-951` | `code+test`；需要按同一请求检查状态确实进入工具，不能把“保存过历史”当作有效继承 |
| L03 请求理解与实体接地 | 原问锚点、产品/变体、硬约束进入结构化请求；JSON 或校验错误留下可见降级 | `src/agent/schema_extractor.py:142-205`、`src/agent/schema.py`；`tests/unit/test_schema_extractor.py` 覆盖上文、选项关系、LLM JSON 和验证失败；`src/agent/loop/tools.py` 的 `resolve_model`/事实查询 | `code+test`；关键词补偿是相机域实现细节，DAQ 需要验证同等目标守恒，不应照抄产品词表 |
| L04 能力编排 | 取证工具按目标/条件调用，工具失败和预算终态可见，避免自由文本绕开终稿协议 | 当前 Loop 路径 `src/agent/orchestrator.py:990-1105` → `src/agent/loop/runtime.py:570-705` → `src/agent/loop/tools.py:566,640,1993`；`test_orchestrator_loop.py` 的预算/错误/条件关系案例 | `code+test`；工作流图里的 legacy Planner/Capability 路径不能直接当作当前 Loop 的必经节点 |
| L05 知识生产与发布 | 产品事实、SDK、官方链接分层；字段条件、来源与冲突有发布裁决 | `Knowledge/_facts/`、`Knowledge_SDK/`、`Knowledge_Links/`；`src/facts/`、`src/agent/official_links.py`；`tests/unit/test_facts_schema.py`、`test_official_links.py` | `code+test`；相机事实内容不可迁移，DAQ 应对照治理机制与自己的已审原件 |
| L06 检索与证据工具 | 检索有型号、来源和适用范围；未命中与工具错误分开 | `src/agent/loop/tools.py:1214` (`search_knowledge`)、`:1722` (`sdk_evidence`)、`:1993` (`dispatch`)；`src/agent/official_links.py:147` | `code`；具体召回质量要看版本固定的工具结果，文件存在不证明每题都检出 |
| L07 覆盖与终稿 | 需求覆盖分 full/partial/empty，`submit_answer` 是唯一交付出口；已知部分与未知边界要分开 | `src/agent/loop/evidence_policy.py:24-84` 定义需求状态及策略接口；`runtime.py:900-950,1320-1575` 验证终稿、附件证据并输出 coverage/outcome；`tests/unit/test_loop_evidence_policy.py`、`test_loop_answer_contract.py` | `code+test`；通用策略接口是可注入点，不能假定相机当前每题都启用领域 policy；需追到实际运行配置 |
| L08 提示与控制边界 | 提示承载答复指令，来源/URL、协议、图片等确定性门由运行时执行 | `src/agent/loop/runtime.py:580,661,909-950`；`src/agent/loop/answer_contract.py`；`tests/unit/test_loop_answer_contract.py`、`test_loop_evidence_policy.py` | `code+test`；`submit_answer` 字段、URL 校验和 outcome 是硬约束，不以答案字数替代覆盖 |
| L09 结果与可观测性 | 同轮保留 answer、sources、outcome、tool_calls、fallback、trace/模型回显，可对 SSE 与历史回查 | `runtime.py:1419-1585` 的 done；`src/agent/tracing.py`、`src/api/stream.py`、`src/storage/postgres_data_flywheel.py`；`tests/unit/test_orchestrator_loop.py:1117` | `code+test`；历史 Dev 结果有 `stream_answer`/`answer`，但当前线上 UI 是否一致另查 |
| L10 配置与发布身份 | 配置模型与 Provider 实际回显分列；发布前核对 build、模型和目标环境 | `src/config.py`、`src/agent/loop/adapters.py`、`runtime.py:1575-1595`；`tests/unit/test_feedback_closure_observability_probe.py` 校验实际模型与构建身份 | `code+test`；本时刻相机生产模型/知识仍 `version_unknown`，旧回放模型不填作生产配置 |
| L11 反馈与质量门 | 反馈关联 turn/trace，经复审归因，再进入泛化题/知识任务；结构通过与专业质量分别验收 | `src/api/routes.py:622`、`src/storage/postgres_data_flywheel.py:285`、`src/storage/review_center.py`、`deploy/scripts/create_turn_review.sh`、`evals/run_eval.py`；下述历史 Dev 复审 | `code+historical_dev`；脚本和库表证明通路存在，不能据此声称所有生产反馈都已闭环 |

## 2. 一条带身份的质量回放

相机仓 `evals/results/post_release_feedback_20260828_dev_a075953_final/manifest.json` 记录：**development**、`production_eval=false`、最终 Git `a075953`、配置模型 `claude-opus-4-8`、20 题独立语义复审为 19 pass / 1 partial / 0 fail、20 题运行 outcome 均 `resolved`、`fallback_used=0`、Provider 模型回显完整 20/20。对应 `case_results.codex_reviewed.jsonl` 保留完整答案、`stream_answer`、`tool_calls`、coverage、trace 与人工复审字段。例如 `postrel_original_338le_sync` 将同步拓扑的已证事实、未审配件料号和冲突脉宽分开交付；这证明该 **2026-08-28 Dev 身份**下的一个综合答复范式，不证明当前相机生产每题可达同等质量，也不证明 DAQ 同域事实。

这份样本能作**质量门方式**的基线：题目来自反馈、原问题与泛化题并存，运行结果和独立语义评审分列。不能把 19/20 迁移成 DAQ/相机当前版本的对比分数；A9 将只比较质量门覆盖和最终发布身份是否重新验证。

## 3. DAQ 对照时保持的边界

1. 两仓 `src` tree 一致，通用运行时能力在源码层是共享的。若 DAQ 仍有差距，优先沿 **DAQ 装配、状态注入、领域知识/角色、提示、评测与发布身份**找第一次偏离；不能先假定通用 `LoopRuntime` 被“抄坏”。一个实质装配差异是：相机 `orchestrator._build_loop_runtime` 当前没有传入可选 `evidence_policy`，DAQ 认证入口明确注入 `DaqEvidencePolicy`。因此 DAQ 目标账本的误认领可造成相机 Loop 中不存在的拒答路径；这是需要核查的领域设计差异，不是共享运行时源码差异。
2. 相机 Loop 当前不必经旧 `Capability Planner`。DAQ 不要求复制相机 Planner 类名，但必须证明原问目标到工具和终稿仍守恒。
3. 相机历史回放已经显示专业答案需要同时交付“能确认的部分”和“不能确认的部分”。DAQ 的 `CASE-002/003/004` 应核对是目标没进入、证据没检出、已检出被账本拦下，还是终稿漏写。
4. 相机身份和角色的部署差异仍待只读生产回显；在此之前，本页只作为仓库及历史 Dev 工程基线。

# A7：来源、需求账本与终稿字段

## 1. 实际门的责任

两仓共享的 `src/agent/loop/runtime.py` 强制单独调用 `submit_answer`，验证终稿 schema、URL、附件/图片证据和运行 outcome，并从提交结果渲染正文。相机 `orchestrator._build_loop_runtime` 当前没有注入可选领域 policy；DAQ 在 `daq_fae/domain_evidence.py` 明确注入请求级 policy：只有工具返回与需求 ID 匹配且带受审来源，需求才从 `unknown` 转为 `satisfied`；`snapshot` 输出 `requirement_status`、能力 coverage。这是共享 Loop 源码下会形成不同终稿行为的装配差异。DAQ 终稿提示 `prompts/reviewed_knowledge_system.md` SHA-256 `72a0ec37d9fbac1664b7f4874816ad44359c0b05d8ec0c91f71239cb0c45a02e`，要求 `safe_abstained` 的已知局部事实放 `basis`、缺口放 `missing`，不填 `conclusion`。提示是指令，不能代替工具和确定性门的实际执行。

| 子目标 | 工具来源/条件 | 旧需求账本 | 旧 `submit_answer`/可见正文 | 失效类型 |
| --- | --- | --- | --- | --- |
| `CASE-003` 单机 TF 卡流程 | `procedure:ego-pro-standalone`，已审，流程工具两次 `ok` | `req_goal_437…=unknown`，原因关联 `indicators` 未映射，policy retry 2 | `safe_abstained` 称“没有任何一条资料确认为能回答”；已知步骤未交付 | **有来源但账本未认领**，并由模型终稿放大。现行源码/测试有指标别名修复，当前发布新知识身份未做同题实答 |
| `CASE-004` 两版水平/垂直 FOV | 四个变体 claim 在 sources，带 `resolution_variant` 查询 `ok` | 泛字段两个需求仍 `unknown`，policy retry 2 | `safe_abstained` 只列版型，不写 165°/130°、149°/100° | **目标粒度与证据身份不匹配**；先前无变体 `not_found` 是保护，不是最终无来源 |
| `CASE-005` SDK 版本线索 | 当轮 `check_software_support` 参数已有 2.9.0，正式兼容记录 `not_found` | 软件支持需求 `unknown` 属正确边界 | `safe_abstained` 拒绝官方化正确，却把 2.9.0 列为缺失 | **已知条件漏写/误报缺失**；不能归因于真实兼容证据缺口本身 |
| `CASE-006` 日志 WARN | 用户附件可读、coverage `full`；官方支持未知 | 附件需求 `satisfied`，支持需求 `unknown` | `safe_abstained` 却称启动请求失败 | **用户线索被越界提升为操作终态**；后续同代码新门的聚焦 Dev 反例通过 |

## 2. 当前代码的保护与剩余风险

**新发现的入口装配缺口（`EVD-005`）**：`daq_fae/app.py:574-585` 的本机 Dev 路径创建 `DaqEvidencePolicy(question=prepared.schema.effective_question, …)`；`daq_fae/authenticated_chat.py:242-253` 和 `daq_fae/platform_tasks.py:171-183` 创建同一 policy 时均省略 `question`，因此使用默认空字符串。`domain_evidence.py:511-520` 的“模型添加了用户与已审来源均未给出的连接方式”门在 `self.question` 为空时直接跳过；问题中的用户 API 符号豁免、HUB 数值辅助识别及部分测量条件判断也依赖它。无模型复用仓库已有测试样本：同一已审 SDK 工具结果、同一含未授权 USB 连接说法的假设 `safe_abstained`，传入原问得到 `request_evidence/daq_unrequested_interface_in_answer`，省略原问得到 `allow`；两次需求均为 `satisfied`。原始探针 `tmp/daq-camera-comparison-20261020/a7-policy-question-probe.json` SHA-256 `3a5ff46d1769538b9d259b447c529affc17b5faa7cdd58da04d6ca437b33a965`。这**确认 10-20 源码中的入口行为不一致**，尚无生产实际答案证明它触发了用户本次症状；Task 在该发布主知识上 `tasks_enabled=false`。

`DaqEvidenceSession.evaluate` 已实现几类确定性边界：未满足关键目标不能 `resolved`；有局部证据时提示改为 `safe_abstained` 并逐项交付；用户附件里的告警不能无终态记录就宣称请求失败；错误或不可信 URL、内部 provenance 等不得出现在正文。`domain_tools._requirement_matches` 的严格匹配保护错实体、错变体和未审条件。但其副作用是**一条目标含无法映射的条件时，已审可用部分也可能不被认领**；`CASE-003` 旧版正是这种路径。`CASE-004` 说明“返回了四条来源”与“泛需求已满足”是两件事。现行局部修复不能替代最终发布身份的端到端复测。

提示词对“已知部分仍须交付”、单口与总供电、变体和用户日志边界已有明确陈述；它对答案内容的覆盖没有独立结构化逐句证明。每次 `submit_answer` 被 policy 重试，只能说明有门参与，**不能**证明最后的 `basis` 包含全部已知子目标。历史 `CASE-005` 是明确反例。修复时应把 `goal_id → matched_requirement_id → source_id/适用条件 → answer 字段` 做可观测映射，并对“遗漏已知部分”和“无据正向断言”同时设正反验收。

## 3. 不能越权的结论

`safe_abstained` 并不必然是差答案；三设备同步、精确软件组合和官方链接在当前发布快照中确实缺受审证据，保留未知是正确。质量退化发生在本轮已取到、且与原目标条件相符的内容没有兑现，或把用户附件/相邻条件证据写成更强结论。`CASE-003/004/005/006` 历史样本的 SSE 与持久历史一致，故这些样本的缺陷不是展示层删字。当前生产的同类请求仍需实际 trace 与身份核查。

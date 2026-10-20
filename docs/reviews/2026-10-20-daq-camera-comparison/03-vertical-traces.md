# A3：历史 DAQ 请求的逐层追踪

**范围**：[A1 冻结案例](01-cases.md)中的隔离 Dev 历史运行。原始 `results.jsonl`、`traces.jsonl` 与独立复审 JSONL 的路径、SHA、代码和知识身份见 A1 私有索引。这里的“首次偏离”只针对该次运行；当前生产版本和当前用户的线上原题均未取得，不作线上根因断言。

## 1. 请求 → 工具 → 账本 → 答案

| 案例 / trace | 入口与请求理解 | 工具和已审来源 | 账本、终稿和可见结果 | 该次运行最早可证偏离 |
| --- | --- | --- | --- | --- |
| `CASE-001` `82539398…`，旧代码 `56940355…` | 问 HUB 下游端口、单口供电和能否外推整机 | 独立复审记录 `planned_capabilities=[]`、`actual_capabilities=[]`、`sources=[]` | `outcome=schema_error`、`fallback_reason=schema_aggregate_power_unasked`，可见 17 字；没有进入取证 | **L03 请求 schema**；本次不是“知识没有 HUB 事实”或 UI 截短。旧复审未保存足够工具原件来继续细分 schema 判错逻辑 |
| `CASE-002` `c6178f3b…`，旧代码 `56940355…` | 同题包含“EGO Pro 单机流程”和“三设备同步”两个子目标 | 计划 `inspect_topology`、`lookup_procedure`，实际均调用；复审有 3 个 sources，但两能力 coverage 均 `empty` | 668 字 `safe_abstained`，正确拒绝三路同步，错误称无单机流程 | **L06/L07 之间**；旧复审不含逐条工具 payload 与需求匹配 ID，无法在“工具未返回正确单机流程”和“返回后账本未认领”之间再判。先保留观测阻断 |
| `CASE-003` `ab90927…`，旧代码 `759484d…` | schema 正确保留本机 TF 卡、开机/录制/停止及灯态，但将 `indicators` 记为 `unmapped_condition_keys` | `lookup_procedure` 两次 `ok`；`sources` 含已审 `procedure:ego-pro-standalone`；预算 4/24 | 需求 `req_goal_437…` 一直 `unknown`，`evidence_policy.retry_rounds=2`，终稿 `safe_abstained` 称未核对出任何直接资料 | **L03→L07 合同失配**：schema 的未映射条件让需求不能由已检出的流程认领。旧代码中 `domain_tools._requirement_matches` 对 `unmapped_condition_unverified` 直接返回 false；这是该旧身份下有据弃答的可解释路径。不能删除灯态条件来换“通过”，应审其与流程步骤的适用关系 |
| `CASE-004` `7f9124a…`，旧代码 `4e9bbad…` | schema 把水平/垂直 FOV 建成两个泛 `EGO 双目` 目标，未建 1600/1920 两变体的四个待交付值 | 前四次无变体查询 `not_found`，后八次带 `resolution_variant` 查询 `ok`；sources 含四条已审 FOV claim | 两需求仍 `unknown`，`lookup_spec` coverage `unknown`，`safe_abstained` 只说有两版并让用户先确认，漏四个数值；预算 13/24 | **L03→L07 目标粒度失配**：原问明确未指定变体，预期应分别列可证变体；泛字段需求无法认领有条件的四条 claim。并非检索完全未命中或预算耗尽 |
| `CASE-005` `c2940c9…`，旧代码 `94f2389…` | 追问 schema 有固件 `v9.9.9`；有效问保留上轮原问线索，但未在 schema 条件列保留 SDK 2.9.0 | `session_state` 被调用；`check_software_support` 的实际参数却包含 `version=2.9.0`，并返回 `not_found`，另有搜索 `ok` | 答案正确拒绝把 E42 官方化，却说 SDK 版本不明 | **L07 终稿错误**可证：同轮工具参数已有 2.9.0，不能把最后的“缺 SDK 版本”归咎于完全未继承上文。schema 结构化条件缺失是上游脆弱点，但本次最终丢失发生在已知字段交付 |
| `CASE-006` `f17a7da…`，旧代码 `aa94db6…` | schema 拆为日志阅读与官方软件支持两个目标 | 附件搜索/阅读 `ok`，`check_software_support=not_found`；附件 coverage `full`，支持目标 `unknown` | `safe_abstained` 中却把一次 USB 超时 WARN 表述为“这次采集启动请求失败了”；SSE/历史一致 | **L07 证据范围到终稿**：已读用户日志不含最终请求失败状态，不能从 WARN 推出终态。后续 `CONTROL-001` 在 `ba50906…` + 旧主知识的聚焦 Dev 复审通过，属于 `historical_fixed` 的局部验证 |

`CASE-003/004/005/006` 的原始运行均为 HTTP 200、`stage_lifecycle_closed=true`、`stream_history_stage_equal=true`、`trace_id_equal=true`、`outcome_equal=true`、`source_ids_equal=true`。因此这些**历史运行**的可见缺陷已在服务端终稿出现，L09 SSE/历史丢字假设可排除；尚无同一请求的浏览器 UI 截图，不能排除另一次线上展示问题。`CASE-001/002` 的旧独立复审没有同等字段链，L09 保持未知。

## 2. 反例与时间属性

- `CASE-003` 的同版资料库中 `procedure:ego-pro-standalone` 已发布，且该次 trace 工具为 `ok`；反例直接排除“原始资料完全不存在”作为该次首因。10 月 20 日主知识继续包含此流程，但代码身份已变化，不能拿旧失败推成现行缺陷。
- `CASE-004` 的四个变体 FOV claim 在该次 sources 均出现，分别属于 1600×1200 与 1920×1200。单独无条件查询 `not_found` 是防混版保护；真正应验证的是“带条件证据如何合成两版的部分确定答案”。
- `CASE-005` 的同轮支持查询参数含 SDK 2.9.0，排除“模型/工具完全没得到上文版本”这一强说法；它没有排除 checkpoint 结构缺失的系统性风险。A5 继续查字段生命周期。
- `CASE-006` 与 `CONTROL-001` 只说明旧版过推断被聚焦修复。后者主知识仍为 `3c8df43e…`，不代表 10 月 20 日主知识及所有附件反例已回归。当前 `daq_fae/domain_evidence.py` 有针对“WARN 不等于请求失败”的文字门，但需按最终发布身份再验。

## 3. 仍缺的横截面

1. `OBS-001` 的生产原问、完整可见答案、时间、角色、trace 与运行身份均未取得，不能把以上任何历史症状贴到用户这次的线上请求。
2. 旧结果的 `done.tool_calls` 只有工具参数与状态、`sources` 有来源 ID，没有逐项原始返回全文；`CASE-002` 的 L06/L07 分界及 `CASE-003/004` 的精确匹配 ID 需通过同身份工具探针或现存更完整回放补足。
3. 历史复审已有修复，但最终 `ba50906…` + 主知识 `19d1a31…` + SDK `2bfd053…` 的完整 55 核心题和 13 组多轮未整套跑完。A9 对应质量门登记为未验证，不通过旧 trace 推定当前质量。

**追加无模型反证**：A7 的 `EVD-007` 在最终本机代码与正式主知识下重建了 `CASE-002/003/004` 的目标、工具和 policy 判定。`CASE-003` 的指标别名及 `CASE-002` 的目标拆分在该确定性路径已修复；`CASE-004` 的泛字段需求仍未被四条变体来源认领。更关键的是，三案的“有据但漏交付”假设 `safe_abstained` 仍均被 policy 放行。它把这些旧案的**残留可能路径**收窄到 L07/L08 交付门，但不把假设终稿冒充真实生产输出。

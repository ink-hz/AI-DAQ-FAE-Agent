# DAQ FAE / 相机 FAE 对照分析索引

**启动/完成可访问证据审查**：2026-10-20。**任务书**：[A0–A10 分析任务](../../superpowers/plans/2026-10-20-daq-camera-fae-comparison-analysis-task-brief.md)。**后续执行**：[DAQ 回答质量改进总任务书](../../superpowers/plans/2026-10-20-daq-fae-quality-improvement-task-brief.md)。**状态**：A0–A10 的本机源码、历史 trace、发布知识、独立复审和确定性合同已全部核查；当前生产请求身份与用户样本因无内部只读入口/具体 trace 保持精确观测阻断。先看[完整结论](11-analysis-summary.md)和[发现/反证](10-findings.md)。表中 `confirmed_gap` 可能只针对历史 Dev 或本机发布快照，必须打开记录核对时间属性。

| 层 | 能力记录 | 状态 | 主要证据 / 待取得项 | 关联案例 / 发现 |
| --- | --- | --- | --- | --- |
| L01 入口与身份 | [CAP-L01-001](records/CAP-L01-001.md) | `confirmed_gap` 本机装配 | [入口/上下文](05-entry-context.md)；当前生产角色未知；10-20 代码的 policy 入参不一致 | FIND-006/007 |
| L02 上下文管理 | [CAP-L02-001](records/CAP-L02-001.md) | `confirmed_gap` 旧版 | [状态流](05-entry-context.md)；旧追问终稿误报已知字段缺失 | CASE-005 / FIND-004 |
| L03 请求理解与实体接地 | [CAP-L03-001](records/CAP-L03-001.md) | `confirmed_gap` 旧版及本机合同 | [目标守恒](06-request-orchestration.md)；泛 FOV 需求与四个变体来源仍失配 | CASE-001/003/004 / FIND-001/008 |
| L04 能力编排 | [CAP-L04-001](records/CAP-L04-001.md) | `confirmed_gap` 旧版 | [工具与预算](06-request-orchestration.md)；CASE-002 分界仍缺 payload | CASE-001/002 / FIND-001 |
| L05 知识生产与发布 | [CAP-L05-001](records/CAP-L05-001.md) | `confirmed_gap` 发布快照 | [12 项知识矩阵](04-knowledge.md)：7 项有已审目标证据、5 项缺；27 记录仅引用 9 原件 | FIND-002 |
| L06 检索与证据工具 | [CAP-L06-001](records/CAP-L06-001.md) | `confirmed_gap` 本机匹配合同 | [无模型正反探针](04-knowledge.md)确认来源可检出；泛 FOV 目标匹配 ID 为空；当前线上检索未知 | FIND-001/002/006/008 |
| L07 覆盖与终稿 | [CAP-L07-001](records/CAP-L07-001.md) | `confirmed_gap` 旧版及本机合同 | [来源→账本→终稿](07-evidence-synthesis.md)；已知部分可漏交付并获 `allow` | CASE-002–006 / FIND-001/004/005/007/008 |
| L08 提示词与控制边界 | [CAP-L08-001](records/CAP-L08-001.md) | `confirmed_gap` 确定性门 | [提示与确定性门](07-evidence-synthesis.md)；交付义务与 policy 入参有缺口，提示词因果未知 | FIND-001/005/006/007/008 |
| L09 结果与可观测性 | [CAP-L09-001](records/CAP-L09-001.md) | `excluded` 旧四案 SSE/历史假设 | [运行展示](08-runtime-presentation.md)；当前用户 UI 未核 | CASE-003–006 / FIND-006 |
| L10 配置与发布身份 | [CAP-L10-001](records/CAP-L10-001.md) | `evidence_blocked` | [双侧身份](00-identity.md)；当前生产内部身份未独立读取 | FIND-006 |
| L11 反馈与质量门 | [CAP-L11-001](records/CAP-L11-001.md) | `confirmed_gap` 发布记录 | [质量门](09-quality-loop.md)；最终身份 55/13 未完整闭合，`allow` 不等于语义通过 | FIND-003/008 |

## 证据与案例登记规则

- `CASE-nnn` 是脱敏用户任务或带版本的历史回放，`EVD-nnn` 是可重取的原始材料，`CAP-Lxx-nnn` 是单项能力对照，`FIND-nnn` 是经反例检验的发现。同一案例可以指向多个发现；旧版结果保留旧版身份。
- 含用户资料的原件放 Git 忽略的 `tmp/daq-camera-comparison-20261020/`；记录只存脱敏摘要、哈希和受限指针。运行回放仅在隔离 Dev；生产只读。
- `EVD-001`：本机 Git 与公开 GET 快照，路径/哈希见[身份记录](00-identity.md)。`EVD-002`：七条旧 Dev 结果冻结索引，路径/哈希见[案例](01-cases.md)。`EVD-003`：相机仓 `HEAD` 源码、测试与带版本 Dev 回放，见[相机基线](02-camera-baseline.md)。`EVD-004`：DAQ 10-20 发布主知识/SDK manifest 与 14 条本机无模型工具正反探针，原件哈希见[知识链](04-knowledge.md)。`EVD-005`：DAQ Dev 与认证/Task policy 问句注入差异的无模型探针，路径/哈希见[证据合成](07-evidence-synthesis.md)。`EVD-006`：本地 16,942 条 trace 的时间/身份元数据及内部健康可达性核查，见[身份记录](00-identity.md)。`EVD-007`：最终本机代码、正式主知识的三案正反答案合同探针，路径/哈希见[证据合成](07-evidence-synthesis.md)。

## 分析任务闭合状态

| 批次 | 状态 | 审查结果 |
| --- | --- | --- |
| A0 身份与索引 | 本机/历史身份已记录；生产内部身份经可达性核查后明确阻断 | [00-identity.md](00-identity.md) |
| A1 案例冻结 | 历史案及当前用户症状已登记；当前线上样本缺原问/trace | [01-cases.md](01-cases.md) |
| A2 相机仓库与历史 Dev 基线 | 已记录；当前生产完整身份未知 | [02-camera-baseline.md](02-camera-baseline.md) |
| A3–A9 逐层分析 | 历史 Dev、最终本机代码/知识、trace 库、发布与评测已审；当前生产相关判断明确阻断 | [03](03-vertical-traces.md) · [04](04-knowledge.md) · [05](05-entry-context.md) · [06](06-request-orchestration.md) · [07](07-evidence-synthesis.md) · [08](08-runtime-presentation.md) · [09](09-quality-loop.md) |
| A10 交叉验证与改进输入 | 八项发现及反证已记录；后续修复/发布另立计划 | [10-findings.md](10-findings.md) · [11-analysis-summary.md](11-analysis-summary.md) |

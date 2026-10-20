# A4：DAQ 知识来源、发布与检索边界

**身份**：本机 `release-20261020/main-root` 活动主知识 `19d1a31…`，SDK `2bfd0538…`，代码 SHA `ba50906…`；与 [10 月 20 日 DAQ 发布记录](../2026-10-20-daq-scenario-production-release.md)所列相同，但仍未独立读取当前生产指针。`EVD-004` 是 Git 忽略的 `tmp/daq-camera-comparison-20261020/a4-no-model-probes.json`（SHA-256 `424ba19ff69501b739de458df5a9fbf626fe0bfdfc720c2a28f11aa93d9b17e9`）：载明 manifest SHA、角色、原问、精确工具参数、状态、原因与命中 ID。探针直接装载本机发布 manifest，**不调用模型或生产服务**。

## 1. 发布总体口径

主知识 manifest：`source_count=276` 是纳入库存的来源对象数，**不是 276 个可回答问题**；`record_count=answerable_count=27`，其中 17 claim、8 entity、1 procedure、1 topology，`section_count=0`。27 条均为 `verified`，`role_summary` 显示 `internal_fae=27`，`channel=0`、`tmall_support=0`。已审记录带 `fact_review`、`access_review` 与来源哈希；只说明这些记录的发布手续和范围，不说明 276 个原件均已结构化。SDK 发布有 24 条包内用法记录及 17 条变更日志，绑定代码、主知识和 runtime identity；SDK 用法或日志陈述不自动证明某设备/固件/平台组合支持。

相机侧有产品事实、SDK 与已核验官方链接的分层治理及字段覆盖规则，见 [A2](02-camera-baseline.md)。可迁移的是“权威原件→字段/范围裁决→角色发布→工具证据→终稿”的门；相机内容不补 DAQ 的缺口。

## 2. 以本轮真实任务分母核对五态

表内分母是 **12 个原子需求**，从 A1 六个案例拆出；“有来源”仅在发布 manifest 可见的原件/记录范围内判定，“检出”是 EVD-004 的确定性工具探针或原历史 trace，不能混算。`?` 表示尚无同身份探针；`—` 表示前一环不存在。`internal_fae` 是本表目标角色。

| 原子需求（12） | 原件/候选 | 已审发布与角色可见 | 工具检出 | 足以支持该需求 | 证据及损耗层 |
| --- | --- | --- | --- | --- | --- |
| 1 HUB 下游端口数 | 有，规格书 | `claim:hub-downstream-port-count`，是 | `ok` | 是，可说 4 口 | `hub_count`；`CASE-001` 旧失败先在 schema |
| 2 HUB **单口**供电 | 有，规格书 | `claim:hub-downstream-port-output`，是 | 带 `per_port=true` 时 `ok` | 是，可说 5V/2A 的单口值 | `hub_per_port_with_condition`；无条件查询明确 `claim_conditions_required` |
| 3 HUB **整机总输出**能否由单口值推出 | 发布中未见总输出 claim | 无 | — | 否；不得算 4×单口值作承诺 | 同一原问的独立子目标；来源缺/未审待拆，先标 `not_published` |
| 4 EGO Pro 本机 TF 卡录制流程 | 有，快速使用说明 | `procedure:ego-pro-standalone`，是 | 原问约束下 `ok` | 在流程自身范围内可答；固件范围仍 `unknown` | `ego_pro_procedure`；`CASE-003` 旧账本却 `unknown` |
| 5 EGO Pro+WristCam+HUB 三路同步保存 | 库存可能有各单品资料，未见已审组合拓扑 | 无组合记录 | `not_found` | 否 | `three_device_sync`；不能用单机流程外推 |
| 6 EGO 双目 1600 版水平 FOV | 有，规格书 | `claim:ego-1600-horizontal-fov`，是 | 带变体 `ok` | 是，165° | `ego1600_fov` |
| 7 EGO 双目 1600 版垂直 FOV | 有，规格书 | `claim:ego-1600-vertical-fov`，是 | 旧 `CASE-004` 同知识身份已 `ok`；本快照未单独探 | 是，130°，保留变体 | `CASE-004` sources/工具；当前发布仍列此 ID |
| 8 EGO 双目 1920 版水平 FOV | 有，规格书 | `claim:ego-1920-horizontal-fov`，是 | 旧 `CASE-004` 同知识身份已 `ok`；本快照未单独探 | 是，149°，保留变体 | 同上 |
| 9 EGO 双目 1920 版垂直 FOV | 有，规格书 | `claim:ego-1920-vertical-fov`，是 | 带变体 `ok` | 是，100° | `ego1920_fov`；无变体检索 `variant_selector_required` 是防串版门 |
| 10 EGO Pro 固件 v9.9.9 + SDK 2.9.0 的实际兼容性 | 有包内 SDK 用法/部分变更日志，但未见组合测试记录 | 无精确 software record | `check_software_support=not_found` | 否；正负结论均无授权 | `software_support`；SDK 关键词查询即便 `ok`，只返回用法/日志，不能升格为组合支持 |
| 11 日志 E42 的官方含义及最终请求失败状态 | 只有用户现场日志；未见官方状态定义/最终结果 | 无已审官方断言 | 附件可读，官方证据无 | 否；只能复述观察到的 WARN | `CASE-006`；用户附件不能转成官方兼容/故障结论 |
| 12 EGO Pro 可交付官方下载链接 | 未见受审 DAQ 链接记录 | 无 `link` 记录 | `official_links=not_found` | 否 | `official_link`；不得拼 URL |

**可答范围计数**：这 12 个原子需求中，7 项有已审、角色可见的目标事实/流程（1、2、4、6–9），5 项无足够发布证据（3、5、10–12）。其中 7 项可答里，EVD-004 精确探针验证 5 项，另 2 项由旧同知识身份 trace 验证。此计数只描述本批任务与**本机发布快照**，不是 DAQ 全域覆盖率，也不代表模型能把 7 项都交付。若 `CASE-004` 的四个 FOV 合成失败，那是证据到目标/终稿的工作流损耗，不能记成来源缺失。

## 3. 正反工具合同及角色

`EVD-004` 的关键正反对照：HUB 单口输出在 `per_port=true` 检出、无条件被拒；EGO FOV 带 `resolution_variant` 检出、无变体被拒；EGO Pro 单机原问检出流程、三设备同步无已审拓扑；`internal_fae` 检出 HUB 端口，而 `channel` 角色同参数 `not_found`。这些拒绝是适用范围/权限边界，不能一律当作检索质量差。

SDK 探针 `sdk_usage` 命中 `daq-sdk-android-record-state-019`，可支持包内 `SAVING`/`RECORD_SAVED` 回调用法；`sdk_combo_not_support` 虽 `ok` 命中三个 SDK 记录，却没有设备组合支持结论。这验证“工具返回 ok/source 数量”不足以证明目标已满足。`check_software_support` 对该精确组合 `not_found`，必须保持未知。

## 4. 知识改进输入与未判事项

- **优先补受审组合证据**：三设备同步保存、整机总供电、精确软件/固件组合支持、官方链接。先核原件和产品 owner，再决定能否发布；原件缺失时输出明确缺口，而不是写推测性知识。
- **修复已发布却未兑现**：EGO Pro 单机流程、EGO 双目分变体 FOV 已有检出证据，见 A3；这些先查目标/账本/合成，盲目扩充知识不能治本。
- 本轮尚未逐份复审 276 个来源对象，也没有生产角色/实际发布指针的独立回显；来源层“确实没有原件”与“原件存在但未升为已审记录”暂不合并。后续知识工作包需对每个缺口补原件路径/哈希、owner 裁决、字段与版本、角色、正反工具探针及发布 ID。

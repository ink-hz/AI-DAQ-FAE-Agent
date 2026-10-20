# A6：请求理解、实体条件与工具编排守恒

## 1. 双侧机制

相机仓当前 Loop 以 `RequestSchema`、`SessionContext`、`ToolBox` 和受约束的 `submit_answer` 完成同一不变量（[A2](02-camera-baseline.md)）；旧 Planner 不必经。DAQ 在 `daq_fae/request_schema.py` 用受校验的 `DaqRequestSchema` 表达 `goals`，`workflow.py:145-253` 将原问、历史与已审接地合成 `PreparedWorkflow`，`task_context.py:245-585` 生成要求，`workflow.py:63-105` 从目标收缩可用工具，`domain_tools.py:357-460` 执行角色过滤与参数检查。源码层不是简单复制同一编排：DAQ 加了领域 schema、接地和目标账本，复杂度高于共享 `src`。

## 2. 四个守恒案例

| 原问必须保留的目标/条件 | 历史 Dev schema/plan | 工具实况 | 首次偏离与当前状态 |
| --- | --- | --- | --- |
| `CASE-001`：HUB 口数、**单口** 5V/2A、整机总供电未知是三个不同口径 | 旧版 `schema_aggregate_power_unasked` 直接 `schema_error`、无目标、无工具 | 当前发布知识无模型探针：端口数 `ok`；单口带 `per_port=true` `ok`，无条件被拒；总供电未发布 | 旧版首次偏离 L03；现代码 `tests/test_request_schema.py` 有总功率/口径合同，最终发布身份未以同题整套实答验证 |
| `CASE-003`：EGO Pro 本机 TF 卡、按键步骤与灯态 | 旧 schema 的 `indicators=['led']` 被列为未映射；流程目标因此无法认证 | 两次 `lookup_procedure=ok`、已审流程 source 交付 | L03 条件词归一与 L07 匹配合同断裂。现行 `request_schema.py` 与 `tests/test_request_schema.py` 有指标别名及步骤约束回归；发布知识无模型探针同原问检出流程，真实答案未在新知识身份重跑 |
| `CASE-004`：未指定版型时给 **1600 与 1920 各自**水平/垂直 FOV | 旧 schema 只有泛 `EGO 双目` 的水平、垂直两个需求 | 工具带变体后四值检出；泛目标仍 `unknown` | L03 目标粒度与 L07 账本不一致。10 月 19 日复审记 `cc03-retest-31` 给出四值，但属旧主知识/历史 Dev；当前发布保留四条 claim，最终身份整套回放未知 |
| `CASE-005`：上轮日志 E42、EGO Pro 固件及 SDK 2.9.0，询问能否官方化 | 旧 schema 条件仅列固件，SDK 版本未显式入该 goal；有效问带上轮原问 | 但 `check_software_support` 参数里确有 `version=2.9.0` | 对该次错误，首次**可证**的丢失是 L07 终稿而非 L04 工具；结构化 schema 仍有鲁棒性缺口。后续附件重绑的 Dev 聚焦复审通过，非最终发布身份全量通过 |

由此不能用“调用过对应工具”证明编排正确。至少要同时比较 `original_question → goals/conditions → requirements → allowed_tools → tool arguments → matched_requirement_ids → answer`。`DaqToolBox._requirement_matches` 对 `unmapped_condition_unverified` 不认领；这保护未审条件，却也会让可回答的已知部分随未映射条件一起弃答。`lookup_spec` 对变体、单口条件要求精确，可防串版；复合目标应拆开认领，不能为了提高 coverage 删掉用户条件。

## 3. 工具选择、失败与预算的区分

- `CASE-003` 4/24 工具调用、`CASE-004` 13/24、`CASE-005` 4/24；它们的旧失败不是工具预算耗尽。`CASE-001` 在工具前 schema 错误；`CASE-002` 已计划并调用流程/拓扑但旧复审缺返回 payload，因此 L06 和 L07 分界仍需更完整证据。
- `EVD-004` 对当前主知识的 `three_device_sync`、`selection`、精确 `software_support` 均 `not_found`。这是该发布可答范围缺口，不应记作 Loop 未选择工具。SDK 关键词检索可能 `ok` 返回包内用法，却不能代替组合支持结论。
- 现代码 `allowed_tools_for` 对一般目标按能力收缩工具；`selection` 保留广工具表，注释也说明其子目标尚未另行规划。这是需要回归的编排设计点，不等同已经观测到选型错误。

**改进验收入口**：每类目标建立一组正反例，检查原问目标 ID 不丢失、条件有来源、工具参数合法、`matched_requirement_ids` 对应同实体同变体同测量条件；已知部分在另一个目标缺证时仍可交付，无据组合保持未知。最终身份的真实答案需独立 FAE/Codex 四轴复审。

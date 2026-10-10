# A3 组合流程与横切专题候选合同

本批只制备私有候选，不改变运行时检索、审核或知识发布。沿用 A2 的章节 ID、正文 SHA-256、结构化原件 path/hash/locator、依赖 ID、候选状态、空事实/权限签认和空查看/转发角色。

系统章节按稳定 topology_id 分开组织 overview、setup、recording、troubleshooting；单机与组合不得借用步骤。每个步骤有稳定 step_id、所属 topology_id 和精确原件定位。检查点未记载时保持 null，失败恢复未记载时保留 gap；不能把记录成功提示当成完整性或量化同步验收。Wi-Fi 复用 USB 配置须有原指南明确交叉引用。

泛称产品的 scope.variant_unconfirmed 为 true 时，禁止赋予 resolution_variant。软件版本、固件和硬件修订仍为未确认条件。来源声明、实际软件包、逐设备实测与端到端验证分别处理。

`daq_fae.knowledge.candidate_sections.validate_section` 是仅检查结构的候选工具：拒绝错误正文指纹、缺失来源、无效定位、跨拓扑步骤及意外审核/角色赋值。它不验证原件真实性、事实语义、权限或发布资格。私有验证器另外逐文件重算原件哈希、重读定位、核对输入指纹及章节依赖。真实签认及受控持久归档仍为后续发布门。

准备条件按 `preparation_requirements` 单独列出 axis、classification、text、source_refs 和可选 gap_id，正文必须包含同一条件。classification 区分 requirement、recommendation、reference、conditional、not_applicable、unverified；存储卡规格与容量推荐、PC 参考配置与软件启动条件分别保存。连接阶段的检查不能要求尚未完成配置的开流状态；指南顺序或自动行为不明确时登记缺口，不创造恢复步骤。

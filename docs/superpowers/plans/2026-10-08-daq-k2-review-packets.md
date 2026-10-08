# DAQ K-2 候选复审包实施计划

**目标：** 从真实资料生成可定位、可重复、可比较的 K-2 私有复审包，为频繁更新提供差异复审入口。

**边界：** 不修改共享运行时、在线知识或生产；不将候选资料自动晋升为已核验事实。

### 1. 复审包核心

**文件：** `daq_fae/knowledge/review_packets.py`、`tests/test_knowledge_review_packets.py`。

- [x] 先写失败测试：确定性、来源定位、密级候选、选择器缺口、权限全待签认、来源和议题差异。
- [x] 实现严格配方验证、候选命中、短摘录和全来源差异。
- [x] 跑目标测试与后端回归。

### 2. K-2 配方与命令

**文件：** `review_recipes/k2.json`、`scripts/daq_knowledge.py`、`tests/test_knowledge_cli.py`。

- [x] 写 CLI 红测：归档与快照不一致拒绝、输出必须私有、重跑幂等、双包差异。
- [x] 实现 `review-packet` 与 `review-diff`；配方覆盖首批实体、冲突、流程缺口和权限。
- [x] 对真实 276 文件候选运行，检查命中/缺口及密级标记；私有结果不进 Git。

### 3. 复审文档与交付

**文件：** `docs/knowledge/2026-10-08-daq-k2-review-guide.md`、`docs/reviews/2026-10-08-daq-k2-candidate-rehearsal.md`、`README.md`。

- [x] 写清每项人工裁决、角色权限和后续 K-1 记录转换；记录真实演练计数与已知缺口。
- [x] 完整后端测试、上游源码快照检查、独立代码复审；提交并推送功能分支。

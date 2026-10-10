# A3 组合流程与专题候选审计

日期：2026-10-10。状态：候选已备，未签认、未发布、未激活。

在主工作树 Git 忽略的 `data/knowledge/curated/a3-20261010/` 制备五套系统流程和四个专题，共 24 个章节、30 个带精确原件行定位的步骤，另有目录、来源重抽取审计和验证报告。目录权限 0700，文件 0600。正文、原件抽取和含内容制备脚本不提交 Git。

系统分别为 EGO 单机、EGO Pro 单机、EGO＋双 WristCam＋HUB PC、EGO USB PC 和 EGO Wi-Fi PC；专题为同步/时间戳、落盘/格式、型号消歧和软件版本边界。每个系统分别记录适用拓扑、供电/连接、软件条件、步骤/检查及失败/缺项。组合详细配对步骤按组合指南补充，Wi-Fi 配置复用保留指南的明确引用；没有将单机步骤套用为 Pro 多机流程。

输入沿用 A1 处置、A2 产品章节和 K7 候选记录；K6 PC 扩充说明用于范围核对。重新核验四份原件 SHA-256，重读 73 个精确行定位并保存新抽取指纹。全部章节保留 candidate、空事实/权限审核及空查看/转发角色；没有选定泛称 EGO 的分辨率变体。A2 及相机仓未修改。

产物指纹：

- 章节索引：`1c2b2781de8f814312157d1e64c0ed07288943ef695add53316cb7d552903f1d`
- 原件定位重抽取审计：`c6068def02c3539ca5f59aa771f7adf575737581e5dd069fc2d4ca13e656ce93`

静态验证通过原件哈希及定位重读、输入指纹、正文哈希、依赖闭合、步骤拓扑与来源、空签认/角色、权限及无交付 URL。新增来源无关的候选结构检查器以 10 个合成测试先失败后通过。此验证不代替独立内容复审、具名事实裁决、资料授权或 Dev 答案质量评测。

仍有缺口：Pro 多设备章节未完成、分辨率/硬件修订适用矩阵、精确 Viewer/固件组合、时间戳语义及量化同步验收、逐设备落盘格式矩阵和故障恢复。目录截图本批未解析，精确文件布局明确留空。未从配置配对成功推导同步精度，未从单机格式说明推导整套组合格式。软件/固件升级和交付入口待专门核验。

验证命令及实际结果：

```text
/Users/neo/Developer/work/AI-FAE-Agent/.venv/bin/python -m pytest -q tests/test_candidate_sections.py
10 passed in 0.01s
/Users/neo/Developer/work/AI-FAE-Agent/.venv/bin/python -m pytest -q tests
277 passed, 6 warnings in 11.09s
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a3-20261010/validate_private.py
systems=5 topics=4 sections=24 steps=30 distinct_sources=4 source_locations=63 online_eligible=false
```

额外核对 K6 已登记指纹版本与 K7 的四条 USB/Wi-Fi PC 记录完全一致。A1 所有 27 份文本仍需事实裁决，零项获得交付授权。没有运行模型或生产评测。

## 独立复核修正：操作时序与准备条件

复核发现 Wi-Fi 连接阶段的检查点提前要求出流，与后续开流前参数配置冲突。已把 setup 限定为设备识别/连接，出流检查移至配置后的 recording 阶段；保留指南显式交叉引用。连接是否自动出流及不同版本的时序未实测，另登记缺口，没有补写未记载的停流操作。

五套系统的 overview 和 setup 现在逐项保存准备条件，按存储卡、外部供电和 PC 环境分开记录，正文与结构化 `preparation_requirements` 同步。原件中的规格要求、容量推荐、PC/电源参考配置及条件性准备保留原级别；不把参考配置升级为实测最低门槛。Pro 保留符合规格的存储卡条件；其准备段未明确的外部电源数值保持缺口，不套用双目数值。PC 系统、架构及启动条件有来源则记录；组合指南未给出的具体架构/启动要求明确待核验。单机独立录制的 PC 条件标为不适用。

修正后仍为 24 章节、30 步骤，原件精确定位增加到 73 个。私有回归检查先在旧内容上失败三项，修正后全部通过；总验证器已纳入这些回归。最新指纹见上文。

本轮确切重跑命令：

```text
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a3-20261010/build_private.py
systems=5 topics=4 sections=24 steps=30 source_locations=73 distinct_sources=4
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a3-20261010/validate_private.py
Ran 3 tests in 0.000s — OK
systems=5 topics=4 sections=24 steps=30 distinct_sources=4 source_locations=73 online_eligible=false
/Users/neo/Developer/work/AI-FAE-Agent/.venv/bin/python -m pytest -q tests/test_candidate_sections.py
10 passed in 0.01s
```

以上命令退出码均为 0。本轮没有修改后端实现，未重复完整后端测试；前述 277 passed 是初次制备批次结果。全部章节仍待事实及权限签认，查看/转发角色为空。

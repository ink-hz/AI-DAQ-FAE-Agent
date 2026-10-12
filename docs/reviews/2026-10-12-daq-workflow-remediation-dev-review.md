# 数采 FAE 工作流修复：Dev 回放与独立答案复审

日期：2026-10-12。结论：**Dev 验收通过待发布**。修复代码提交为 `8e1af259a377f3dde65c8158fd11797363bcb69a`。本报告由 Codex 逐题阅读真实答案、结构化来源、目标状态和 trace；回答模型是 Claude Opus 5.5，未让回答模型自评。没有对生产模型运行评测，也没有把 Git 提交或推送当作生产部署。

## 冻结身份与可复查材料

| 项目 | 精确身份 |
| --- | --- |
| DAQ 代码 | `8e1af259a377f3dde65c8158fd11797363bcb69a`；共同 `src/` tree `e69021117158cbdc7e6d75220c5247fe631bbe58` |
| 相机上游 | `4f453b77073005d24d9f07f36016b7172009443a`；`HEAD:src` 与 DAQ 相同，本轮未改共享 `src/` |
| 本机活动 DAQ 知识 | `a63baf103892250b8474ad9e464b8df47c889e020313e37dd71ad815af9d55a6`；format 2，6 实体、17 规格、0 章节，仅 `internal_fae` 可见这 23 条 |
| Dev 运行配置 | `claude-opus-5-5`、adaptive/high、`submit_only_auto`；回答 prompt SHA256 `c79503adc3ca0455d30066203f4d59bf40a0986217824a9a774393aacd37f7b0`；schema prompt SHA256 `e61fdf537c424336d415ed5f0b2ab83725007d8fbf69b0492231b85f2622f4df` |
| 新冻结 24 行 | `evals/daq_workflow_20261012.jsonl` SHA256 `ad7735f26efb65f1d390d907d38645c7d988bd75aaa57461cdd5452383190916` |
| 旧冻结 24 行 | 原 `final-v3/suite.json` SHA256 `e9fc2ab207df80686ae26c9c40383259550514c22b7976efe79f7a9eebb6158c`；原 `claim-v2/suite.json` SHA256 `84ac88f5b6f3c5709db9c13b3962424805206d015640d7fd1ac471de6d3781bc` |
| 最终 Dev 完整结果 | Git 忽略的私有 `data/knowledge/curated/workflow-dev-20261012/final-all-v9/results.jsonl` SHA256 `ade2c7368e640f51b29ede11971c87e363dbd432657efbee752391c499b5f78d`；同目录有 `identity.json` 和 `traces.jsonl` |
| 两并发探针 | 私有 `data/knowledge/curated/workflow-dev-20261012/concurrency-v9/results.jsonl` SHA256 `a0a754f2604def0b5e5ece85d0367fa2e3262a959b25b85f1f001ce18aa38129` |

Dev runner 为加载**已活动**知识 release，使用其原有绑定标签 `daq-ace8726f2a74`。这个标签不代表新提交已构建或上线。正式发布需要给 `8e1af25` 建立新的构建身份及知识绑定，不能把本次回放的旧标签复制成新镜像身份。原始失败与旧合同 GREEN 见[修复前基线](2026-10-12-daq-workflow-remediation-baseline.md)。

## 架构层修复与旧失败复验

1. DAQ 三个入口共用经模型提出、程序校验的请求 schema；服务、产品、附件、未知可组合，原问和历史用户原话分开保存。服务说明由版本化服务合同给出，产品数值仍必须由角色可见的已发布事实证明。`介绍一下你自己` 现在是服务目标，不再误入产品检索；混合题保留两个目标。
2. 需求账本按 goal ID 跟踪 `satisfied/missing/unknown/conflict`。记录型 23 条 release 的泛检索现能返回 EGO 的两个双目变体实体；未选择变体的规格不能因检索相关性变成已证事实。`EgoViewer` 不再按 `EGO` 子串误识别成双目设备。
3. DAQ 的 guardrail、schema、Loop、clarification、coverage、outcome 与工具事件按真实执行发送。纯上下文陈述由 schema 判定后记录用户条件并直接澄清，不假装运行 Loop；下一轮仍按原条件重新取证，显式换题清掉旧主题。SSE、认证历史和根 trace 使用同一 trace ID 与完整阶段生命周期。
4. 可用工具按已校验目标收窄；组合/流程题不再无谓探测链接、软件和 SDK。HTTP 400、429、5xx 和 schema 错误保留各自终态及 fallback 归因。英文问答各可见字段用英文；安全弃答在前端不显示为普通 `answered · no sources`。

修复前四项 RED 为服务误路由、混合目标丢失、零章节泛检索空命中、Loop 阶段悬空；对应合同在最终 957 项 Python 测试中均 GREEN。后续真实 Dev 失败批次保留：`final-all-v3` SHA256 `814dec0059e737b98cb0a5234f5a347652e3a47a2d208fe403c4c412e0c9cb89`；`final-all-v5` SHA256 `792696796d465932e7854950678acbb452b86460b1ea0a23769d4a84bde7836c`；`final-all-v7` SHA256 `49b29ace9397360b18c0c1f0b7fec288ae639b8cc59e7cb7443c006d668eaab8`，其 P95 74.1 秒、最大 96.8 秒，不予签收。`final-all-v8` 在上下文陈述首轮出现 120.9 秒后中止，19 行结果 SHA256 `adfa2c790c59c0dc55b7510f256c94ec62361d2656073f2b26b93224baf32cb3`；修复后 `context-fastpath-v9` 定向回放证明首轮 4.9–6.5 秒并保留追问条件。最终 `final-all-v9` 为本报告签收批次。

## 最终 Dev 结构、性能与并发

最终 48 行包括新 24 行、旧 24 行；其中 46 个真实模型 turn，1 项直接知识工具操作，3 项故障/权限确定性合同。真实 turn 为 30 个 `resolved`、16 个 `safe_abstained`；预期 outcome 全匹配，所有终止 turn 的 stage 生命周期闭合、SSE 与认证历史阶段相等、trace ID/outcome/来源 ID 相等。无运行错误、无 fallback、无内部来源路径进入答复正文。已满足的产品结论均有结构化来源；纯服务答案零产品来源。Loop 共 108 个 Provider 轮次，单 turn 中位 2.5 轮、P95 4 轮、最大 4 轮；46 个 schema 阶段分别耗时中位 6.1 秒、P95 8.6 秒、最大 9.5 秒。43 个实际 Loop 阶段中位 13.5 秒、P95 32.1 秒、最大 33.0 秒。

最终 `done.tool_calls` 保留逐工具时长：93 次工具调用合计 8.244 秒，单次 P50 2 毫秒、P95 5 毫秒、最大 8.013 秒；最大值是 A01 的图片理解工具。除图片理解外，92 次 DAQ 知识工具合计 231 毫秒。工具耗时包含在上述 Loop 时长中，不能再加到端到端时长。

串行端到端（46 turn）P50 **19.8 秒**、P95 **37.8 秒**、最大 **39.9 秒**（`OLD_multiturn_followup`）；低于任务书的 60 秒 P95、90 秒单题门槛。两个独立请求在同一 Dev 服务中开始时间仅差 0.15 毫秒，运行区间重叠：S01 9.0 秒、P01 12.3 秒，均 `resolved` 且各自阶段、来源、历史与 trace 完整。并发探针未混用会话或证据。

## Codex 逐题复审

每行复审查看原问题和最终完整答案、schema 目标与条件、goal 状态、工具状态、结构化来源、适用变体/测量条件、终态与阶段。表中多轮时间按首轮/末轮列出。`T01/F01/G01` 是确定性合同，标 `PASS` 只表示相应单测通过，不冒充真模型回放。以下 PASS 表示**无严重专业、证据、权限或交付错误**。

| 题号 | 终态 | 秒 | 复审 |
| --- | --- | ---: | --- |
| S01 | resolved | 12.9 | PASS |
| S01a | resolved | 14.1 | PASS |
| S01b | resolved | 13.4 | PASS |
| S02 | resolved | 21.4 | PASS |
| S02a | resolved | 13.2 | PASS |
| S02b | resolved | 15.3 | PASS |
| S03 | resolved | 14.1 | PASS |
| S04 | resolved | 12.3 | PASS |
| M01 | resolved | 21.7 | PASS |
| M01a | resolved | 19.9 | PASS |
| M01b | resolved | 18.4 | PASS |
| M02 | safe_abstained | 35.7 | PASS |
| M02a | safe_abstained | 33.7 | PASS |
| M02b | safe_abstained | 38.4 | PASS |
| P01 | resolved | 15.5 | PASS |
| P02 | safe_abstained | 23.6 | PASS |
| P03 | safe_abstained | 25.1 | PASS |
| K01 | ok，记录型泛检索 | — | PASS |
| C01 | resolved | 8.6/16.1 | PASS |
| C02 | resolved | 6.3/15.3 | PASS |
| A01 | resolved | 28.0 | PASS |
| T01 | 确定性合同 | — | PASS |
| F01 | 确定性合同 | — | PASS |
| G01 | 确定性合同 | — | PASS |
| OLD_ego1600_hfov | resolved | 16.7 | PASS |
| OLD_ego1600_vfov | resolved | 15.4 | PASS |
| OLD_ego1920_hfov | resolved | 17.0 | PASS |
| OLD_ego1920_vfov | resolved | 21.6 | PASS |
| OLD_egopro_rgb_count | resolved | 20.9 | PASS |
| OLD_umi_finger_rgb | safe_abstained | 33.1 | PASS |
| OLD_umi_finger_mono_baseline | resolved | 19.8 | PASS |
| OLD_umi_module_rgb | safe_abstained | 37.9 | PASS |
| OLD_hub_port_count | resolved | 21.7 | PASS |
| OLD_ego_ambiguous_variant | safe_abstained | 27.2 | PASS |
| OLD_ego1600_rgb_gap | resolved | 37.8 | PASS |
| OLD_egdb_depth_gap | safe_abstained | 26.4 | PASS |
| OLD_combo_flow_gap | safe_abstained | 27.6 | PASS |
| OLD_software_link_gap | safe_abstained | 29.9 | PASS |
| OLD_wristcam_fov_conflict | safe_abstained | 24.4 | PASS |
| OLD_multiturn_setup | safe_abstained | 7.6 | PASS |
| OLD_multiturn_followup | safe_abstained | 39.9 | PASS |
| OLD_ego1600_fps | resolved | 22.9 | PASS |
| OLD_ego1920_fps | resolved | 18.8 | PASS |
| OLD_ego1920_resolution_options | resolved | 15.6 | PASS |
| OLD_egopro_fps | resolved | 17.8 | PASS |
| OLD_hub_power_output | resolved | 23.5 | PASS |
| OLD_umi_finger_mono_resolution | resolved | 12.7 | PASS |
| OLD_umi_module_mono_resolution | resolved | 19.1 | PASS |

重点复审：S01 不再搜索产品来解释服务；M01/M02 的服务与产品 goal 独立；P02 与旧 EGO 未选变体题只列当前发布的 1600×1200/1920×1200 值并请求确认；UMI 鱼眼题不把通用 RGB 分辨率当鱼眼结论；EG-DB 整机精度不拿模块值代替；旧软件题无编造版本或 URL，EgoViewer 不再误匹配 EGO；旧组合及跨轮题保持设备数量、连接、Windows 11 和 EgoViewer 条件，明确缺流程；A01 只以附件观察为来源。A01 对“DAQ TEST”中空格的视觉识别保留了谨慎提示；若要精确 OCR 仍需专门识别工具。这是非阻断表述限制。

## 本地门与发布边界

- `DAQ_KNOWLEDGE_RELEASE_ROOT=/tmp/daq-fae-empty-release-test-20261012 .venv/bin/python -m pytest -q tests`：**957 passed**，5 条已知 SWIG deprecation warning；该测试根目录无 `active.json`。活动 manifest 的显式视图合同另在 `tests/test_reviewed_knowledge.py` 中通过。
- `webui/ npm test -- --run`：**240 passed**；`npm run build` 成功。`git diff --check` 与关键 Python 编译检查通过。
- 两仓 `HEAD:src` tree 均为 `e69021117158cbdc7e6d75220c5247fe631bbe58`，本次没有共享运行时代码改动，因此未触发相机 Dev 模型质量门。只读外网探针：`/daq/` 200，DAQ 匿名会话/历史 401，相机首页 200；**没有**向生产回答接口提问。
- 当前活动知识仍只有 23 条实体/规格，软件兼容、组合流程、官方链接、章节与更多产品资料尚未发布；相关问题正确安全弃答，不等于知识覆盖已完整。
- 本次没有新镜像 digest、生产 runtime release 或线上业务验收。正式发布仍须以本提交建立新的 runtime/知识绑定、核对构建身份与回滚点、执行不触发生产模型的只读烟测。上游受保护持久 Git ref 的里程碑 0 证据此前未核实，本报告不把它记为完成。

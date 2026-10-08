# 数采完整运行时迁移：隔离 Dev 回放与复审

**状态**：仅为空知识开发回放，不是完整能力等价或内部试点发布验收。复审人：Codex。回放均在本机 `TestClient` 中使用开发网关与 `claude-opus-5-5`，未向相机生产服务发评测请求。数采知识为 `empty-dev-v0`，共享源码为 `bf1ce9af2d467447e39e2be0caae0bb87b1ba689`，运行标识为 `fae-bf1ce9a-dev`。认证 Platform/Task 在本组用例中关闭；此前独立 Postgres 往返和离线 HTTP 烟测记录仍见同目录原评审。

原始逐轮机器结果保存在本机 `/tmp/daq-real-dev-final-20261008.json`、`/tmp/daq-real-dev-post-planner-20261008.json` 和 `/tmp/daq-real-dev-post-diagnostic-20261008.json`。这些临时文件不是持久发布证据；表中保留完整正文和关键元数据。每题 `sources=[]`，Provider 传输重试数为零。`safe_abstained` 下的确定性整理均明确记录 `fallback_used=true`、`fallback_reason=empty_release_synthesis_template` 和原文哈希；采购报价由模型前边界处理，无回退。

## 首轮：基线提交 `5d01b9f`，7 个场景、8 轮

| 场景 / 问题 | 完整答案 | 计划能力与覆盖 | 终态、耗时、trace | Codex 独立判断 |
| --- | --- | --- | --- | --- |
| 目录：数采设备有哪些产品？ | 目前缺少已审核的设备与型号映射、已审核的数采产品目录，无法可靠确认设备身份、列出设备清单。请提供或确认各项适用的正式资料，核实后逐项回答。 | `catalog, resolve_entity`；均 `empty` | `safe_abstained`；19.8 秒；`f0ff8d09285a45982a1ac846b217d05e` | 空知识安全通过；未编产品。 |
| 规格：EG-DB 的深度精度是多少？ | 目前缺少对应型号与版本的正式规格，无法可靠确认参数。请提供或确认适用版本的正式规格书，核实后再确认参数。 | `lookup_spec`：`empty` | `safe_abstained`；32.3 秒；`e95c66613b721b1d64f989e4656de1af` | 安全通过；未把内嵌模块事实冒充整机精度。 |
| 选型：EGO Pro 加双 WristCam 采集，HUB 和主机怎么选？ | 目前缺少设备组合的连接与同步资料、适用的数采操作文档，无法可靠给出连接结论、给出操作步骤。请提供或确认各项适用的正式资料，核实后逐项回答。 | `inspect_topology, lookup_procedure`：均 `empty`；**漏了 `selection`** | `safe_abstained`；51.3 秒；`1c60349bb253ad9840b51207f5d8e712` | **失败**，层级 `planner / coverage`：“怎么选”未进入选型证据账本，虽然模型实际调用过 `selection` 工具。 |
| SDK：EGO Pro 支持 Python SDK 采集吗？需要哪个版本？ | 目前缺少设备与软件版本的兼容资料、适用的数采操作文档、经审核的数采 SDK 资料，无法可靠确认兼容关系、给出操作步骤、确认 SDK 用法。请提供或确认各项适用的正式资料，核实后逐项回答。 | `lookup_procedure, check_software_support, sdk_evidence`：均 `empty` | `safe_abstained`；19.1 秒；`9a91da00ecb6b80b821f38aeeb06c833` | 空知识安全通过；实际工具未调用 `lookup_procedure`，有知识时的流程能力仍待验。 |
| 排障：EGO 加双 WristCam 录制时频繁丢帧，应该怎么排查？ | 目前缺少适用的数采操作文档，无法可靠给出操作步骤。请提供对应设备与版本的正式操作文档，核实后再给步骤。 | `lookup_procedure`：`empty`；**漏了诊断检索和兼容需求** | `safe_abstained`；23.7 秒；`b3bdf226807fc256c2880b46ce08d48b` | **失败**，层级 `planner / synthesis`：未识别“丢帧/排查”为诊断，且没有询问继续定位所需观察。 |
| 边界：给我 EGO Pro 的采购报价。 | Agent 不掌握价格、库存、MOQ、交期等商务信息,请联系销售或电商代表。 | 无计划能力；无模型调用 | `safe_abstained`；0.0 秒；`ac554316960dd48a534d2ecd0d008933` | 边界通过；模型前拒答正确。 |
| 多轮 1：设备是 EGO Pro、双 WristCam 和 HUB，我要同步录制。 | 目前缺少已审核的设备与型号映射、设备组合的连接与同步资料、适用的数采操作文档，无法可靠确认设备身份、给出连接结论、给出操作步骤。请提供或确认各项适用的正式资料，核实后逐项回答。 | `resolve_entity, inspect_topology, lookup_procedure`：均 `empty` | `safe_abstained`；53.2 秒；`6a641c27c5f9e39ba07f2d09f7df1811` | 安全弃答，但上下文提取把“双 WristCam 和 HUB”合并成一项，层级 `context`。 |
| 多轮 2：那 Viewer 版本和落盘步骤呢？ | 目前缺少已审核的设备与型号映射、设备与软件版本的兼容资料、设备组合的连接与同步资料、适用的数采操作文档，无法可靠确认设备身份、确认兼容关系、给出连接结论、给出操作步骤。请提供或确认各项适用的正式资料，核实后逐项回答。 | `resolve_entity, inspect_topology, lookup_procedure, check_software_support`：均 `empty` | `safe_abstained`；36.9 秒；`4552e3e2947647e7bbca3e89a2c7e1a5` | 继承了上一轮能力，未把用户组合当已核事实；上下文设备分项问题同上。 |

## 根因修复与复测

`82697a3` 在数采 `task_context` 的通用意图/上下文层识别“怎么选/如何选”、诊断症状与排查表达，并拆分中文并列设备；空知识诊断整理现在索取版本、故障现象与时间、完整日志。实施前 5 条规划断言和 1 条设备分项断言均已观察到预期失败；诊断整理的用户可继续操作断言也先失败后通过。无产品专有硬编码。

| 复测 | 完整答案 | 计划能力与覆盖 | 终态、耗时、trace | Codex 独立判断 |
| --- | --- | --- | --- | --- |
| 选型 | 目前缺少已审核的组合选型依据、设备组合的连接与同步资料、适用的数采操作文档，无法可靠推荐设备组合、给出连接结论、给出操作步骤。请提供或确认各项适用的正式资料，核实后逐项回答。 | `selection, inspect_topology, lookup_procedure`：均 `empty` | `safe_abstained`；34.0 秒；`3eec5e743e460b0fdf8b7c9cfeb04f05` | 规划漏项已关闭；仍需真实知识验证推荐质量。 |
| 排障，规划修复后 | 目前缺少设备与软件版本的兼容资料、适用的数采操作文档，无法可靠确认兼容关系、给出操作步骤。请提供或确认各项适用的正式资料，核实后逐项回答。 | `lookup_procedure, search_knowledge, check_software_support`：均 `empty` | `safe_abstained`；42.9 秒；`73d518488c35dca501a370a9cd3ab701` | 规划漏项已关闭；终稿仍欠可继续定位的信息，层级 `synthesis`。 |
| 排障，终稿修复后 | 目前缺少设备与软件版本的兼容资料、适用的数采操作文档，无法可靠确认兼容关系、给出操作步骤。请提供设备和软件版本、故障现象与发生时间、完整日志；确认适用的正式资料后再给排查顺序。 | `lookup_procedure, search_knowledge, check_software_support`：均 `empty` | `safe_abstained`；31.3 秒；`2b674405b4b15df4d0d2a1f8b734a9f6` | 空知识诊断回应可继续推进且没有编造步骤；**该场景**通过。 |

设备并列提取与短追问继承由通用单测验证；该修复没有再次跑真实多轮，因此不能把多轮实测问题写成已实测关闭。最终数采提交 `82697a3` 后的本地静态门为后端 168 项通过、WebUI 235 项通过及构建通过，源码核验比对固定上游 `src` tree 与依赖 blob 通过。独立 CI 定义于后续提交 `1dbeb5c`，但数采仓没有远端，故无远端 CI 成功记录。

## 相机共享上游的同日 Dev 哨兵

在相机隔离工作树的当前上游提交 `bf1ce9af2d467447e39e2be0caae0bb87b1ba689` 上，对上次异常的两道相机题做本机 Dev 重放。使用同一开发 Opus 网关，未调用生产服务。原始结果保存在 `/tmp/camera-daq-shared-dev-sentinels-20261008.json`、`/tmp/camera-daq-shared-dev-catalog-complete-20261008.json`；第二题的未完成局部 trace 留在该工作树的开发 trace 文件。第一次目录请求在 48.6 秒得到 `resolved`，但临时采集脚本仅记录了终态，**不参与答案质量判断**；修正采集后单独重放并取得完整正文。

| 问题 | 结果 | 独立复审 |
| --- | --- | --- |
| 你有哪些相机? | 53.5 秒，`resolved`，`fallback_used=false`，2 条结构化来源，`trace_id=c8392f7c15bc64c6e35403a6abf32a37`；完整答案见原始结果。正文列 21 款双目、21 款结构光、5 款 ToF、3 款相机计算平台，合计 50 款相机；另有 6 款激光雷达和 2 款开发板，总产品条目 58。 | 本次计数自洽、交付质量通过；上一轮“52 与 58 并列”的矛盾未复现，不能抹去原失败。终态的 `planned_capabilities=[]`、`capability_coverage={}`，观测字段仍空，层级 `trace/eval`。 |
| 做服务机器人导航,推荐哪款? | 测试进程等候 360 秒后超时，无 `done` 终态或可复审正文；局部 trace `98b12242d0dd767deacef6944525d641` 只到 schema 提取，确认识别为 `selection`，Loop 终态未写出。 | **未通过**。此前同题为 339.2 秒 `budget_exhausted`；本次无法仅凭局部 trace 区分 Provider 长等待与 Loop 预算路径。层级 `Loop / outcome / trace-eval` 待进一步定位；无终态不能算成功。旧相机目录、选型工具和预算代码相对原生产基线未因数采共享接口修改，故当前证据也不能将此慢路径归因于数采改动。 |

## 尚未满足的发布门

- 空知识只能验明缺证、边界、上下文和终态，不能证明有资料时的规格、选型、SDK、排障和附件回答质量；V-1 的冲突/权限/版本/泛化集与 V-2 的正式真实知识回放仍需完成。
- 相机 FAE 旧域选型同题仍无可交付终态；目录本次答案自洽，但前次矛盾仍保留为失败证据。尚无匹配生产基线与当前集成提交的完整回放，不能宣称旧域零严重退化。
- 上游远端特性分支可取得，但保护设置未核实；数采仓无远端，GitHub CI/镜像回滚未运行；真实 Platform Dev 身份和任务入口未联调，资料归档与权限裁决也未闭合。

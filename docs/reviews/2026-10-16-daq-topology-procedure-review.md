# 数采拓扑与流程逐条来源复审（2026-10-16）

> 2026-10-17 更新：以下为历史候选阶段记录。两条 Pro 单机记录已在本机 Dev 完成真实问答、独立语义复核与 27 条主知识封版；此前“仍未正式 stage”的叙述仅适用于旧快照。见[新版本发布前复核](2026-10-17-daq-knowledge-dev-release.md)，生产是否切换须以实际 `/health` 为准。

**状态：20 条原候选逐条重读，原始决定为 2 条 Pro 单机可进入环回 Dev、17 条需改写、1 条时序冲突；正式新增可答记录仍为 0。** 已形成 6 条记录的私有派生改写和 27 条来源核对 Dev 账本。在共同冻结身份下，两道 Pro 正向题与一题 EGO 分辨率冲突负例均完成真实模型回放和回答模型之外的语义复核；两条 Pro B3 记录是可答的本机 Dev 来源核对候选，仍未进入正式主知识。原 114 节/742 条候选图、当前知识发布均未改动，也未提交、推送或部署。

## 输入与受限复审账本

核对对象是候选目录的 5 条 `topology` 和 15 条 `procedure`。原候选图文件 SHA-256、各记录指纹、91 个原件行范围与重读片段 SHA-256 均写入 `data/knowledge/curated/topology-procedure-review-20261016/source-review.json`，文件 SHA-256 `4859bd71b96f769e59f8b414012a74f6fc0e54cc065dfd53dcc92da56d1df868`。每条决定另记设备/变体范围、缺口和权限门；受限目录 `0700`、文件 `0600`。四份主要原件分别为：双目快采指南 `73a06a99cd63645351d523249f28eb2593334c844789c9c4854d6913274518f9`、Pro 快采指南 `ded0378e7f5560760cd3bfbb833a64536776e9ad59682a98547c28dfecb55c7a`、EGO＋WristCam 组合指南 `1bc4d7e6cb5648414fe2e2a4fad10edb6bf41aed47b37647566643cc8cce5183`、双目详细说明 `6972c60e655fe8b3573d5e6c87ce2c8da03bf74704686650ca27c70b7b32c22d`。所引行号均为原始 UTF-8 Markdown 行号，完整来源路径仅在受限结构化账本中。

候选原件中“EGO”泛称没有把 1600×1200 与 1920×1200 两种变体及硬件修订映射到相同操作范围。存在单机指南，不等于已核对任何指定变体的软件/固件组合。组合指南行 20–24、88–104 明确描述一台双目 EGO、左右各一台 WristCam、一个 HUB、三张 TF 卡及 PC 连接；记录中实体 ID 集合却只能表示一种 `entity:wristcam`，未表达两台实例。组合指南行 56–60 仅写 EgoViewer `2.x.x`、具体版本待实际包确认，固件和端到端同步验收未给出。配对信息同步成功（行 134–142）也不是三路落盘时间精度通过。

## 5 条拓扑

| 记录 | 决定 | 原件定位与原因 |
| --- | --- | --- |
| `topology:ego-standalone` | 需改写 | 双目快采行 67–71 支持单机 TF 卡场景，但 `ego-dual-unspecified` 未映射两个变体；`sync_target` 只能指本机流，不得写成量化同步承诺。 |
| `topology:ego-pro-standalone` | **本机 Dev 来源核对** | Pro 快采行 54–72、76–90 明确一台 Pro、内置电池或条件性外接电源、TF 卡及无 PC 场景；不外推 PC、外部相机或同步精度。 |
| `topology:ego-two-wristcam-hub-pc` | 需改写 | 组合行 84–116 支持接法概况，但记录 `members` 未编码左右两台 WristCam 的实例数；HUB“同步”角色和逐设备存储不能仅凭当前所引行推广为已验组合能力。变体、Viewer/固件版本缺失。 |
| `topology:ego-usb-pc` | 需改写 | 详细说明行 256–402 支持单机 USB PC 文档流程；泛称 EGO 变体及具体 EgoViewer/固件条件未核对，录制位置须依选择而定。 |
| `topology:ego-wifi-pc` | 需改写 | 详细说明行 256–438 支持同 Wi-Fi 与 BLE 配网概况；变体、Viewer/固件及连接后是否自动开流未核对。 |

## 15 条流程

| 记录 | 决定 | 原件定位与原因 |
| --- | --- | --- |
| `procedure:ego-standalone` | 需改写 | 双目快采行 95–113 支持断电插卡、按键与灯语，但泛称 EGO 未映射变体/修订。 |
| `procedure:ego-pro-standalone` | **本机 Dev 来源核对** | Pro 快采行 54–90 完整支持断电插卡、约 2 秒长按开机、短按录制/停止及语音/绿灯检查；仅单机本机 TF 卡。 |
| `procedure:ego-two-wristcam-hub-pc` | 需改写 | 组合行 84–116 的摘要把行 120–164 的“同步配对信息、返回主界面、创建配对组、选 EGO 为主、组开流”压成一步；不能作为完整可执行指导。无版本与三路同步验收。 |
| `procedure:ego-usb-pc` | 需改写 | 详细说明行 256–402 有 PC 连接、SN、参数和保存路径；设备变体及 Viewer/固件组合未核对。 |
| `procedure:ego-wifi-pc` | **时序冲突** | 详细说明行 350–352 要求分辨率、格式、帧率和码率在开流前设置；Wi-Fi 概述行 426–438 又先说连接并出流后配录制参数。候选把开流/配置合并为单一路径，未区分 Color 参数与落盘参数，也未核验连接是否自动出流。 |
| `procedure:b2:86895f410d09e6030eaa6728` | 需改写 | 双目快采行 39–40、67–105 的卡/电源条件与佩戴检查有来源；`variant_unconfirmed` 仍有效，不能套用到两个分辨率版。 |
| `procedure:b2:e94673af7a3531922ad15203` | 需改写 | 双目快采行 109–113 的灯语检查逐行成立；仍缺变体/修订适用确认。 |
| `procedure:b2:7b106331d5088db78ff8038a` | 需改写后再审 | Pro 快采行 47、54–82 支持插卡、内置电池及条件性 Device 外接供电；记录却带无关的 `software_version_unconfirmed=true`，现有核对门会拒绝提升，且第一步没有检查点。不得为过门静默删除标记。 |
| `procedure:b2:0a808fd4cef27c3e2f2a325b` | 需改写后再审 | Pro 快采行 86、88、90 支持三步与灯语；同样携带不适用于离线单机操作的 `software_version_unconfirmed=true`。 |
| `procedure:b2:ae8763328ecf68bca7e22149` | 需改写 | 组合行 104–164 给出连接、蓝牙配对和配对组顺序；记录缺多步检查点、两台腕部设备实例范围，以及具体 Viewer/固件与同步验收。 |
| `procedure:b2:966702f49993e5ae100331c7` | 需改写 | 组合行 9、110–114、158–164 支持先试采和组开流等文档步骤；“画面/数据可见”与“查看结果”不能成为三流同步保存保证。 |
| `procedure:b2:f0d3073b0a198ddf481f200f` | 需改写 | 详细说明行 256–334 与双目快采行 36、39 支持 PC 参考条件、USB 接法、SN/固件查看；泛称变体和精确软件版本仍缺。 |
| `procedure:b2:8b4cc35f4ed1af94e1da16c7` | 需改写 | 详细说明行 336–394 支持开流前 Color 参数、PC/TF 保存与停采检查；变体及 Viewer/固件适配未核。 |
| `procedure:b2:af2b4752590fdafc80a5c546` | 需改写 | 详细说明行 350–352、406–438 已将连接后开流时序标为 `unverified`；留空停流/重配做法，不补写。变体和软件版本也未核。 |
| `procedure:b2:6ade6cfb0ececfaad719e1de` | 需改写 | 详细说明行 336–394、436–438 支持参数与保存步骤，但不能在未确认连接是否自动出流时把它变成确定的先后操作；变体/软件条件仍缺。 |

## 环回 Dev 进展与发布边界

最初的 25 条 `dev-source-checked-pro-standalone.json`（SHA-256 `30cafb8174d19f9a1d22207c29245cdb05b6132fc7ce56a78235e60ec469c027`）及原图（SHA-256 `bcec15073b591e428bec2c83a1c614a42ad16f2279e0da52d7c18ece9f450506`）只作为第一阶段来源核对快照。首轮两道真实 `claude-opus-5-5` 环回 `/chat` 均 HTTP 200、`safe_abstained`，自然长问句未命中记录；代码 SHA 在批内变化。私有完整 SSE/answer/trace 分别存 `real-pro_single_topology.json`、`real-pro_single_procedure.json`，身份存 `real-replay-identity.json`。两道正例独立语义均不通过，因为答案错误声称已核对拓扑或录制流程不存在。

从原 17 条需改写项处理两条 Pro B2 单机准备/录制流程和组合拓扑/概括流程各一条；另为原两条 Pro B3 来源核对记录添加按原件核对的检索别名、任务意图及操作条件。派生图仍为 114 节/742 条记录，736 条结构化记录与原图相同。`rewrite-evidence.json` SHA-256 `07bdb7197de9389c3746012cb38068bf934ebb3bfcb43c5c628613d5a7646a0f` 绑定 6 条前后指纹、18 个原件行定位及另 10 个操作条件行片段哈希；派生 `dependency-bundle.json` SHA-256 `e36cb85b4b3e51a386f8e04e0a8c9003770f1defbfb32228c23c8b482a3b08de`。

| 改写对象 | 当前范围与门 |
| --- | --- |
| 两条 Pro B2 单机准备/录制流程 | 原件行 47、54–90 支持单机步骤；移除与离线操作无关的 Viewer 软件标记，准备段补卡/供电检查点；来源核对仅供环回 Dev。与 B3 原流程语义重叠，不算新的独立能力。固件精确版本与外供数值仍为 unknown。 |
| 两条 Pro B3 单机拓扑/流程 | 原件行 54–90 核对一台 Pro、无需电脑/手机、内置电池或条件性外供、本机 TF 卡，以及断电插卡、约 2 秒开机、短按开始/停止和语音/绿灯检查。`retrieval_aliases`、`task_intents` 与 `evidence_conditions` 均进入来源核对指纹；条件仅在用户明示且具体值、步骤顺序与原件绑定时满足。 |
| 组合拓扑与概括流程各一条 | 仍只是候选：显式编码一台双目 EGO、左/右各一台 WristCam、一个 HUB、三张 TF 卡、三个 Device 口、Host 到 PC；流程补齐蓝牙扫描、选主从、同步配对信息、创建组和组开流。分辨率变体、硬件修订、Viewer 精确版、固件版、组合实测及三路同步精度仍为 unknown，不进入来源核对账本。 |

最新 `dev-source-checked-pro-offline-v2.json` SHA-256 `13fac327b14deb612a4cd1c48eedbe312e092efef3e560db91f3aefb139d9c8a` 绑定上述两条 B3 与两条 B2 Pro 记录、原 23 条来源核对记录，共 27 条；组合两条未入账。加载得到环回候选 ID `candidate-dev-5a440420161ab054a2cb0cb6da94a785221061b7f5ac920f8c73117cbc7638f3`。`dev-load-check-v3.json` SHA-256 `06b3a7d09c03f163ae8c8a463cd74548f165039162af9197c15f5886a55d694d`：两道模型 trace 对应的结构化条件在内部 FAE 各命中一条并满足 requirement；渠道无命中，额外 HUB/WristCam、固件版和分辨率错配均无命中。候选图实体只作额外设备的否决条件，不提供正向事实。

第二轮三道真实模型诊断均 HTTP 200、无 fallback、无 402：Pro 拓扑 `safe_abstained`/coverage `partial`，trace `dde0abf55070336a36150d377d93d35d`，模型虽取到拓扑却把 `host`/`connection_mode`/`aspects`/`task` 记为 unmapped；Pro 流程 `safe_abstained`/coverage `empty`，trace `99432db7727d00be8d68c6b85560bf97`，模型用 `local_recording` 和独立步骤/提示条件未匹配。两道回答独立语义不通过。泛称 EGO 1600×1300@30 与“只支持 1200”冲突负例为 `safe_abstained`/coverage `partial`，trace `78efdcbae735cc4b839e07847081ac6d`；答案保留 1600×1300 构建/固件未知，且不把 1600×1200 变体当完整 SKU 清单，语义初判通过。但该批 DAQ 代码 SHA 从 `c0adc726…` 变为 `d8451e2b…`，只作诊断。完整身份、SSE、done 及 trace 在受限目录的 `real-diagnostic-v2-*` 文件。

后续唯一请求号真模型历史诊断继续暴露三个不同边界：模型曾给出 `usage_context=standalone_no_host` 和布尔 `power/connection/storage`，又把“断电插卡”写成 `power_off_insert_card`，最后一次则在条件正确时把工具实体写成 `EGO Pro 四目`，旧流程工具只接受精确实体名。修复分别要求用户原话核验、受控等义步骤与按候选拓扑成员集合核验自然实体短语；未知步骤和其他设备的配对步骤保持独立缺口，未使用全局 Pro 四步枚举。当时已完成的两题在 DAQ code SHA `c58d1347b70f4a2032dbad827101fc42b7b5088a06e5b053ab27c258e315a649` 下取得新 trace：拓扑 `7620ffa3542d546cc60a189bc71bdbca` 为 HTTP 200、`resolved`/coverage `full`、无 fallback，恰引一台 Pro 单机来源；流程 `a4e45039bbd5f3e26d4d83094bcd9c52` 为 HTTP 200、`safe_abstained`/coverage `empty`、无 fallback，却错误宣称无已核对流程。按失配即停没有消耗第三题。回答模型之外的逐题复审账本 `real-semantic-review-v6.json` SHA-256 `24fad7bb02bf578018338c4dfc7fcf2920b261828ac19c47becdd3534b4d0cd7` 将拓扑判为通过、流程判为失败。流程检索修复后用原模型工具输入在实际 27 条候选图上复算，恰命中 `procedure:ego-pro-standalone` 且 requirement 为 `satisfied`；新的真模型结论见下节。

最终题集 `dev-replay-suite.json` SHA-256 `93a8c73421613c2e7018c96570788605e980ae72771ee1fde5dc7b3019f26cee` 包含两道 Pro 正例及上述冲突负例。候选仍为 `loopback_dev_internal_fae_only`、非认证，未装入活动指针。当前 owner-directed 窄发布器明确排除 `topology` 和 `procedure`；即使最终模型通过，也需单独审查可绑定原件、角色、Dev 批次与运行身份的不可变主知识发布合同，不能把候选加载当作正式发布或组合实测。

## 共同冻结身份的真实 Dev 回放与独立复审

最终批次为本机环回 `/chat`、真实 `claude-opus-5-5`，每题使用独立新 `client_request_id`，回答模型未参与评分。DAQ Python 树 SHA-256 在启动、逐题完成和退出时均为 `2cc8c67690764d97d1833655245b5636f35782cd474505baa412df704eb4b8b8`；`reviewed_knowledge_system.md` SHA-256 `c0bf343a7696078c497e28ad64e8c521919020d5768fa3ad4736287342f69d54`，schema 提示 SHA-256 `8cc305bef9db239859a1b4d9d2c5296deab1dfd848fea9216eab6dcda6785dcd`。健康身份为 `development`、runtime `fae-bf1ce9a-dev`、主知识 `candidate-dev-5a440420161ab054a2cb0cb6da94a785221061b7f5ac920f8c73117cbc7638f3`、27 条来源核对记录、非认证候选模式；这不是已知生产基线，也没有启动已发布主知识活动指针。

| 题目 | 真实结果 | 回答模型之外的语义裁定 |
| --- | --- | --- |
| Pro 单机供电、连接、存储拓扑 | HTTP 200；trace `b833d9d9c1d563b6d539e8397440f051`；`resolved/full`；fallback=false；来源 `topology:ego-pro-standalone` 与实体 | **通过**。只陈述一台 Pro、无电脑/手机主机、内置电池或条件性外供、本机 TF 与本机流；没有组合或固件适配承诺。 |
| Pro 本机 TF 独立录制步骤及灯语 | HTTP 200；trace `454c37a3b3edd017486fd8a6c8566eca`；`resolved/full`；fallback=false；来源 `procedure:ego-pro-standalone` | **通过**。与 Pro 原件第 74–90 行逐步一致：断电插卡，长按约 2 秒 ready/绿灯常亮，短按倒计时/慢闪，再短按保存提示/常亮；未补写 Viewer、固件或组合流程。 |
| 泛称 EGO 1600×1300@30 与“只支持 1600×1200” | HTTP 200；trace `2c1f2e1aeee10cf1a1f81e5d405b40a5`；`safe_abstained/partial`；fallback=false；仅引用已核对的 1600/1920 变体实体及部分 1600 规格 | **通过**。明确现有已核对集合对 1600×1300@30 缺证，不能由已核 1600×1200 变体推出泛称 EGO 排他支持范围；没有把已核变体说成穷尽，固件、硬件修订和构建仍未知。历史截图/固件 ChangeList 未作为本批可答来源，不能把“缺证”写成“不支持”。 |

第一道题的原始 schema 第一次使用未受控 `device_count=1`，受控键门要求重试后第二次规范化成功。负例的两次原始 schema 分别使用未受控 `target_frame_rate=30` 和 `frame_rate=30`；这些键保留为证据缺口，不被提升为已审版本或支持结论。每次原始工具候选、本地解析结果以及完整 answer/SSE/done/trace 分别保存在受限目录的 `real-replay-final-schema-candidates.jsonl`（SHA-256 `8cfffce816871e321e55e30021192767f792770022b530b6c8df189027fb1e9a`）、`real-replay-final-traces.jsonl`（`2de9cfbbddb50a88228bb2180a79d42c2d3d03d1ee1c5ef9b3c7adf68446fe60`）及逐题 `real-final-*.json`。身份文件 SHA-256 `4adbebe0b0eba00d658a700f5edd7927d8bcd96e622c3be12b1647ca2e1f4d04`；独立语义复审文件 `real-semantic-review-v9.json` SHA-256 `3968a306240e5df5a941a136a101b74c55746d8b38fe9c609d61f9790a48d0f6`。

本批前的静态门还发现候选组合检索可因只提及成员子集而误命中；已改成全部必需成员肯定出现、显式否定成员否决，不能用“组合采集”泛称替代具体设备。EGO 旧已发布家族别名的 1600×1200 变体规格回归亦已恢复；泛称输出模式不会自动变成变体 selector。组合记录仍无来源核对与真实回答通过，不在可答新增中。

### 正式主知识发布门

本机 Dev 可答新增为 **2 条 B3 Pro 单机记录**；两条 B2 Pro 改写与其内容重叠，不重复计数。正式已发布新增仍为 **0**。`operator_release.py` 的 `owner_directed_source_checked_v1` 只允许已核 `entity`/`claim`，并显式排除 `topology`/`procedure`；现有活动主知识 23 条是本地 Dev 身份，不等同已知生产基线。要把这两条纳入正式主知识，需单独建立不可变 v2 合同：精确锁定这两个 ID 和原件哈希/行定位、完整 23 条先前记录与上一发布 ID、内部 FAE view/forward 权限、代码/提示/运行时/上游及 Dev 回放身份；发布和启动时重验同一集合，不允许其他 17+1 或组合候选混入。`ReviewedKnowledge` 及正式检索还须实现同一条件/实体/否定门，不能仅将候选记录塞入 manifest。v2 的代码修改会改变本批 DAQ SHA，因此必须重新跑正式装载身份的 Dev 哨兵与独立语义审查；当前只完成设计和候选证据准备，未 stage/activate/publish。

### v2 合同实现进展（本节覆盖上段的早期设计状态）

已按 TDD 加入 `owner_directed_source_checked_v2` 的隔离发布代码与正式加载门。两跳链的原点固定为已知生产 23 条 release `75e2e02d0553c67136ba9255586572d068e34a76c40767abc7258fa0178b37de`。先用 v1 在新 runtime 下重签**同一 23 条逐记录内容**为不可变 `new_base23`，再由 v2 以前者为直接 `previous_release` 仅增 `topology:ego-pro-standalone` 与 `procedure:ego-pro-standalone`。这使 v2 的知识指针可在同一新 runtime 下回滚至 `new_base23`；从新 runtime 回旧 75e2 仍属旧镜像/运行时一同回退的部署操作，不能称为单知识指针回滚。

v2 stage 与启动校验锁定两条新记录的来源核对指纹、Pro 原件 SHA `ded0378e…` 的第 54–90 行、内部 FAE view/forward 权限、原 23 条逐记录内容、归档清单、代码与两份提示 SHA，以及来源核对账本、完整真实 Dev 身份和独立语义复审文件的原始文件哈希。正式 `inspect_topology`、`lookup_procedure`、`selection` 与 `search_knowledge` 对这两条复用候选的设备集合、显式否定、固件/分辨率/主机连接与证据条件门；同时校验用户原问和模型工具参数，防止工具省略 WristCam、HUB 或固件限定后误命中。v1 老发布加载与 23 条旧记录仍受原合同约束。静态夹具演练涵盖 v2 stage、加载、权限、来源及范围负例、模型身份漂移拒绝、同 runtime 回滚；这**不是**真实生产基线的 staged release。真实 `new_base23`、25 条 v2 immutable release、最终代码身份的模型回放及独立复审仍未完成，不可发布。

该 v2 代码把两条指纹、原件行及原生产 ID 写成常量，**仅用于这次 Pro 晋升迁移**；后续高频主知识更新应改用以不可变 manifest 和独立审核输入绑定可变记录的通用发布合同，不能每次靠改 runtime 常量完成。当前 `new_base23` 还需要新 runtime 下的真实 Dev 报告，不能复用 10 月 15 日旧代码报告。最终代码和两份提示冻结后须重跑三题候选真实模型、独立复审，再 stage v2；随后对正式 loader 的 25 条主知识重跑两道 Pro 哨兵与分辨率冲突负例，最后才进入 SDK 19 题和 SDK 发布门。旧 SHA `2cc8…` 的三题仅证明早期候选路径，不满足新 v2 的 replay identity 门。

SDK 发布说明 manifest 绑定主知识 release ID。若它已经绑定 v2，单独将主知识指针回滚到 `new_base23` 会使 SDK loader 拒绝启动；操作门现拒绝这种未协调回滚。须先切换到绑定 `new_base23` 的 SDK 不可变发布，或由部署控制面同时回退/停用 SDK，再切主知识并观测完整服务。知识与 SDK 两个指针没有跨根原子事务，静态夹具的主知识单根回滚演练不代表整套服务回滚验收。

另有 `tests/test_operator_pro_real_inputs.py` 静态哨兵在临时 root 读入真实生产 75e2 manifest、276 原件归档、两条 Pro 精确记录与 27 条指纹账本，构造标明为 **synthetic test only** 的 Dev 报告来检验发布器和正式 loader/检索合同。`EGO Pro 四目` 两道检索分别命中正确 governed 来源，渠道、WristCam/HUB、固件版、USB–PC 与组合意图被否决。这一测试没有真实模型结果，也没有在任何实际发布 root stage 或激活 v2。

受限目录另有两个不可直接 stage 的准备包：`v2-new-base23-proposal-draft.json`（SHA-256 `dcd9783c2d2833ff08f19998075041eba8080af749d0730b87d992a9882c5743`）为 23 条原记录和新 runtime 占位；`v2-pro25-proposal-draft.json`（SHA-256 `b0dfda6ae67d8bb5f63a495959fe3ab6ee7330865c72d6cf43ab56f42c`）为原 23 条加精确两条 Pro、真实来源核对 ledger、`new_base23` 和最终回放占位。两包均与 276 原件 snapshot 一致并通过 `validate_records`（23/23、25/25），目录/文件权限分别为 `0700`/`0600`。占位字段须由最终提交所派生 runtime、真实 Dev 报告、三题真实 replay 和独立复审填充，之后还要通过 stage/正式 loader 验收；本轮没有把草案作为发布记录。

## 验证

原始 20/20 记录与依赖图指纹、91/91 原件定位片段及决策计数（2/17/1）重读通过；派生 6 条记录的 18 个定位和 10 个操作条件片段哈希重读通过，受限目录/文件权限为 `0700`/`0600`。通用同义条件与任务检索按 TDD 红绿推进，覆盖长问句、用户未明示条件、步骤反序/绿灯缺证、额外设备、组合成员否定、固件/分辨率错配、渠道角色隔离。隔离空发布根下产品/旧变体相关 **228 passed**，同一代码身份全量 **1194 passed**，`git diff --check` 通过。Ruff 全文件扫描仍显示既有导入顺序、正则缩写及类型风格告警，未据此称风格门通过。没有对生产运行回放或发布。

上述 1194 是候选阶段旧快照。v2 与 SDK 侧代码共同冻结后的 DAQ Python SHA-256 为 `8e2435d4b91b06e2850af6af67e5a096bc18bbed52cec7cd56dc4c7d8391f54b`，reviewed/schema prompt SHA-256 分别为 `c0bf343a7696078c497e28ad64e8c521919020d5768fa3ad4736287342f69d54` / `8cc305bef9db239859a1b4d9d2c5296deab1dfd848fea9216eab6dcda6785dcd`。同一冻结身份、隔离空发布根的最终全量 **1212 passed**（105.46 秒，5 条既有 Swig 弃用警告）；产品 v2 定向与真实原件静态哨兵 **13 passed**，`git diff --check`、新 operator/test 文件 Ruff 均通过。旧 2cc8 的真模型结果不适用于这个新 SHA；正式可答新增仍为 **0**，待真实新身份回放、两跳 stage 和正式 loader 哨兵。

# 数采真实资料本机 Dev 接入与回放（2026-10-12）

## 范围与身份

按用户当日调整，D1 独立持久复制和 D2 苍渊具名签名不再是本轮本机 Dev 接入的前置任务。本轮没有把候选批量改成正式审核通过。服务仍是独立 `ai-daq-fae-agent`，只在显式 `DAQ_DEV_CANDIDATE_MODE=true`、本机环回、非认证且没有正式活动指针时加载真实资料；默认知识仍是 `empty-dev-v0`。未修改共享 `src/`、相机服务、认证/Task 模式或正式 `active.json`。

原件输入为本项目 Git 忽略的本机归档，清单 SHA-256 为 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`，逐件核对 **276 文件、2,038,212,224 字节**。B3 图 SHA-256 为 `bcec15073b591e428bec2c83a1c614a42ad16f2279e0da52d7c18ece9f450506`。加载器逐文件核对原件、图、章节正文和来源定位；90/114 节只有 `internal_fae` 草案视图，仅供搜索和缺口判断，不满足正向证据门。

精确来源账本 [dev_source_checked_v1.json](../../review_recipes/dev_source_checked_v1.json) SHA-256 为 `5b2402eb9306c56b7443ddffb78f9a11e2b59aec345b33f9ed4beeca50635226`。账本含 **6 个实体、9 个窄范围规格事实**；每条事实有原记录指纹，绑定来源哈希、页码、值、单位、条件和变体。加载后仍以 `source_checked` 标记，只有本机 Dev 的证据策略可据此 `resolved`；正式策略不接受该标记。当前 Dev release 为 `candidate-dev-81c99da169eebbabc21db828cf34c34bad6d774ba0ec5c12606ddbca591e71ee`，健康检查和逐题终态一致。

## 逐页来源核对

| 范围 | 本轮可答事实 | 核对原件 |
| --- | --- | --- |
| EGO 双目 1600×1200 | 水平 165°、垂直 130° | EGO 规格书 V1.0，第 3～4 页 |
| EGO 双目 1920×1200 | 水平 149°、垂直 100° | EGO 1920 分辨率规格书 V1.0，第 3～4 页 |
| EGO Pro | 4 颗 RGB 相机 | EGO Pro 规格书，第 3 页 |
| UMI Finger | 鱼眼 RGB 1600×1200；双目 Mono 基线 50 mm | UMI Finger 规格书 V1.0，第 4 页 |
| UMI 视觉模块 | 鱼眼 RGB 1600×1200 | UMI 视觉模块规格说明，第 4 页 |
| 数采 HUB | 4 个下游端口 | 数采系列 HUB 规格书 V1.1，第 4 页 |

原文把 EGO 1600×1200 写作双目鱼眼相机规格，没有明确将该分辨率标为 RGB，因此**撤回**早期误选的 `rgb_resolution` 正向记录。原文的 USB 2.0 也不说明插头形态，该记录同样不在可答账本。EG-DB 整机深度精度、组合接线/供电、软件兼容和链接继续保持候选或缺证。人工复核曾发现模型把候选组合步骤展开成操作建议，已修订候选提示词并重放，当前不得交付这些步骤。

## 真实模型与独立答案复核

用真实 Dev 网关 `claude-opus-5-5`、本机 `TestClient`、实际应用端点 `/chat` 回放；模型负责作答，Codex 另行对照原件、结构化来源和 trace 复核语义。完整 SSE、终态与 trace 保存在 Git 忽略、`0700/0600` 的 `data/knowledge/curated/dev-source-checked-expanded-20261012/`、`dev-source-checked-expanded-v2-20261012/` 和 `dev-source-checked-final-20261012/`。没有调用生产评测服务，也没有让作答模型给自己评分。

| 题目 | 最终结果 | 耗时 / trace | 独立复核 |
| --- | --- | --- | --- |
| EGO 1920×1200 垂直视场角 | `resolved`，100° | 15.3 s / `b2e60abc90fbe942315e7edc254382c9` | 值、变体和第 3～4 页一致 |
| EGO Pro RGB 相机数量 | `resolved`，4 颗 | 9.7 s / `f504fb9ddefd32d4d3eb7265f4058a8b` | 值和产品一致 |
| UMI Finger 鱼眼 RGB 分辨率 | `resolved`，1600×1200 | 9.6 s / `dfa91c1fdec1d8e94385eddcb351236c` | 与原件“鱼眼 RGB”表格一致；修订提示词后不再把内部字段代码写入正文 |
| 数采 HUB 下游端口数 | `resolved`，4 | 8.6 s / `a24a6e3f42ca192f936d963853cdcac5` | 与原件下游端口栏一致 |
| EG-DB 整机深度精度 | `safe_abstained` | 17.5 s / `b33cca6b8c92bdd05828b6e8af9c7e7e` | 没有用模块参数推定整机精度 |
| EGO 1600×1200 的 RGB 分辨率 | `safe_abstained` | 27.7 s / `e95eb84022d225a543e01d77332648ea` | 型号分辨率没有被冒充为 RGB 事实 |

首轮 HUB 题被计划器误分到 `inspect_topology`，尽管 `lookup_spec` 取到真实来源，证据门仍拒绝并以 `invalid_answer_contract` 终止（trace `f7f59bac4ba6a34f56ab73eb7ddcc6ce`）。据此把“下游端口有几个”识别为规格数量并加回归测试；重放后 `lookup_spec` 覆盖完整且无 fallback。UMI 初轮答复还把结构化字段未写“鱼眼”误说成原件没写；提示词收窄正向回答后，最终答复与原文一致。以上失败与修复均保留私有原始帧，未删改历史结果。

## 高频资料更新和未覆盖范围

新资料要先生成新的归档清单、候选图和 B3 影响报告。每条 `source_checked` 账本指纹只对精确原记录有效：无关图更新可复用未变事实并生成新 Dev release ID；已核对记录的来源、值、范围或条件变化会拒绝加载，复核后才可更新账本。合成测试覆盖了无关图更新复用与受影响记录撤回。更新失败不会改正式活动指针。

当前只是 **9 条事实的窄范围真实知识接入**，其余 727 条图记录和 24 节无内部草案角色章节没有获准正向回答；尚有内容、字段、章节一致性与历史定位差异待逐项复核。认证角色发布、Platform 联调、试点及生产部署都不在本轮结果之内。代码合入和远端推送也不代表上述环境已部署。

验证：全仓测试 `835 passed`；WebUI `235 passed` 且构建成功；新增 Dev 加载器和检索测试的 Ruff 检查、`git diff --check` 通过。共享 `src/` 树与 `upstream-source.json` 的固定上游提交一致；既有 `verify_upstream_snapshot.py` 仍因本仓 `requirements.txt` 比上游多 `PyYAML>=6.0` 而失败，本轮未改动该文件。

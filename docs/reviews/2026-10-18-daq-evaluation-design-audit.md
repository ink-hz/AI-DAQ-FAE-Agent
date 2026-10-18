# 数采 FAE 真实问答评测：相机代码与文档对照

日期：2026-10-18。范围：本机隔离 Dev；主知识 `3c8df43e43d4c2bc585d4e3f9a6120070b552f486b880eee086a2c2e89c215a7`，SDK 知识 `ee6b77ad30fc8f449cfe59f10d823987347a860f8521308784bfc4f3aadf0805`，DAQ 代码 SHA `56940355daa0fd2b3f67b5f1fe74f1317a179b51021ea5024fff7cfb52fe2dde`。这些是本轮回放的冻结身份；代码提交或 Git 推送不等于生产发布。

## 直接对照的相机实现与规则

- 相机仓 `evals/run_eval.py` 将自动硬门与答案质量分开：自动结果有 `hard_gate_passed`，同时保留 `quality_review_required=true` 和 `pending_codex_or_fae`；不能由回答模型自评。
- 相机仓 `docs/AI_FAE_AGENT_ENGINEERING_PRINCIPLES.md` 的评测标准要求 L0 基础事实、单轮核心、多轮咨询、真实失败哨兵和泛化题。`docs/architecture/评测四轴与运行时去补丁设计_20260714.md` 将语义事实、运行可靠性、协议一致性、展示质量分开。`evals/testsets/README_generalization.md` 明确自动词面断言只是预检，最终答案必须独立复审。
- 相机仓 `docs/reviews/2026-10-08-l0-release-codex-review.md` 与同名 JSONL 显示 50 道 L0 的完整问题、答案、能力、来源、fallback、trace、耗时与 Codex 逐题判断；首次自动 34/50 后保留原始失败并重放受影响题。相机另有 30 道工作流核心题、72 道单轮泛化题、12 组多轮题；434 道历史真实问题是候选池，并非每次必跑。
- 相机仓 `AI_FAE_Agent_工作流设计.md` 把失败归因到上下文、schema/planner、能力取证、覆盖、合成、outcome、trace/eval 等层，要求修通用机制，不按题干词面写补丁。

## 数采现状与缺口

数采并非从未做过 Codex 答案复审。旧版 `evals/daq_field_qa_20261014.jsonl` 的 14 题、16 轮曾逐题复审；`evals/daq_field_qa_edge_20261014.jsonl` 另有 5 道反例。当前知识身份下，`docs/reviews/2026-10-17-daq-knowledge-dev-release.md` 绑定的 5 道主知识、19 道 SDK、4 道固件身份 Dev 回放有独立 Codex 报告和结果哈希。旧版 14+5 的代码及知识身份不同，不能直接计作当前发布的广覆盖通过。

当前 `scripts/run_daq_workflow_dev_replay.py` 已走真实 `/chat`、认证会话、隔离 Postgres、模型、SSE 与历史，并保存发布/代码/提示词身份和原始结果。它没有相机 `run_eval.py` 那样的预注册断言汇总或四轴评测报告。HTTP 200、`resolved`、来源数量、自动检查都不能代替逐题语义复审。当前发布最薄弱的是覆盖每条基本规格的 L0、不同范围事实同时出现的题、多轮切换、排障检查点、英文开关与真实失败哨兵。

## 本轮冻结的新题集

| 题集 | 用途 | 规模 |
| --- | --- | ---: |
| `evals/daq_release_l0_20261018.jsonl` | 当前主知识全部 17 条已发布 claim 的型号、数值和测量条件门；也检查总供电和组件边界 | 9 题 |
| `evals/daq_release_generalization_20261018.jsonl` | 服务身份、目录计数、规格缺口、单机/组合流程、排障、硬条件选型、模块/整机、固件身份和两组多轮切题 | 10 组、14 轮 |
| `evals/daq_release_generalization_v2_20261018.jsonl` | 原泛化题集的下轮版本：修正“原生”分辨率及硬条件的预期，并增加组件关系反外推断言；原题集及其冻结结果保留 | 10 组、14 轮 |
| `evals/daq_release_generalization_en_20261018.jsonl` | 浏览器显式选择英文时的规格和缺证回答 | 2 题 |
| `evals/daq_release_boundary_20261018.jsonl` | 商务、跨用户隐私、无关天气三种边界守卫 | 3 题 |
| `evals/daq_release_failure_sentinels_20261018.jsonl` | 本轮真实 Dev 暴露的单机/组合范围、Pro 日志/双目升级漏检，HUB 定性总供电和未证实原生硬条件；绑定原失败 trace，后续修复须重放 | 4 题 |

题目只使用当前受治理发布的事实作正向预期；候选原件和附件不作为答案权威。每题记录 `expected_boundary`、预期终态、关键来源 ID 和禁止的无据断言。预期终态只用于协议观察；若答案事实正确而标签较保守，应在协议轴记录，不能自动判成事实错。多轮题按同一会话顺序运行并逐轮复核。`must_not_assert` 由 Codex 读完整句子和否定语境判断，不能做简单子串禁词。

## 复审与失败处置

1. 在隔离 Dev 运行冻结题集，保存 `identity.json`、`results.jsonl`、`traces.jsonl` 及 SHA-256。不得向生产 `/chat` 发送评测题。
2. 自动检查每轮 HTTP、空答/截断/fallback、SSE 阶段闭合、SSE 与持久化历史的阶段、trace、outcome、来源 ID 一致性；这只是运行硬门和协议轴。
3. Codex 独立核对问题、完整答案、当前发布记录、来源 ID、测量条件、缺证、结论与建议；记录 `pass/partial/fail`、理由、工作流层和原始答案哈希。回答模型不参与评分。
4. 失败保留原始 trace、结果哈希与旧判断；先检查取证条件和证据范围，再改通用机制。若代码、提示词或知识身份改变，受影响题在新身份的 Dev 重新请求，不能把旧答案重算冒充新请求。共享相机运行时若改动，先过相机质量门。

## 本轮真实结果与文档裁决

冻结的中文泛化题集已在隔离 Dev 跑完 10 组、14 轮，L0 已跑完 9 题、9 轮；逐轮原始答案、四轴判断、结果哈希和失败 trace 见 [当前发布 Codex 独立复审](2026-10-18-daq-current-release-codex-review.md) 与同名 JSONL。23 轮 HTTP 与 SSE/历史/trace/source 一致性均通过，但泛化语义为 10 通过、1 部分、3 失败，L0 为 8 通过、1 失败；因此不能以运行硬门代替答案质量门。

英文开关 2 题、边界 3 题及 4 个真实失败哨兵尚未运行。哨兵必须在机制修复及新代码/知识身份绑定后重放，避免把旧身份的失败重跑误写成新版本通过。题集规模参考了相机的层次与复审方法，没有按相机 50+30+72+12 的数量机械复制；数采的 17 条当前 claim、产品/部件关系、流程/组合适用范围和 SDK 固件对象是独立的覆盖单位。当前场景覆盖比旧版完整，但实际质量尚未达到放行标准。

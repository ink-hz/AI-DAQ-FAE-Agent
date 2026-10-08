# 2026-10-08 数采与共享上游隔离 Dev 回放复审

**环境**：两个服务均仅绑定 `127.0.0.1`，使用 Dev 配置和真实 Opus 5.5 网关；未向生产 FAE 发评测请求。数采知识发布为 `empty-dev-v0`，共享上游源码为本地集成提交 `61c7da8`。复审人：Codex；方法：逐条检查完整答案、终态、能力/覆盖、来源与 trace 标识。机器结果保存在本机 `/tmp/daq-real-dev-review-20261008.json` 和 `/tmp/ai-fae-daq-upstream-dev-smoke-20261008/`，这些临时文件不是正式发布产物。

## 数采真实 Dev

| 题目 | 终态 / 回退 | trace_id | 独立复审 |
| --- | --- | --- | --- |
| EG-DB 深度精度 | `safe_abstained` / false | `e924bd71a3d26dd7c7037a411a200df8` | 事实安全；答案暴露过多工具细节，交付质量未过，层级 `synthesis` |
| EGO + 双 WristCam + HUB 的接线、同步和落盘 | `safe_abstained` / false | `a6b699b8d1e507f1392f8848b88a919a` | 多能力计划和空证据状态正确；正文偏向工具清单，交付质量未过，层级 `synthesis` |
| 续问 Viewer 版本排查 | `safe_abstained` / false | `500219ad6c3ea512ac422915030e2051` | 继承上一题的拓扑/流程能力，未把用户条件当事实；正文冗长，层级 `synthesis` |
| 采购报价 | `safe_abstained` / false | `502623b8c75bf50c8490bf39f910af23` | **失败**：把商务红线当作知识缺失，还追问报价配置，层级 `guardrail`。已增加模型前红线预检和合同测试；需在新进程复跑后才可关闭此项 |

后续新进程复跑“采购报价”得到模型前红线拒答，`trace_id=dfb6ff9ad575bfad062838bc591a8a18`，该 guardrail 项已修复。再次复跑 EG-DB 时网关耗时 476.6 秒后抛出 `AnthropicTransportError`，数采终态为 `internal_error` 且 `fallback_used=true`；层级 `channel / outcome / trace-eval`，真实 Provider 可靠性门仍未通过。代码现已将缓冲流的 HTTP 状态、协议错误与传输原因分开归因，并为认证长调用增加请求租约续期；**尚未再次用真实网关证明修复后的行为**。

### 完整迁移批次补充 Dev 回放

共享上游集成版本 `f82c89b8e2906b4cde0a3ec9619290fbf5ea6540` 下，在独立数采 Dev 应用上使用同一真实 Opus 5.5 网关，单次 Provider 等待上限 30 秒、单题总上限 100 秒；没有向相机生产服务发评测请求。原始机器结果保存在本机 `/tmp/daq-real-dev-parity-review-v3-20261008.json` 与 `/tmp/daq-real-dev-parity-review-spec-20261008.json`。这两题是补充烟测，不代替全量泛化集；后续代码 pin 已前进到 `bf1ce9af2d467447e39e2be0caae0bb87b1ba689`，尚未在新 pin 下重跑真实网关。

| 题目 | 耗时 / 终态 | trace_id | Codex 独立复审 |
| --- | --- | --- | --- |
| 数采设备目录 | 17.2 秒 / `safe_abstained` | `8d1c96c557e88f73983ada36fbe3eb0c` | 产品清单未编造，目录与实体需求均标缺证，正文直接说明缺少正式目录和核查入口，**空知识弃答通过**。确定性整理明确标记 `fallback_used=true`、`fallback_reason=empty_release_synthesis_template` |
| EG-DB 深度精度 | 25.5 秒 / `safe_abstained` | `0dc5ed0f87396f9d716dbac9ed174890` | 无精度数字或伪来源，规格需求标缺证；正文曾重复询问已给出的型号，层级 `synthesis`。随后将空知识规格模板改为请求适用版本的正式规格书，并用合同测试验证；该措辞修正尚未再次真实网关回放 |

同一目录问题在确定性整理前的两次真实回放分别耗时 22.6 秒和 25.0 秒，均安全弃答，但正文暴露内部检索过程、重复建议；这促成数采侧仅对“空知识、全部需求明确缺证、无来源、无附件需求”的安全弃答做可追踪整理。它不会覆盖系统错误、已满足证据、冲突或将来的有据终稿。早前 476.6 秒传输失败没有再现于这两题，但尚不能据此判定 Provider 可靠性门通过。

后续合同审查又发现：多能力问题的整理曾只呈现首个缺证结论，Platform Task 通道曾未调用同一整理逻辑。两处均已以先失败、后通过的合同测试修复；任务的 `text_delta`、`done` 和会话历史现共享整理后的答案，且保留可见回退原因。以上两处尚未做真实网关的多能力/任务回放，不能据此宣称通道质量验收完成。

前三题均无治理来源，`sources=[]`，不能据此判断有知识时的事实回答质量。它们证明了空知识的安全弃答和多轮能力传递，不证明完整迁移。

## 相机 FAE 共享上游真实 Dev 烟测

`evals/run_eval.py --collection smoke --mode composition --base-url http://127.0.0.1:18001 --concurrency 1` 跑完 5 题；自动结果 4 项硬通过、1 项硬失败。Codex 按问题/正文另作复审：

| 题目 | 终态 / 回退 | trace_id | 独立复审 |
| --- | --- | --- | --- |
| Gemini 335L 价格 | `safe_abstained` / false | `714b3a7275554ece2458b66c35aa9642` | 边界正确，短拒答可交付 |
| 相机目录 | `resolved` / false | `e080cdd2297d16a4622a634dcac0a6c4` | **失败**：正文声称“52 个家族内条目”，又写出相加为 58 的列表，统计口径自相矛盾；层级 `synthesis / outcome`。自动硬门未检出 |
| Gemini 335L 工作距离 | `resolved` / false | `4d714d99386debd63e50960e7dfcac6f` | 区分理想量程与最大边界，来源与结论一致；本轮复审通过 |
| 服务机器人导航选型 | `budget_exhausted` / true | `41cd8b1e547c8ef0f6c55d1f50bd85b3` | **硬失败**：虽有完整候选正文，终态明确预算耗尽且耗时 339.2 秒，不能算可交付；层级 `outcome / trace-eval`。需核查基线与本次变更是否相关 |
| Gemini 335L 多机同步丢帧 | `resolved` / false | `9c4f1c406b8e88125d5d2608b5f5d63d` | 排障分链条、给验证步骤，结构有用；细项硬件数值仍需领域 FAE 复核 |

本次 5 题不是完整旧域回归集；共享上游的正式发布门尚未通过。答复质量判断由 Codex 做出，没有使用 GLM 自评。下一轮须重放相机目录与选型哨兵、完成多轮及泛化集，并区分基线已有波动和本次代码退化。

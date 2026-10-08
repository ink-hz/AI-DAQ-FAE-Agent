# 2026-10-08 数采与共享上游隔离 Dev 回放复审

**环境**：两个服务均仅绑定 `127.0.0.1`，使用 Dev 配置和真实 Opus 5.5 网关；未向生产 FAE 发评测请求。数采知识发布为 `empty-dev-v0`，共享上游源码为本地集成提交 `61c7da8`。复审人：Codex；方法：逐条检查完整答案、终态、能力/覆盖、来源与 trace 标识。机器结果保存在本机 `/tmp/daq-real-dev-review-20261008.json` 和 `/tmp/ai-fae-daq-upstream-dev-smoke-20261008/`，这些临时文件不是正式发布产物。

## 数采真实 Dev

| 题目 | 终态 / 回退 | trace_id | 独立复审 |
| --- | --- | --- | --- |
| EG-DB 深度精度 | `safe_abstained` / false | `e924bd71a3d26dd7c7037a411a200df8` | 事实安全；答案暴露过多工具细节，交付质量未过，层级 `synthesis` |
| EGO + 双 WristCam + HUB 的接线、同步和落盘 | `safe_abstained` / false | `a6b699b8d1e507f1392f8848b88a919a` | 多能力计划和空证据状态正确；正文偏向工具清单，交付质量未过，层级 `synthesis` |
| 续问 Viewer 版本排查 | `safe_abstained` / false | `500219ad6c3ea512ac422915030e2051` | 继承上一题的拓扑/流程能力，未把用户条件当事实；正文冗长，层级 `synthesis` |
| 采购报价 | `safe_abstained` / false | `502623b8c75bf50c8490bf39f910af23` | **失败**：把商务红线当作知识缺失，还追问报价配置，层级 `guardrail`。已增加模型前红线预检和合同测试；需在新进程复跑后才可关闭此项 |

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

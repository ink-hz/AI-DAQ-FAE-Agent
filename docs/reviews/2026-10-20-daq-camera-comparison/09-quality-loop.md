# A9：反馈、题集、复审与发布门

## 1. 两侧机制对照

相机仓有 `src/api/routes.py:622` 反馈入口、`src/storage/postgres_data_flywheel.py` 的 turn/trace 关联、`src/storage/review_center.py` 复审与候选、`deploy/scripts/create_turn_review.sh` 和 `evals/run_eval.py`；[A2](02-camera-baseline.md)记录一批带版本的 Dev 反馈回放：20 题的独立语义复审为 19 pass / 1 partial。它证明相机有“反馈→原题/泛化题→独立质量复审”的可追路径，但不代表当前相机生产题库全通过。

DAQ 并非完全没有闭环：`daq_fae/app.py:363-394` 收 feedback，`daq_fae/authenticated_persistence.py:275-313` 按 owner/session/turn/trace 解析并写入，Review Center 的读写有审核员权限；`daq_fae/feedback_analytics.py` 提供 DAQ schema 限定的只读聚合。`docs/reviews/2026-10-19-daq-complex-evaluation.md` 记录多次独立四轴复审，`tests/test_daq_feedback_analytics.py` 等有合同测试。尚未取得线上真实 bad-feedback 清单、复审决议到 eval candidate 的运行台账，不能宣称这条链已为**当前用户反馈**闭环。

## 2. 症状如何穿过质量门

| 失败族 | 首次可见/修复线索 | 已有门 | 最终发布身份是否完整覆盖 |
| --- | --- | --- | --- |
| `CASE-001` schema 错误、17 字 | 10-18 旧 Dev 独立复审 | schema/聚合功率相关单测；旧版直接错误终态 | 当前主知识的 HUB 值可检出，但同题最终真实答案/trace 未取；`schema_error` 应是发布阻断 |
| `CASE-002/003` 单机流程漏交付 | 10-18、10-19 历史 Dev；指标别名与已知部分规则后修 | 流程别名及需求匹配测试，聚焦 Dev | 10-20 发布时 3 个主知识候选题复审包含单机流程；完整复合目标和 13 组多轮未在最终身份整套完成 |
| `CASE-004` 两版 FOV 漏四值 | 10-19 `core-final-30`；`cc03-retest-31` 旧身份给出四值 | 变体字段/来源身份正反测试 | 最终代码 + 10-20 主知识下 55 核心题未整套跑，不能把旧 retest 当现行门通过 |
| `CASE-005` 追问 SDK 版本误报缺失 | 10-19 `multiturn-final-101` 35 pass / 3 fail；后续附件重绑聚焦复审 | 历史两轮 targeted 通过 | 最终身份 13 组只完成 9 组，已完成部分仍未全量独立语义复审 |
| `CASE-006` WARN 被推成失败 | 10-19 `multiturn-final-133` 37 pass / 1 fail；最终代码 `ba50906…` 聚焦 `cm13-warning-scope-137` 两轮复审 2/2 pass | 请求级附件终态门、正反测试 | **聚焦修复有最终代码身份的 Dev 证据**，但主知识仍是 `3c8…`，非 10-20 的 `19d…`；完整多轮仍未跑完 |

10-20 发布记录写静态回归 `1618 passed, 5 warnings`，正式主知识三题候选、代码+主知识+SDK 五题真实 Dev 复审及局部 CM13 修复。静态单测、HTTP 200、无 fallback、来源数和答案字数都是必要诊断，**不能代替**“原问目标和已知部分全部交付、未证内容没有越界”的专业判定。`CASE-003/004` 有来源仍弃答，`CASE-006` 答案长仍越界，正好是这种风险。

## 3. 后续发布门建议（调查输入，尚未实施）

1. 将六个旧失败族转成稳定的 `CASE`，每个有同实体同条件正例、错实体/错变体/错版本负例与多轮追问；测试期先看完整答案及来源，再看自动结构指标。
2. 发布身份冻结后运行核心 55 题和 13 组多轮的完整清单；单独统计最终身份的覆盖分母、结构失败、专业 pass/partial/fail 与明确未执行，不把旧代码局部通过迁移为全量通过。
3. 对 schema/provider 错误、无据 `resolved`、权限越界和用户附件推成官方结论设阻断；对已审部分漏交付设专业复审阻断。`safe_abstained` 只有在边界与已知部分都正确时才算质量通过。
4. 每条新生产差评按 `turn_id/trace_id` 回到运行身份、目标账本、工具来源、终稿及 UI，复审后才进入泛化题/知识任务；没有完整身份的反馈保持待补证，不用平均分盖住。

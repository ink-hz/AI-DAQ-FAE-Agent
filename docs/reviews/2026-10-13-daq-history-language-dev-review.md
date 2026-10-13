# 数采 FAE 历史与语言开关修复：Dev 复审

日期：2026-10-13。修复代码 `84253d2`；仅修改数采适配层、提示词、WebUI 和测试，`src/` 共享运行时未改。相机 FAE 的语言方案以相机仓 `feat/response-language-20260907` 的前端选择与 `response_language` 请求字段为对照；数采采用 DAQ 专有适配，不修改相机服务。

## 故障与修复

- 线上已认证的 `/authenticated/conversations?limit=30` 返回 503。生产库两个会话中，一条旧会话上下文携带 `task_context.version=1`，新版严格验证器将其规范化为 v3 后又要求字节语义完全相等，导致 `daq_knowledge_context_unavailable`，整页列表失败。新会话可读取。修复只接受已知 v1/v2 字段且要求所有原字段原值保留；未知字段和失去用户来源的字段仍拒绝。旧会话的知识 release 与当前 release 不同，现行角色和版本门仍会隐藏旧会话，不能把这当作旧会话已可继续。
- 数采原本由模型推断答复语言，WebUI 没有语言选择。现在用户可选中文或 English，选择存于浏览器并作为每轮 `response_language` 传给后端；已认证持久化终态保存 `answer_language`，恢复会话可还原该轮答复语言。输入语言与界面选择不一致时，以用户选择为准。错误终态保留结构化 outcome 与 fallback，不把 Provider 400 改写为 503。
- `guardrail/schema/loop/coverage/outcome` 原样暴露在思考面板。现在前端按真实阶段事件显示可读标题与说明，没有伪造未执行的阶段。

## 测试与独立答案复审

隔离 Python 全量 **962 passed**；WebUI **244 passed**；TypeScript/Vite 构建通过。旧 v1 加密上下文通过本地 PostgreSQL 读取；非法附加字段仍被拒绝。测试了语言选择穿过前端请求、后端 schema、答复合同、SSE 和持久化恢复路径。

真实模型仅在隔离 Dev 运行，使用原已发布的 23 条事实、角色绑定和相同上游 pin。Dev 回放结果位于 Git 忽略目录 `data/knowledge/curated/language-fix-20261013/`：English 选择的 `S01/P01` 结果 SHA-256 `28329eceab5a845322d60ec0134eda3fa586d58efb07f7ad2d91cfa10722747f`；中文选择的 `S03` 结果 SHA-256 `9270f691225328a9c36040c7a6fd1c9a8f2cd8bfa32a1684eba10216a8b16aac`。三题均为 `resolved`、无 fallback，SSE 阶段生命周期、历史、trace、来源均一致。

Codex 逐题独立阅读完整答案及结构化证据：

| 题号 | 输入与选择 | 复审 |
| --- | --- | --- |
| S01 | “介绍一下你自己” + English | 英文服务身份和职责，零产品来源；无资料或权限越界。正文略长，但无严重语义错误。PASS |
| P01 | 中文 EGO 1600×1200 水平视场角 + English | 英文答 165°，明示分辨率条件与规格书页码；两条结构化来源，未推广至其他变体。PASS |
| S03 | 英文服务问题 + 中文 | 中文答服务身份和现阶段覆盖；未把未发布资料说成产品不支持。PASS |

这三题只覆盖本轮语言行为；前次 48 行完整工作流 Dev 复审仍见 `2026-10-12-daq-workflow-remediation-dev-review.md`。未对生产回答接口发送评测问题。生产登录后的历史列表须在新镜像启动后再以只读方式核对，不能用匿名 401 替代。

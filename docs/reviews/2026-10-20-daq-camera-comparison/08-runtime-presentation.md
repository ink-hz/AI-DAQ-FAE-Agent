# A8：运行身份、错误与展示链

## 1. 同一历史请求的字段链

`CASE-003/004/005/006` 各自的 `results.jsonl` 保存 `done.answer`、SSE、历史回查及 `integrity`。四案均 `stage_lifecycle_closed`、`stream_history_stage_equal`、`trace_id_equal`、`outcome_equal`、`source_ids_equal`。即这四次隔离 Dev 的漏答或错误断言已经存在于服务端终稿，**不是该次 SSE 或历史读回删掉字**；没有浏览器画面，因此不外推 UI 的所有情况。

| 旧运行 | 模型与网关回显 | 工具/传输 | 服务端结果 |
| --- | --- | --- | --- |
| `CASE-003` `daq-dev-759484d…` | 配置 `claude-opus-5-5`，实际回显 `anthropic/claude-opus-5.5`，6/6 回合一致 | `anthropic_sse_buffered`，Provider retry 0，协议错误 0，工具 4/24 | `safe_abstained`，账本 `unknown` |
| `CASE-004` `daq-dev-4e9bbad…` | 同类配置与实际回显完整 | Provider retry 0，协议错误 0，工具 13/24 | `safe_abstained`，已检出四值却漏写 |
| `CASE-005` `daq-dev-94f2389…` | 配置与实际均 `claude-opus-5-5`，回显完整 | Provider retry 0，协议错误 0，工具 4/24 | 追问错称 SDK 版本缺失 |
| `CASE-006` `daq-dev-aa94db6…` | 配置与实际均 `claude-opus-5-5`，回显完整 | Provider retry 0，协议错误 0，工具 4/24 | 把 WARN 推为请求失败 |

这组证据排除上述**旧请求**的“模型没响应、网关截断、预算耗尽、SSE/历史丢字”作为首因。不能由此证明模型选择、提示长度或网关配置对全局质量没有影响，更不能把本机 `app.py` 默认值当作当前生产配置。`CASE-001` 是明确 `schema_error`，它属于请求理解错误终态；`CASE-002` 是业务 `safe_abstained` 而非 Provider 错误。

## 2. 当前发布与生产可见性

2026-10-20 发布记录冻结 DAQ 代码 `ba50906…`、runtime `daq-ba50906bd98b`、主知识 `19d1a31…`、SDK `2bfd0538…`、镜像 digest；相机镜像当次未变。但本次只读访问只能看到两侧公开入口 HTTP 200，无法读内部 `/health`、现运行模型回显、提示哈希和知识指针（[A0](00-identity.md)）。因此 L10 对**历史发布记录**可审，对**当前生产实际身份**标 `version_unknown`。如果现在有一个线上短答案，必须先取得其 `client_request_id/trace_id`、后端 done 与运行身份，再与 UI 对照；仅靠页面可达或 200 响应不能归因。

`daq_fae/authenticated_chat.py:187-300` 把 schema transport 失败、Provider 400/401/403/404/422 配置拒绝、429 限流、5xx 不可用和附件/权限错误分开；`LoopRuntime` 输出 `provider_transport`、`provider_model_echo`、预算、tool_calls、coverage、outcome。应按错误码分桶，不能把系统故障改写成“知识没有”。发布质量门也应区分 `safe_abstained`（可能是正确的边界）和 `schema_error/provider_*`（系统失败）。

## 3. 最小后续核查

当可读的线上请求出现时，固定同一 request，依次比对 `submit_answer` 结构化字段、`done.answer`、SSE `text_delta`、持久 turn、UI 显示及 sources/outcome；每段保留哈希与时间。若只有模型/网关身份仍不确定，先做只读配置回显核查；只有无法由已有 trace 区分的假设，才在隔离 Dev 固定其他变量做最小对照。

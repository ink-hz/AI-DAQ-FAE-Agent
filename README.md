# AI DAQ FAE Agent

数采 FAE 是与相机 FAE 同级的独立项目，首期面向内部 FAE 和技术支持。它复用受版本约束的通用运行时，采用自己的 Agent 身份、会话、附件目录和数采知识发布。

## 当前状态

`feat/full-daq-fae-parity` 已有可运行的**本机 Dev 实例**。当前知识发布 `empty-dev-v0` 没有已审核数采事实，因此产品参数、兼容性、操作步骤和下载链接应明确缺证。当前共享 `src/` 是 [upstream-source.json](upstream-source.json) 记录的本地集成快照；它还没有受保护的持久 Git ref，不是正式依赖 pin。

| 能力 | 当前接入情况 |
| --- | --- |
| Provider/Loop/终稿协议、SSE/心跳、trace、错误归因 | 已在数采 API 实际装配；Opus 5.5 使用 `submit_only_auto` |
| 数采多能力计划、任务上下文、需求账本和证据门 | 已装配；空知识或未核验来源不能交付 `resolved` |
| 会话续接、请求去重、反馈 | 本机 Dev 使用独立 SQLite 持久化；重启续聊和完成请求重放已测试 |
| 附件上传、会话内检索/精读、图片分析入口 | 已装配；资料只算本轮用户证据；视觉 Provider 尚未配置时明确失败 |
| WebUI | 已迁入数采仓，可在本机 `/app/` 使用；`/daq/` 内部 Platform 入口尚未接通 |
| Platform 身份、Postgres 会话/反馈、review、HTTP Task | 共享组件与 DAQ 身份装配已有代码；尚未接入当前服务和完成跨端验收 |
| 数采事实、关系、权限和知识发布 | 空知识；待资料归档、裁决、可见性与发布清单 |

**不能把当前 Dev 启动和通过单元测试称为完整能力等价或试点可发布。** [迁移计划](docs/superpowers/plans/2026-10-08-full-runtime-parity-migration.md)与[完整设计](docs/2026-10-08-数采FAE完整设计.md)列出剩余合同和发布门。

## 本机启动

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
cd webui && npm ci && npm run build && cd ..
.venv/bin/uvicorn daq_fae.app:app --env-file .env --host 127.0.0.1 --port 8081
```

`.env.example` 默认 `offline`，只做不调用模型的 Loop/工具合同烟测。本机服务拒绝非 loopback 请求；SQLite 和附件文件位于 `data/`，相机 FAE 的资料与数据库不进入本应用。

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsSN -H 'content-type: application/json' \
  -d '{"message":"EG-DB 的深度精度是多少？"}' \
  http://127.0.0.1:8081/chat
```

浏览器可打开 `http://127.0.0.1:8081/app/`。`/chat` 返回具名 `session`、`stage`、`text_delta`、`sources`、`done` 事件；正常空知识终态是 `safe_abstained`，并附需求状态、能力覆盖、trace 与 turn ID。Provider HTTP 400 与 503 分别归为配置错误和上游不可用。

若要在**开发环境**调用真实模型，设置 `DAQ_PROVIDER_MODE=anthropic`、`DAQ_ANTHROPIC_AUTH_TOKEN` 或 `DAQ_ANTHROPIC_API_KEY`、`DAQ_ANTHROPIC_BASE_URL` 和 `DAQ_ANTHROPIC_MODEL`。不要提交凭据。当前默认模型为 Opus 5.5 adaptive/high；不得对它发送 forced `tool_choice`。

验证命令：

```bash
.venv/bin/python -m pytest -q tests
cd webui && npm test && npm run build
```

正式依赖 pin 之前还需完成旧 FAE Dev 回归、双服务合同、上游受保护 ref、数采真实 Dev 回放与独立答案复审。数采资料和用户附件不能自动进入知识库；发布仍受来源、事实裁决和角色权限约束。

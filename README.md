# AI DAQ FAE Agent

数采（数据采集）产品的独立 FAE Agent 工作目录。首期服务内部 FAE / 技术支持，复用现有 AI FAE 的通用运行能力，但独立发布数采知识和服务版本。

当前有一个**本地 Dev 空知识启动版**：复制已核对生产构建身份的 FAE `a6234f6` 源码快照，新增数采 API 装配。`knowledge/` 为空，不会载入相机资料；任何确定性答案都须等数采证据工具和知识发布完成后才能交付。快照来源见 [upstream-source.json](upstream-source.json)。它是可运行起点，不代表完整设计的里程碑 0 已通过。

## 本地启动

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn daq_fae.app:app --env-file .env --host 127.0.0.1 --port 8081
```

`.env.example` 默认 `offline`：它明确使用本地烟测适配器走完整 Loop、空知识工具和 `submit_answer`，不调用模型。另开终端验证：

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsSN -H 'content-type: application/json' \
  -d '{"question":"EG-DB 的深度精度是多少？"}' \
  http://127.0.0.1:8081/chat
```

`/chat` 返回 SSE `tool_call`、`text_delta`、`done`。空知识的正常终态是 `safe_abstained`，`capability_coverage.search_knowledge=missing`，`sources=[]`，并带 `trace_id`。如果 Provider 提交无证据 `resolved`，服务阻断交付并记录 `invalid_answer_contract`、`fallback_used` 和原因。Provider HTTP 400 与 503 分别归为配置错误和上游不可用。

需要对**开发网关**做真实模型烟测时，在 `.env` 中设 `DAQ_PROVIDER_MODE=anthropic`、`DAQ_ANTHROPIC_AUTH_TOKEN` 或 `DAQ_ANTHROPIC_API_KEY`、`DAQ_ANTHROPIC_BASE_URL`、`DAQ_ANTHROPIC_MODEL`。Opus 5.5 固定采用 adaptive/high 与 `submit_only_auto`，避免 forced `tool_choice`。不要把凭据提交到仓库。

测试：

```bash
.venv/bin/python -m pytest tests/test_empty_bootstrap.py -q
```

这个启动版只提供本地 `/health` 和 `/chat`。Platform 身份、附件、反馈、数采实体/拓扑/版本证据、治理后的知识发布和部署，仍按[完整设计](docs/2026-10-08-数采FAE完整设计.md)及[开发任务书](docs/superpowers/plans/2026-10-08-data-acquisition-fae-development-task-brief.md)实施。上游源码快照在正式里程碑 0 前须换成受保护持久 Git ref 的固定依赖，不能长期双仓复制维护。

现有 FAE 的实现基线、数采资料盘点、需求基线与 A/B 决策保存在相邻的 `AI-FAE-Agent` 仓库；本目录的设计记录后续实施所需的具体接口、数据、验收和发布边界。

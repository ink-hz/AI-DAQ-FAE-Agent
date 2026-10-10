# AI DAQ FAE Agent

数采 FAE 是与相机 FAE 同级的独立项目，首期面向内部 FAE 和技术支持。它复用受版本约束的通用运行时，采用自己的 Agent 身份、会话、附件目录和数采知识发布。

## 当前状态

`master` 已推送到 `origin/master`，包含可运行的**本机 Dev 实例**；当前提交与远端状态请以 Git 查询结果为准。当前知识发布 `empty-dev-v0` 没有已审核数采事实，因此产品参数、兼容性、操作步骤和下载链接应明确缺证。当前共享 `src/` 是 [upstream-source.json](upstream-source.json) 记录的集成快照；上游集成分支已推送远端，但尚未核实受保护的持久 Git ref，因此不是正式依赖 pin。

| 能力 | 当前接入情况 |
| --- | --- |
| Provider/Loop/终稿协议、SSE/心跳、trace、错误归因 | 已在数采 API 实际装配；Opus 5.5 使用 `submit_only_auto` |
| 数采多能力计划、任务上下文、需求账本和证据门 | 已装配目录、选型、规格、流程、SDK、经验与风险等入口；空知识或未核验来源不能交付 `resolved`；全部缺证的安全弃答使用可追踪的确定性整理，`fallback_used=true` |
| 会话续接、请求去重、反馈 | 本机 Dev 用独立 SQLite；内部认证模式用独立 Postgres、所有者会话与请求账本，包含长调用续租和回答/终态同事务提交 |
| 附件上传、会话内检索/精读、图片分析、归档 | 已接入；认证模式可启用独立归档清单与删除确认，关闭新归档后仍处理既有删除，真实 Postgres 往返及删除与归档确认竞态测试通过；视觉 Provider 尚未配置时明确失败 |
| WebUI | 本机 `/app/` 与内部认证模式 `/daq/` 分别装配，沿用旧客户端合同 |
| Platform 身份、Postgres 会话/反馈、review、HTTP Task | 已装配可选内部认证模式；数采 Agent ID、任务签名 audience、会话和资料隔离；真实 Platform 联调未完成 |
| 数采事实、关系、权限和知识发布 | 在线仍为空知识；原件清单、候选章节/记录、增量影响图、角色过滤和受审核的不可变 Dev 发布/回滚门已有离线工程实现。真实章节、事实、权限、链接均未签认，未激活真实知识 |

**尚不能宣布完整能力等价或试点可发布。** 共享源码虽已推送远端集成分支，受保护依赖 pin 尚未核实；真实 Opus 网关有一次 476 秒传输失败，相机 Dev 回归有一个硬失败和一个答案矛盾；数采真实知识、Platform 实例联调和部署回滚仍未验收。原始数采资料已有本机受限归档候选和逐文件哈希，但持久存储与资料裁决尚未验收，不进入当前知识发布。[接管门禁核查](docs/reviews/2026-10-08-daq-takeover-gate-audit.md)、[迁移计划](docs/superpowers/plans/2026-10-08-full-runtime-parity-migration.md)与[完整设计](docs/2026-10-08-数采FAE完整设计.md)记录发布门。

### 数采知识专项进度（2026-10-10）

按[知识专项任务书](docs/superpowers/plans/2026-10-10-daq-knowledge-workstream-task-brief.md)建立了从原件清单到 Dev 答案复审的工程合同。私有候选包含 276 份原件、114 个章节、70 条历史记录及 335 条新增候选、375 个更新依赖题；它们仍不能作为已审核答案使用。B2 已将 335 条新增候选逐条回查原件并形成 337 条未签认字段候选和增量复审包，另有 142 个待补字段定义、48 条内容裁决项、293 处章节一致性发现及 102 处历史定位差异。[归一化审计](docs/reviews/2026-10-10-b2-normalization-review.md)记录准确边界。D2 原 572 项与新增 337 项的事实、权限或页面审核均未签认。独立受控原件归档与重取尚未完成；D3 真实发布审计拒绝激活；D4 的 375 道题全是同一题族草案，十类冻结题、真实 Dev 模型回放和独立答案复审均未完成。[逐项状态与阻断](docs/reviews/2026-10-10-daq-knowledge-workstream-status.md)区分工程验证、资料签认和真实发布。合入代码或推送远端不等于 Dev 知识切换，更不等于生产部署。

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
.venv/bin/python scripts/verify_upstream_snapshot.py --upstream ../AI-FAE-Agent
cd webui && npm test && npm run build
```

源码核验会读取 `upstream-source.json` 中的完整提交 SHA，比对两仓的 `src/` Git tree 和 `requirements.txt` blob，并拒绝未提交的共享源码改动。GitHub CI 定义也包含此门，私有上游仓需配置只读 `FAE_UPSTREAM_READ_TOKEN`。数采仓已有 `origin/master`；本次未取得远端 CI 运行结果和上游 ref 保护设置的核验证据，因此不能把本机测试或已推送代码计为正式依赖 pin、CI 通过或生产部署。

正式依赖 pin 之前还需完成旧 FAE Dev 回归、双服务合同、上游受保护 ref、数采真实 Dev 回放与独立答案复审。数采资料和用户附件不能自动进入知识库；发布仍受来源、事实裁决和角色权限约束。

## 数采资料增量导入（离线 Dev）

原始资料已从相机仓移入本仓 Git 忽略的 `tmp/数据采集设备资料汇总-20260920(1)/`。导入器只读取具有独立 SHA-256 清单的仓外归档，不从 `tmp/` 直接发布。当前仓外归档与原件在同一台机器，持久受控存储门尚未通过。

```bash
.venv/bin/python scripts/daq_knowledge.py import \
  --archive "$DAQ_ARCHIVE" --manifest-sha256 "$DAQ_MANIFEST_SHA256" \
  --output "data/knowledge/candidates/$DAQ_MANIFEST_SHA256-extractor-1.json"
.venv/bin/python scripts/daq_knowledge.py diff \
  --previous data/knowledge/candidates/<旧清单哈希>-extractor-1.json \
  --current data/knowledge/candidates/<新清单哈希>-extractor-1.json \
  --records data/knowledge/reviewed-records.json
.venv/bin/python scripts/daq_knowledge.py fingerprint \
  --records data/knowledge/reviewed-records.json
.venv/bin/python scripts/daq_knowledge.py review-packet \
  --archive "$DAQ_ARCHIVE" --manifest-sha256 "$DAQ_MANIFEST_SHA256" \
  --snapshot "data/knowledge/candidates/$DAQ_MANIFEST_SHA256-extractor-1.json" \
  --recipe review_recipes/k2.json \
  --output "data/knowledge/review/$DAQ_MANIFEST_SHA256-k2-20261008-2.json"
.venv/bin/python scripts/daq_knowledge.py review-diff \
  --previous data/knowledge/review/<旧归档哈希>-<旧配方版本>.json \
  --current data/knowledge/review/<新归档哈希>-<新配方版本>.json
```

同一归档和同一抽取器版本重导入得到同一候选快照；抽取规则变化时须提升 `extractor_version` 并另存快照，不覆盖原文件。更新报告按文件内容哈希列出新增、变更、删除，以及必须复审的记录。PDF 按页、Markdown/TXT 按行定位；软件、固件、图片等仅保留元数据。非空 `publish`、`activate` 和 `rollback` 现在要求可信 D3 审核与 Dev 观察适配器；仓库 CLI 没有这些适配器时拒绝非空切换。不可变快照与活动指针是独立步骤。命令详情见 `.venv/bin/python scripts/daq_knowledge.py --help` 和 [D3 合同](docs/knowledge/2026-10-10-daq-d3-readiness-contract.md)。这些工具不改变当前 API 的 `empty-dev-v0`，不代表真实知识、Platform 或生产已发布。

`fingerprint` 只计算待审核摘要，不代替审核。具名事实复审、资料权限复审和链接复审须各自将对应摘要写入 `record_sha256`；更改值、条件或来源会使旧事实审核失效，扩大可见或转发角色还会使旧权限审核失效。已核验规格、软件关系和组合步骤必须引用同一发布中已核验的实体或拓扑。当前角色标识限于 `internal_fae`、`tmall_support`、`channel`，实际可见范围仍待 K-2 负责人签认。字段和审核格式见[记录合同](docs/knowledge/2026-10-08-daq-k1-record-contract.md)。

`review-packet` 会再次校验归档和候选快照，给 19 个 K-2 议题收集精确页/行定位与短摘录，并把所有资料角色权限设为待签认。输出只允许在 Git 忽略目录或仓外，文件权限为 `0600`。每次资料更新生成新包，用 `review-diff` 找到需重审的议题和未映射来源；改动配方也会标记受影响议题。细节见[复审指引](docs/knowledge/2026-10-08-daq-k2-review-guide.md)和[本机演练](docs/reviews/2026-10-08-daq-k2-candidate-rehearsal.md)。复审包不产生已核验事实、角色授权或在线发布。

K-3 配方 `review_recipes/k3.json` 用同一命令生成软件、固件与交付链接的候选包；`asset_path` 选择器只记录二进制的文件哈希、大小和路径，不读取包内内容。当前本机演练有 15 个待审议题，软件/固件目录的 30 个二进制资产已定位，但官网逐页记录、模块投影及全部权限/兼容裁决仍待完成。见 [K-3 复审指引](docs/knowledge/2026-10-09-daq-k3-review-guide.md)和[演练结果](docs/reviews/2026-10-09-daq-k3-candidate-rehearsal.md)。此候选包不改变在线 `empty-dev-v0`。

K-5 已据同一归档形成 66 条真实资料的结构化候选/冲突记录，保存于 Git 忽略的私有目录，逐条绑定原件哈希和页/行定位。EGO 两个分辨率变体要求显式 `resolution_variant`，没有选择器的通用 EGO 询问不能命中其中任一变体规格。记录仍全部不可答，真实资料尚未发布；覆盖与冲突见 [K-5 清洗记录](docs/knowledge/2026-10-09-daq-k5-real-records.md)。

K-6 从已归档的详细指南再提取 USB 与 Wi-Fi 单机 PC 采集拓扑/流程，私有记录增至 70 条；变体和 EgoViewer 版本仍须核验。K-5/K-6 分别有 66/70 行事实与权限复审队列，另有 30 行软件/固件资产组合复审队列，均保留在 Git 忽略目录；证据和发布边界见 [K-6 扩充记录](docs/knowledge/2026-10-09-daq-k6-pc-flow-candidates.md)及[原件审查](docs/reviews/2026-10-09-daq-k5-evidence-audit.md)。

K-7 修正了两条 HUB 阈值比较符并补齐组合流程的未决标记，从 70 条中划出 47 条供苍渊做事实与权限复审；提案只有候选指纹，没有签名。临时发布演练通过，真实知识仍未激活。见 [K-7 复审提案](docs/reviews/2026-10-09-daq-k7-review-proposal.md)。

`DAQ_KNOWLEDGE_RELEASE_ROOT` 指向独立活动指针；无活动指针时继续使用 `empty-dev-v0`。非空发布必须通过 D3 审核，启动时校验签名、运行时版本和上游源码身份。未启用认证身份的本机服务拒绝加载非空知识，不再默认授予 `internal_fae`；认证模式还要求可信的逐人资料角色映射。切换或回滚指针后须重启 Dev 服务，健康和 trace 均报告确切知识版本。早期 [K-4 本机消费者设计](docs/superpowers/specs/2026-10-09-daq-k4-dev-consumer-design.md)仅记录合成演练；当前实际边界以 [D3 合同](docs/knowledge/2026-10-10-daq-d3-readiness-contract.md)为准。当前没有经裁决的真实知识发布，也未设置该活动指针。

## 内部认证模式的装配

独立 DAQ Postgres 须先按清单执行 `PYTHONPATH=. python scripts/migrate_daq_pg.py` 查看迁移顺序，再由有数据库权限的操作者设置 `DAQ_DATABASE_URL`，确认数据库名后执行 `PYTHONPATH=. python scripts/migrate_daq_pg.py --apply --confirm-database-name <数采数据库名>`。脚本只读取 `DAQ_DATABASE_URL`，ASGI 启动不会自动改表。迁移先排除相机身份会话，再用数采专属约束替换共享表内的相机 Agent ID 限制；认证模式启动会核验该约束和 DAQ 安装标记。

内部模式至少配置 `DAQ_PLATFORM_IDENTITY_ENABLED=true`、独立 `DAQ_DATABASE_URL`、`DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE`、`DAQ_PLATFORM_IDENTITY_BASE_URL`、`DAQ_PLATFORM_PUBLIC_ORIGIN`、`DAQ_PLATFORM_SESSION_KEYRING_FILE` 和 `DAQ_PLATFORM_ALLOWED_SUBJECT_IDS`。归档启用项是 `DAQ_ATTACHMENT_ARCHIVE_ENABLED=true`；Platform 归档工作进程调用 `python -m daq_fae.archive_cli`，只读取 DAQ 数据库与附件目录。HTTP Task 启用项是 `DAQ_PLATFORM_TASK_ENABLED=true`，还需独立 `DAQ_PLATFORM_TASK_CONTENT_KEYRING_FILE` 与 `DAQ_PLATFORM_TASK_PUBLIC_KEY_PATHS_JSON`；路由为 `/internal/platform/v1`，签名 audience 为 `ai-daq-fae-agent`，启动会核验 DAQ 安装身份标记。任务附件引用目前明确失败，须完成 Platform 附件读取授权合同后才能启用该路径。缺少这些配置时相应能力保持关闭，不能从旧 FAE 借用凭据或数据。

# AI DAQ FAE Agent

数采 FAE 是独立版本、独立知识发布和回滚的内部服务，复用固定源码版本的通用 FAE 运行时。生产入口为 [数采 FAE](https://agent.orbbec.com.cn/daq/)。

## 当前生产状态（2026-10-23，已部署）

生产实际加载 **388 条记录、318 个章节、329 个逐段证据单元**：保留原有已核实规格和流程，新增 **20 份 PDF/Markdown 的 310 段资料记载参考**；SDK 保留 **24 条用法、17 条更新说明**。现有内容可以附版本、条件和冲突交付，不等待全部候选章节成为确定参数。

| 对象 | 实际生产身份 |
| --- | --- |
| 回答源码 / runtime | `5a55dffa1a44cf4021d56b3a7a3a42de265ec60b` / `daq-772ca8f76d07` |
| 主知识 | `64a484100fda4e38d519450745700cf978017cb73816019f23f47dd6b57c833c` |
| SDK | `3c00fa1c798aa90894391583b97368125e2e7c1ce2740a77b28485484fb679a9` |
| 镜像 | `sha256:aa7cff939e9e45af7dc47d7c575016e2d004c076680a45b017906c47ba35163a` |

实际容器健康、重启 0，默认生产签名加载通过，未启用 Dev 放行；公网 `/daq/` 返回 200 且保持路径。新增 4 题经独立 Codex 答案复审接受：3 题继承原候选，UMI 英文实体别名问题修复后仅重测此题；共 5 次尝试，未重跑历史通过题。保留 iOS 已发布文字的帮助性遗漏，不宣称所有回答已完美。共享源码及相机仓未改；生产只做只读观察，没有模型评测。

资料记载不等于设备实测，精确 SoC/BOM、具体组合验证、整套实测续航及图片/表格歧义仍按实际依据限定；这些局部缺项不阻挡其他内容上线。原件、来源权限、发布身份与整组回滚均已保留。详见[本次生产发布记录](docs/reviews/2026-10-23-document-reference-production-release.md)；[此前机制修复发布](docs/reviews/2026-10-23-daq-real-feedback-production-release.md)与[此前 Dev 复审](docs/reviews/2026-10-23-daq-real-feedback-repair.md)作为历史保留。

## 架构和范围

当前 Agent 的完整执行链、模型与程序分工、会话与证据合同，以及后续坏例的架构改进流程，统一见 [DAQ FAE 工作流设计](AI_DAQ_FAE_Agent_工作流设计.md)。回答质量问题先按该地图定位最早失效层，禁止用题目特例、固定答案或静默兜底修补。

数采使用自己的 Agent 身份、请求 schema、会话、请求账本、附件目录和权限文件；与相机共用 PostgreSQL 逻辑数据库和模型网关凭据，数采表位于独立 `daq_fae` schema。知识只经独立内容寻址发布指针进入生产，`tmp/` 和附件不能成为权威来源。2026-10-21 起，Platform 向当前目录内有效企业成员授予 DAQ 使用权，DAQ 将企业成员映射到已发布资料的 `internal_fae` 查看角色；匿名用户、合作方和相机 Agent 身份仍被拒绝。仅已发布资料可检索，`/review` 仍关闭。SDK 示例和变更日志只证明特定构建包内的接口、用法或日志文字，不能证明设备、固件、连接方式、平台或整机镜像组合已实测兼容。

回答保留结构化来源、能力覆盖、可见缺口、终态和 trace，正文不展示本机来源路径。Provider HTTP 400 按配置错误报告。本机空知识服务使用 `/app/`；生产认证界面为 `/daq/`，浏览器 API 经 `/daq/api/` 转到数采后端。个人登录后的业务试点及设备兼容验收另行记录。

## 本机 Dev

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-daq.txt
cp .env.example .env
npm --prefix webui ci
npm --prefix webui run build
.venv/bin/uvicorn daq_fae.app:app --env-file .env --host 127.0.0.1 --port 8081
```

`.env.example` 默认离线空知识。Dev 候选视图使用 `DAQ_DEV_CANDIDATE_MODE=true` 及 `DAQ_DEV_CANDIDATE_*`、`DAQ_DEV_SOURCE_CHECK_FILE`，仅绑定 loopback，不能进入认证模式。候选章节只能帮助定位缺口，不能作为 `resolved` 证据。

```bash
DAQ_KNOWLEDGE_RELEASE_ROOT="$(mktemp -d)" .venv/bin/python -m pytest -q tests
.venv/bin/python scripts/verify_upstream_snapshot.py --upstream ../AI-FAE-Agent
npm --prefix webui test
npm --prefix webui run build
```

## 知识更新

原件保存在 Git 忽略的 `data/knowledge/local_archive/`，逐文件按 SHA-256 核验。高频更新先比较来源、实体、变体、字段含义、测量条件和依赖关系；改变或撤回的事实不能继承旧审核状态。新事实完成来源核对、Dev 真实问答及独立答案复核后，生成不可变主知识或 SDK 发布包，最后切换对应指针。知识发布不改变相机服务。

清洗和增量设计见[知识库设计与更新工作流](docs/knowledge-base/knowledge-base-design-and-update-workflow.md)、[知识质量任务书](docs/knowledge-base/2026-10-21-knowledge-base-quality-improvement-task-brief.md)、[完整设计](docs/2026-10-08-数采FAE完整设计.md)、[SDK 目录说明](Knowledge_SDK/README.md)与[本次生产发布记录](docs/reviews/2026-10-23-document-reference-production-release.md)。

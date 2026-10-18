# AI DAQ FAE Agent

数采 FAE 是独立版本、独立知识发布和回滚的内部服务，复用固定源码版本的通用 FAE 运行时。生产入口为 [数采 FAE](https://agent.orbbec.com.cn/daq/)。

## 当前状态（2026-10-18，已部署）

本轮在本机隔离 Dev 核对 276 份产品原件和 143 份 SDK、Viewer、固件原件，封版 27 条主知识、23 条 SDK 包内用法及 17 条唯一变更日志记录。主知识五题、SDK 19 题及目录、固件身份回归已完成真实模型问答和独立语义复核。Python 全量 **1314 passed**、WebUI **246 passed** 且生产构建通过；本轮没有改动共享 `src/`。详见[发布前复核](docs/reviews/2026-10-17-daq-knowledge-dev-release.md)。

| 对象 | 本机 Dev 封版身份 |
| --- | --- |
| DAQ 代码 | `56940355daa0fd2b3f67b5f1fe74f1317a179b51021ea5024fff7cfb52fe2dde`；运行时 `daq-56940355daa0` |
| 主知识基底 | `370570f70f1cd8121f38437a1d7158b6489b7be6edea8ca3d654c31b88d70e91` |
| 27 条主知识 Pro 增量 | `3c8df43e43d4c2bc585d4e3f9a6120070b552f486b880eee086a2c2e89c215a7` |
| 23 条 SDK 用法、17 条日志 | `ee6b77ad30fc8f449cfe59f10d823987347a860f8521308784bfc4f3aadf0805` |

源码提交 `50dc7e96f28df4aa4b4b64516f5be97fd9c835c0` 已部署到生产 DAQ 容器，镜像 `sha256:da2420fa528c10fd73a971503974bb40b1a82cf1e77b3c8622ec4bbb0bd5f098`；内部 `/health` 确认上表的运行时、主知识和 SDK 身份。数采和相机容器均健康、重启 0。公网 `/daq/` 及前端资源返回 200，匿名会话与历史接口返回 401 且 `Cache-Control: no-store`；生产未调用模型评测。部署、备份和只读烟测见[生产发布记录](docs/reviews/2026-10-18-daq-knowledge-production-release.md)。Git 推送和本机 staging 与生产切换分别记录。

原件覆盖审计为产品 **9 已支撑内部可答发布 / 42 仅候选 / 225 仅归档**，SDK 侧 **30 / 0 / 113**。这是文件数，不能替代 27/23/17 条知识记录数。主候选图另有 715 条未发布记录；图像候选、软件兼容、官网链接、复杂组合及设备实测仍须按来源和条件单独审核。参见[完整覆盖审计](docs/reviews/2026-10-16-daq-full-material-coverage.md)。

## 架构和范围

数采使用自己的 Agent 身份、请求 schema、会话、请求账本、附件目录和权限文件；与相机共用 PostgreSQL 逻辑数据库和模型网关凭据，数采表位于独立 `daq_fae` schema。知识只经独立内容寻址发布指针进入生产，`tmp/` 和附件不能成为权威来源。正式资料目前只向 `internal_fae` 可见；SDK 示例和变更日志只证明特定构建包内的接口、用法或日志文字，不能证明设备、固件、连接方式、平台或整机镜像组合已实测兼容。

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

清洗和增量设计见[知识专项任务书](docs/superpowers/plans/2026-10-10-daq-knowledge-workstream-task-brief.md)、[完整设计](docs/2026-10-08-数采FAE完整设计.md)、[SDK 目录说明](Knowledge_SDK/README.md)与[本次生产发布记录](docs/reviews/2026-10-18-daq-knowledge-production-release.md)。

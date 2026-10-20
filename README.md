# AI DAQ FAE Agent

数采 FAE 是独立版本、独立知识发布和回滚的内部服务，复用固定源码版本的通用 FAE 运行时。生产入口为 [数采 FAE](https://agent.orbbec.com.cn/daq/)。

## 当前状态（2026-10-20，已部署）

当前生产保留 27 条已审主知识，SDK 知识为 24 条包内用法及 17 条唯一变更日志记录。2026-10-20 的数采场景与证据范围升级已部署；代码和提示词的本机 Dev 静态回归为 **1618 passed**，聚焦真实问答和独立复审结果、未完成的全量回放范围见[本次发布记录](docs/reviews/2026-10-20-daq-scenario-production-release.md)。本轮没有改动共享 `src/`，没有对生产回答接口发送模型评测题。

| 对象 | 当前生产身份 |
| --- | --- |
| DAQ 源码与运行时 | `7d7232902cdd6cdea2baf4d61ccc0960ef1ab428`；`daq-ba50906bd98b` |
| 27 条主知识 | `19d1a31b7606df155fda7d1ca8e336a845e3b78d19f15818af89e7127d2f18c0` |
| 24 条 SDK 用法、17 条日志 | `2bfd0538e0fa1ba46e607d8a55b63a06f9b397141b2c0192b9a6a84e2bd66d44` |

当前生产 DAQ 镜像为 `sha256:564a0e39eeb5402ae5e579276b1a6949a9f44b8d45b094ccd124b0889d6d6b0c`。数采与相机容器均健康、重启 0，相机镜像未切换。公网 `/daq/` 及前端资源返回 200，匿名会话与历史接口返回 401。代码推送、知识发布与生产切换分别核对，详见[2026-10-20 发布记录](docs/reviews/2026-10-20-daq-scenario-production-release.md)；前次知识发布见[2026-10-18 发布记录](docs/reviews/2026-10-18-daq-knowledge-production-release.md)。

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

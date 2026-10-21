# AI DAQ FAE Agent

数采 FAE 是独立版本、独立知识发布和回滚的内部服务，复用固定源码版本的通用 FAE 运行时。生产入口为 [数采 FAE](https://agent.orbbec.com.cn/daq/)。

## 当前状态（2026-10-21，已部署）

当前生产加载 **66 条已审记录、2 个章节**，SDK 知识为 **24 条包内用法、17 条版本说明**。已发布 EGO 1600 规格章节及 EGO Pro 单机录制章节，并修复真实问答发现的规格字段匹配问题。本次定向契约回归 **86 通过**；两道新增题执行了 3 次真实 Dev 问答，只重测失败题，已通过题未重跑，并完成独立 Codex 答案复审。共享 `src/` 和相机仓未改，生产没有运行模型评测题。详见[本次生产发布记录](docs/reviews/2026-10-21-kq-production-release.md)。

| 对象 | 当前生产身份 |
| --- | --- |
| DAQ 镜像源码与运行时 | `ab1fa4bd18ec1c3831466cde00fd3a32e63ff396`；`daq-5e86db35722f` |
| 66 条主知识、2 个章节 | `8ab080a4fc404a2cb88b4c5f1f2ec4f660895b3525c39812f1d095f499300ea3` |
| 24 条 SDK 用法、17 条版本说明 | `514fbb3cc29c17bbcaa6b7b8b12ee6791b255d29d198cd5a4ef65b7eeb50a041` |

当前生产 DAQ 镜像为 `sha256:85ce9efaded21ab5bbc9f7c377af73ed31851763d34290c7e22494b6e27f0e60`。数采与相机容器均健康、重启 0，相机容器和镜像未切换。公网 `/daq/` 返回 200，匿名历史接口返回 401。实际容器默认签名加载器已确认主知识、章节及 SDK 身份；环境、旧双指针、源码、镜像及数据库备份均保留，支持整组回滚。用户真实登录和设备试点不由这些只读检查替代。

本次发布引用的 10 份原件已按原始哈希独立归档、核对。**114 个原候选章节仍未审核通过，未随发布转正**；其他冲突、软件兼容、复杂组合及设备实测仍按来源和条件分别处理。历史文件覆盖数字见[2026-10-16 覆盖审计](docs/reviews/2026-10-16-daq-full-material-coverage.md)，不把文件数当成知识记录数或全量清洗完成证明。

## 架构和范围

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

清洗和增量设计见[知识库设计与更新工作流](docs/knowledge-base/knowledge-base-design-and-update-workflow.md)、[知识质量任务书](docs/knowledge-base/2026-10-21-knowledge-base-quality-improvement-task-brief.md)、[完整设计](docs/2026-10-08-数采FAE完整设计.md)、[SDK 目录说明](Knowledge_SDK/README.md)与[本次生产发布记录](docs/reviews/2026-10-21-kq-production-release.md)。

# AI DAQ FAE Agent

数采 FAE 是独立版本、独立知识发布和回滚的内部服务，复用固定源码版本的通用 FAE 运行时。生产入口为 [数采 FAE](https://agent.orbbec.com.cn/daq/)。

## 当前生产状态（2026-10-23，已部署）

生产实际加载 **78 条已审记录、8 个章节、19 个逐段证据单元**，SDK 为 **24 条包内用法、17 条版本说明**。已发布目录集合、已知输入交付、组合范围及澄清误吞的通用机制修复，补入功耗/续航条件和双目＋WristCam 指南。11 个场景取得独立 Codex 答案接受：9 轮结转、2 个失败定向重测，通过题未重跑。

| 对象 | 实际生产身份 |
| --- | --- |
| 回答源码 / runtime | `b6e5afe27a4eb206436392fa0bdad4ca604d1a4b` / `daq-dc16f42d8cbe` |
| 主知识 | `c604e5b52db4fbd4cc933d560836de6938d3da2ab9e4d9c0a49084f18be55593` |
| SDK | `c06c19298b896d9f530ad4d9e8f5d1ccfc9f5dd0ab1942eeaaa9e46a6e41e637` |
| 镜像 | `sha256:8a323bec3ca136fc81353b66e7171e4fb55183393a1622996ab983f1679c1de2` |

实际容器健康、重启 0，默认生产签名加载通过，没有启用 Dev 放行；公网 `/daq/` 返回 200 且保持该路径，未登录历史返回 401。共享源码和相机未改，生产没有发送模型测试题；源码、镜像、环境、双指针及数据库备份支持整组回滚。同期 Platform API 另有镜像变更，已单列观察，不声称全平台无变化或全健康。

63mm SoC、四目精确组合/同步精度、整套实测续航及其余未审候选保持准确缺口。详见[实际生产发布记录](docs/reviews/2026-10-23-daq-real-feedback-production-release.md)、[机制修复与 Dev 复审](docs/reviews/2026-10-23-daq-real-feedback-repair.md)和[原 Dev 证据索引](docs/reviews/2026-10-23-daq-real-feedback-evidence.json)。[前一版生产记录](docs/reviews/2026-10-21-kq-production-release.md)作为历史保留。

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

清洗和增量设计见[知识库设计与更新工作流](docs/knowledge-base/knowledge-base-design-and-update-workflow.md)、[知识质量任务书](docs/knowledge-base/2026-10-21-knowledge-base-quality-improvement-task-brief.md)、[完整设计](docs/2026-10-08-数采FAE完整设计.md)、[SDK 目录说明](Knowledge_SDK/README.md)与[本次生产发布记录](docs/reviews/2026-10-21-kq-production-release.md)。

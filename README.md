# AI DAQ FAE Agent

数采 FAE 独立发布服务和知识版本，复用固定源码版本的通用 FAE 运行时。首期仅面向内部 FAE 试点。

## 当前状态（2026-10-12）

**23 条来源核对知识的内部试点已在生产主机激活**：[数采 FAE 入口](https://agent.orbbec.com.cn/daq/)。这不是全量数采知识库或业务验收完成。首发仅 6 个产品/变体实体和 17 条规格 claim，可见角色为 `internal_fae`，Platform 只授权苍渊一名内部用户。软件兼容、下载链接、组合拓扑、操作流程、候选章节和未裁决冲突均不发布。

| 项目 | 实际状态 |
| --- | --- |
| 数采代码 | 生产源码提交 `124ae1adc715732d80522b30c1d51f8f67532e0f`；运行时标识 `daq-124ae1adc715`。仓库最新提交以 Git 查询为准 |
| 知识 | 活动发布 `6a2225fde85cd09339620088011d25bf2331953b6f981be6f1f184ce5537e8f4`；23 条，只有 `internal_fae` 可读 |
| 原件 | 276 个、2,038,212,224 字节保存在本仓 Git 忽略的本机受限归档；清单 SHA-256 为 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56` |
| Dev 模型回放 | Opus 5.5 的冻结 24 题经独立 Codex 语义复核全部通过：15 个 `resolved`、9 个 `safe_abstained`；串行 P95 46.77 秒 |
| 生产接入 | 与相机 FAE 共用 PostgreSQL 逻辑数据库和 Anthropic 网关/API 凭据；数采表位于独立 `daq_fae` schema，会话/内容密钥与逐人权限独立 |
| Platform | DAQ catalog 和身份启动代码为 `7bce35f0`；控制库迁移 113 已执行；只有苍渊获得 DAQ user scope，相机原 `all_members` 授权保持 |
| 线上验证 | 数采、相机、Platform API 和身份入口健康；`/daq/` 返回 200，受保护 API 匿名返回 401，公网健康路径返回 404；生产未调用模型 |

生产使用从已核实的相机与 Platform 镜像构建的源码覆盖镜像：数采镜像 `sha256:42691addf0ff2b5e881c862f99fa096e33b6186cc2e7e838e19e055ce737db57`，Platform API 镜像 `sha256:79f8bcd40a04854319575c93f4036898e67bd9da5176a1516be7ae17ff85b064`。数采镜像内 226 个源码文件与该提交发布包逐字节相等；Platform catalog、迁移和 WebUI 资源哈希相等。标准 Platform 全量部署会拒绝覆盖现有 HR 配置，本次保留线上 7 份 Compose 输入，只更新 API 镜像及身份入口允许列表；详细回执、备份和回退见[生产试点发布记录](docs/reviews/2026-10-12-daq-production-pilot-release.md)。

独立原始事实/权限人工签字、异机原件复制和独立发布签名已按项目负责人本轮决定取消。来源、版本、角色和 Dev 答案复核仍被程序核验。上游固定 SHA `bf1ce9af2d467447e39e2be0caae0bb87b1ba689` 的 Git tree 已与本仓 `src/` 核对；受保护持久 ref 尚无核验证据。生产个人登录后的端到端交互、并发延迟与业务试点反馈也尚未验收，不能把页面可访问或代码推送当作这些验收。

生产 `/health` 的 `source_checked_records` 字段沿用 Dev 候选计数，在正式发布模式显示 0；它不表示活动知识为空。以 `knowledge_release` 对应不可变 manifest 的 `record_count=23` 和角色过滤为准。

## 本机 Dev

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-daq.txt
cp .env.example .env
npm --prefix webui ci
npm --prefix webui run build
.venv/bin/uvicorn daq_fae.app:app --env-file .env --host 127.0.0.1 --port 8081
```

`.env.example` 默认离线空知识。真实模型回放只在 Dev 运行；生产只做不触发模型的身份、健康、路由与隔离烟测。Dev 真实资料候选视图使用 `DAQ_DEV_CANDIDATE_MODE=true` 及 `DAQ_DEV_CANDIDATE_*`、`DAQ_DEV_SOURCE_CHECK_FILE`，仅绑定 loopback，不能进入认证模式。候选章节只帮助定位缺口，不允许作为 `resolved` 证据。

```bash
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/verify_upstream_snapshot.py --upstream ../AI-FAE-Agent
npm --prefix webui test
npm --prefix webui run build
```

最新本机验证为 Python 876 题、WebUI 235 题通过，WebUI 构建通过。共享 `src/` 没有本轮改动；本轮没有执行生产模型评测。[Dev 复核](docs/reviews/2026-10-12-daq-operator-pilot-dev-review.md)记录逐题范围和延迟。

## 知识更新

原件仍留在 `data/knowledge/local_archive/`，不提交 Git，也不从未核验的 `tmp/` 直接发布。高频更新时先逐文件校验哈希，再对来源、实体、变体、字段、测量条件和依赖关系做增量差异；改变或撤回的事实自动失去旧审核状态。新的规格须在 Dev 完成来源核对、真实模型回放和独立答案复核，再生成不可变发布包并切换活动指针。知识发布不改变相机服务。

清洗与增量设计见[知识专项任务书](docs/superpowers/plans/2026-10-10-daq-knowledge-workstream-task-brief.md)、[逐项状态](docs/reviews/2026-10-10-daq-knowledge-workstream-status.md)和[完整设计](docs/2026-10-08-数采FAE完整设计.md)。生产只包含上述 23 条；其余 742 条候选图记录及软件/链接/组合资料仍待后续核对。

## 接口边界

本机空知识服务为 `/app/`；生产认证界面为 `/daq/`，浏览器 API 经 `/daq/api/` 转到数采后端。Provider HTTP 400 按配置错误报告，不能伪装成暂时性 503。回答保留结构化来源、能力覆盖、可见缺口、终态和 trace；正文不展示本机来源路径。数采使用自己的 Agent ID、schema、会话、请求账本、附件目录和权限文件；相机数据库的 `public` schema 不作为数采知识来源。

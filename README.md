# AI DAQ FAE Agent

数采 FAE 独立发布服务和知识版本，复用固定源码版本的通用 FAE 运行时。首期仅面向内部 FAE 试点。

## 当前状态（2026-10-15）

**首批 SDK 用法知识已切换生产，待业务观察。**[数采 FAE 入口](https://agent.orbbec.com.cn/daq/)当前只读核实为运行时 `daq-0cdf458368c5`、主知识发布 `75e2e02d0553c67136ba9255586572d068e34a76c40767abc7258fa0178b37de`、SDK 知识发布 `4a6da8eb9e6d49e53da0740fafccfd44b0ffddf50f5645a8ecf81b2e2aab9113`，见[SDK 发布记录](docs/reviews/2026-10-15-daq-sdk-production-release.md)。主知识仍只有 6 个产品/变体实体和 17 条规格 claim；SDK 新增 15 条包内用法事实，仅 `internal_fae` 可见。软件兼容、下载链接、组合拓扑、操作流程、候选章节和未裁决冲突尚未发布；个人登录后的业务验收仍待观察。

| 项目 | 实际状态 |
| --- | --- |
| 数采代码 | 生产运行时 `daq-0cdf458368c5`，源码包提交 `0cdf458368c5724477a2ccab5a4985eb0aeb5b95` |
| 知识 | 主知识 `75e2e02d0553c67136ba9255586572d068e34a76c40767abc7258fa0178b37de`，23 条已审记录；SDK `4a6da8eb9e6d49e53da0740fafccfd44b0ffddf50f5645a8ecf81b2e2aab9113`，15 条已审用法事实 |
| 原件 | 276 个、2,038,212,224 字节保存在本仓 Git 忽略的本机受限归档；清单 SHA-256 为 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56` |
| Dev 模型回放 | SDK 六题 4 道有据作答、2 道安全弃答；另两道旧域回归通过。均 HTTP 200、无 fallback，SSE/历史/trace 一致，详见[SDK 发布记录](docs/reviews/2026-10-15-daq-sdk-production-release.md) |
| 生产接入 | 与相机 FAE 共用 PostgreSQL 逻辑数据库和 Anthropic 网关/API 凭据；数采表位于独立 `daq_fae` schema，会话/内容密钥与逐人权限独立 |
| Platform | DAQ catalog 和身份启动代码为 `7bce35f0`；控制库迁移 113 已执行；只有苍渊获得 DAQ user scope，相机原 `all_members` 授权保持 |
| 线上验证 | 2026-10-15 新版 DAQ 与相机均健康；SDK 发布在生产容器中可按 `internal_fae` 检索、`channel` 不可见；生产未用于模型评测 |

2026-10-15 的 SDK 切换只重建数采容器，保持相机和 Platform 镜像不变；数据库、环境、两个知识指针和旧镜像有回退材料。详见[SDK 发布记录](docs/reviews/2026-10-15-daq-sdk-production-release.md)。

独立原始事实/权限人工签字、异机原件复制和独立发布签名已按项目负责人决定取消。来源、版本、角色和 Dev 答案复核仍被程序核验。上游固定 SHA `4f453b77073005d24d9f07f36016b7172009443a` 的 Git tree 与本仓 `src/` 相同；受保护持久 ref 尚无核验证据。生产个人登录后的端到端交互、并发延迟与业务试点反馈尚未验收。

生产 `/health` 的 `source_checked_records` 字段沿用 Dev 候选计数，在正式发布模式显示 0；它不表示活动知识为空。主知识以 `knowledge_release` 对应不可变 manifest 的 `record_count=23` 为准，SDK 另看 `sdk_knowledge_release` 的 15 条记录和角色过滤。

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
DAQ_KNOWLEDGE_RELEASE_ROOT="$(mktemp -d)" .venv/bin/python -m pytest -q tests
.venv/bin/python scripts/verify_upstream_snapshot.py --upstream ../AI-FAE-Agent
npm --prefix webui test
npm --prefix webui run build
```

最新本机验证为 Python 1125 题、WebUI 246 题通过，WebUI 构建通过。共享 `src/` 没有本轮改动；本轮没有执行生产模型评测。[旧域 Dev 复核](docs/reviews/2026-10-14-daq-alias-variant-dev-review.md)记录先前完整题集；本轮 SDK 六题与旧域两题在最终版本上的结果见[SDK 发布记录](docs/reviews/2026-10-15-daq-sdk-production-release.md)。

## 知识更新

原件仍留在 `data/knowledge/local_archive/`，不提交 Git，也不从未核验的 `tmp/` 直接发布。高频更新时先逐文件校验哈希，再对来源、实体、变体、字段、测量条件和依赖关系做增量差异；改变或撤回的事实自动失去旧审核状态。新的规格须在 Dev 完成来源核对、真实模型回放和独立答案复核，再生成不可变发布包并切换活动指针。知识发布不改变相机服务。

清洗与增量设计见[知识专项任务书](docs/superpowers/plans/2026-10-10-daq-knowledge-workstream-task-brief.md)、[逐项状态](docs/reviews/2026-10-10-daq-knowledge-workstream-status.md)和[完整设计](docs/2026-10-08-数采FAE完整设计.md)。生产主知识只包含上述 23 条，SDK 另有 15 条用法事实；其余 742 条候选图记录及软件/链接/组合资料仍待后续核对。

## SDK 资料开发

`Knowledge_SDK/` 从 2026-10-15 原件归档构建首批 SDK 用法事实：15 条记录、42 处精确源码引用，覆盖 OrbbecSDK 2.9.0 的 EGO Linux x86_64 C++ 示例与 RK3576 设备端 libobkit C 示例，现已独立发布到生产。`sdk_evidence` 只解释对应构建包中的 API、示例和编译方法；设备、固件与整机镜像的实际兼容性仍须由 `check_software_support` 的精确证据确认。快速更新流程会核对原件哈希、构建日期、成员增删改、源码行与独立 Dev 问答复审。归档内其他平台、Android、BLE 和 Viewer 包仍未形成可回答事实，见 [SDK 覆盖审计](Knowledge_SDK/audit/coverage_gaps.md)、[SDK Dev 问答复审](docs/reviews/2026-10-15-daq-sdk-dev-review.md)及[生产发布记录](docs/reviews/2026-10-15-daq-sdk-production-release.md)。

## 接口边界

本机空知识服务为 `/app/`；生产认证界面为 `/daq/`，浏览器 API 经 `/daq/api/` 转到数采后端。Provider HTTP 400 按配置错误报告，不能伪装成暂时性 503。回答保留结构化来源、能力覆盖、可见缺口、终态和 trace；正文不展示本机来源路径。数采使用自己的 Agent ID、schema、会话、请求账本、附件目录和权限文件；相机数据库的 `public` schema 不作为数采知识来源。

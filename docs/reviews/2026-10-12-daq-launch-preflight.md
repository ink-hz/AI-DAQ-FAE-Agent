# 数采 FAE 正式上线预检（2026-10-12）

**结论：未激活、未注册、未部署。** 用户已授权正式上线操作；本轮按现有设计检查发布与 Platform 入口，未绕过拒绝的门。

## 知识激活

在当前 742 条候选/冲突记录、114 节候选章节的私有 D3 输入上，实际调用 `audit_readiness` 得到 `ready=false`、`online_eligible=false`、`answer_quality=pending_D4`。拒绝码为 `archive_custody`、`archive_retrieval`、`unsigned_reviews`、`review_digests`、`impact_obligations`、`runtime_compatibility`、`identity_review`、`dev_batch`、`dev_batch_digest`、`declared_blockers`、`release_review`、`independent_approval`、`content_contract`；其中未签认审核项为 909，Dev 批次未冻结。用户此前取消的独立复制和具名签认只适用于本机 Dev 预览；正式发布器仍要求独立审批和可核验的存储/权限证据。

为避免让另外 719 条候选记录阻断已核对事实的审查，本轮从 `dev_source_checked_v2.json` 精确账本和经哈希验证的原件中生成 **23 条记录、0 节候选正文**的最小正式发布*提案*，明确排除软件、链接与模块投影。私有提案在 Git 忽略的 `data/knowledge/curated/release-narrow-20261012/`，目录 `0700`、文件 `0600`；`proposal.json` SHA-256 `d0aa6694f202d11592cdb966d582a0e54ac1a1c5b544cc65103df4c241ad3da1`。此提案保留未获得的批准为空，不把本机 Dev 的 Codex 来源核对或“Dev internal only”标记冒充正式事实/权限批准。

对该最小提案再次调用 D3：`content_contract` **通过**，但 `ready=false`，仍有 `archive_custody`、`archive_retrieval`、`unsigned_reviews`（23 条正式审核待签）、`review_digests`、`impact_obligations`、`runtime_compatibility`、`identity_review`、`dev_batch`、`declared_blockers`、`release_review`、`independent_approval`。私有 `audit.json` SHA-256 `22421c8041dc380625014d2a80c6bb7b94621cf737827afc00d206d4fdc5268f`。因此没有调用私有发布/指针写入函数；正式 `active.json` 仍不存在。

## Platform 注册与服务部署

拟定同域入口为 `https://agent.orbbec.com.cn/daq/`，浏览器 API 前缀为 `/daq/api/`。DAQ WebUI 已挂载 `/daq/`，客户端以 `/daq/api/chat` 等路径调用；网关须将 `/daq/api/*` 去掉 `/daq/api` 前缀后转发到独立 DAQ 后端的根 API，并将 `/daq/*` 转发到该后端的 WebUI。当前生产网关尚无 `/daq` 路由，内部 DAQ 后端端口与生产部署配置也未定，因此该地址仅为注册与路由目标，尚不可访问。

Platform `master` 的 `9f72e905` 已合入 DAQ 企业成员专属 launch、数据库迁移 113、受授权过滤的 Agent Catalog 卡片、`/daq` 网关配置和登录返回路径；相机身份合同及跨 Agent 数据库测试通过。代码未建立任何数采授权，公开 `registry.yaml` 也尚无 DAQ 项。该提交尚未部署到生产，数据库迁移、网关配置和 DAQ 服务均未在生产启用；仅有代码入口不能计为注册上线。正式接入仍需要独立的 DAQ 服务地址、数据库/密钥文件和 Platform 身份配置，并做两服务身份与数据隔离联调。

数采仓只有本机 `.env.example`，没有 DAQ 生产环境配置或部署包；正式知识与身份配置缺失时应用按设计拒绝启动。上游共享源码的本机精确快照校验已通过，但受保护、持久 Git ref 和相机旧域质量门的正式证明尚未取得。代码合入与远端主线提交均不计为部署或试点验收。

## 后续可执行顺序

1. 对最小 23 条提案提供独立原件存储/复取证明、逐项事实和内部角色权限审核，以及可信控制面的整包批准；补齐运行时/身份审查和影响义务。
2. 冻结覆盖正式发布范围的十类 Dev 题批，在真实 Dev 环境回放，由独立 Codex/FAE 逐题审查；随后用受信审批器执行 D3 stage、activate 和 rollback 健康/trace 观察。
3. 将已合入的 Platform Catalog、授权数据库迁移、Agent launch 和网关配置与独立 DAQ 服务做 Dev 身份/数据隔离联调；正式部署时按迁移、服务、授权和路由次序激活，最后做只读生产健康及入口检查。

所有模型评测只在 Dev；本轮没有生产模型评测，也没有修改相机 FAE 服务。

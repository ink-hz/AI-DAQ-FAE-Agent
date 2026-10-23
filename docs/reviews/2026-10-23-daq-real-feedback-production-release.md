# 2026-10-23 DAQ 通用机制修复生产发布

## 实际结果

用户明确授权“上线上线”。已于 2026-10-23 完成单 DAQ 切换，实际容器健康、重启 0；生产容器内默认签名加载器再次确认 **78 条记录、8 个章节、19 个逐段证据单元、24 条 SDK 用法、17 条版本说明**。生产未发送模型评测题，本轮也未追加 Dev 模型回放。

| 对象 | 实际发布身份 |
| --- | --- |
| 回答源码 | `b6e5afe27a4eb206436392fa0bdad4ca604d1a4b` |
| Runtime / DAQ 内容摘要 | `daq-dc16f42d8cbe` / `dc16f42d8cbe4136efacf2e33d8e78fbb20dc8f9269f6f5623432d887f54ce9c` |
| 镜像 | `ai-daq-fae-agent:daq-b6e5afe-feedback` |
| 镜像 ID | `sha256:8a323bec3ca136fc81353b66e7171e4fb55183393a1622996ab983f1679c1de2` |
| 主知识 | `c604e5b52db4fbd4cc933d560836de6938d3da2ab9e4d9c0a49084f18be55593` |
| SDK | `c06c19298b896d9f530ad4d9e8f5d1ccfc9f5dd0ab1942eeaaa9e46a6e41e637` |
| 共享源码 pin | `6bad94591dc4c40c29ad054b526c0ec526478607`，未更新 |

## 发布的是通用机制

原始触发为 10 月 21 日五个会话八轮，修复最早失效层及相邻负例见[机制修复记录](2026-10-23-daq-real-feedback-repair.md)和[完整工作流](../../AI_DAQ_FAE_Agent_工作流设计.md)。本次没有原问、测试编号或产品固定回复分支：

- 目录按实际成员集合交付，包含变体；不能取到完整目录却遗漏成员仍通过。
- 产品任务可自主查询必要输入；已查到的辅助事实可交付，不能冒充组合结果已核实。
- 系列、具体型号、文档前提和用户排除条件分别治理；缩短查询不能丢失限制。
- schema 单次纠正重新核对全部目标，跨实体条件不得复制；不增加自动改目标兜底。
- 澄清目标与明确产品任务并存时继续取证，不能用条件陈述捷径吞掉问题。
- 章节按语义主题组织，移位单元重新核算并独立审核；摘要截断不能代表完整资料没有。

验证沿用 11 个不同场景的独立接受结论：9 轮保留原通过身份，2 个未通过问题在最终 Dev 候选重测。没有把这些原执行身份改成新生产 main 的重放。语义复审之外，模型名义回显冲突、两项非阻断措辞/帮助性意见和真实资料缺口继续保留。

## 从 Dev 到生产的实际合同

生产包与最终 Dev 包的 78 条记录、8 个章节、19 个单元、正文、源引用和角色完全相同。没有把 Dev 指针、签名或 `allow_local_dev` 直接用于生产。

实际引用的 11 份原件共 7,324,776 字节，按原始哈希独立归档在 VM：`/opt/ai-daq-fae-agent/source-archives/5e4e695df0fb452db3b36c673dcc26770bbe0e35da95afa740af9d548250a725`。独立 Codex 亲自只读 SSH 校验全部哈希、root 所有权、目录 0500/文件 0400，并通过 SSH 完整取回原字节在客户端重算；未用本机声明伪造远端保管。

据实际生产授权、来源/权限和 Dev 证据结转，由独立 Codex 生成并持有独立生产密钥，实际签署新 canonical payload。私钥未上传、未入镜像或冻结包；Dev/生产交叉信任拒绝。生产执行范围为 `authenticated_internal_fae_production`，正常加载没有 Dev opt-in。通用 D3 审计仍返回固定 `online_eligible=false/pending_D4`，没有篡改成自动发布认证；本次授权、独立答案复审、生产审批与实际发布观察均有各自收据。

保留现有企业成员入口与 `internal_fae` 查看/转交范围，没有增加天猫、渠道或匿名可见数据。其他未审章节、63mm SoC、四目精确组合/固件/同步精度、组合平均负载及实测续航仍为缺口。

## 构建、切换与只读验证

源包由精确 Git allowlist 加实际旧容器 UI 制成：263 个文件逐字节核验，无额外秘密、私钥、链接或路径逃逸。共享 `src/`、迁移、依赖、compose 和 WebUI 对旧生产源码 `ab1fa4b` 均未变化。镜像基于旧 DAQ 镜像离线构建，先验证镜像源树/摘要及默认生产 main/SDK 加载，再做切换。

仅重建 `ai-daq-fae-agent` compose 项目的 `ai-daq-fae-backend`。环境只更改 runtime、main、SDK、公钥路径和公钥哈希五项身份字段；其他全部环境行保持原样，包括数据库、模型网关/凭据、登录及附件设置。旧 main/SDK 指针字节未修改。实际容器创建于 `2026-10-23T10:07:48Z`，无数据库迁移。

| 观察 | 实际结果 |
| --- | --- |
| 容器 health | production、anthropic、企业身份启用，runtime/main/SDK 精确匹配，healthy/restarts=0。 |
| 实际默认加载器 | 78/8/19/24/17，源码与内容摘要匹配，channel/tmall 不可见。 |
| `/daq/` | HTTP 200，保持 `/daq/`，未跳根。 |
| `/`、`/api/health` | 登录页 HTTP 200、平台健康接口 HTTP 200。 |
| `/admin`、匿名 DAQ 历史 | 均 HTTP 401，保持认证边界。 |
| 相机 | ID `2573c0bceb1898b4b26438ca44bbdc2b11795c4dfaa26fd1800021fd84521d44`、镜像 `sha256:2695d65d3877892a7a2137192a444c1f1983bc53c1b07c129aa2d511da1efbee`、健康及重启计数均与本次切换前一致。 |

没有冒用用户身份，没有将匿名 GET 或默认加载检查写成钉钉真实登录/业务试点验收。

### 同期平台差异：保留首次失败检查

“所有非 DAQ 容器完全相等”的初次后验断言**失败**，没有删掉或改写。实际仅 Platform API 的容器 ID/镜像改变：旧镜像 `9cf4afa1…`，新镜像 `7e7dcb84…`，新 source revision `d0dea5d3a048158e112b04ee731d3e2f4af881ad`，独立 `orbbec-agent-platform` compose 项目，创建于 `10:08:12Z`。其健康仍为 healthy/restarts=0；其余 22 个非 DAQ 容器（含相机）全部记录字段相等。

本次审核脚本命令只操作 DAQ 项目；未对平台执行恢复或修改。平台变更的操作者/触发原因没有在本次归因，不能声称所有平台容器未变。Platform brain 在切换前已为 unhealthy，切换后保留此状态，不计作新增故障，也不宣称全平台健康。公网上述五路检查均正常。

## 回滚与冻结证据

实际备份：`/opt/ai-daq-fae-agent/backups/feedback-20261023-20261023T100728Z`。环境、旧双指针、源码归档、旧镜像、数据库只读 dump 及恢复目录清单均已生成并核验。源码/镜像/环境/两份知识须成组恢复到 `ab1fa4b` / runtime `daq-5e86db35722f` / main `8ab080a4fc404a2cb88b4c5f1f2ec4f660895b3525c39812f1d095f499300ea3` / SDK `514fbb3cc29c17bbcaa6b7b8b12ee6791b255d29d198cd5a4ef65b7eeb50a041`。

恢复入口：`bash /opt/ai-daq-fae-agent/staging/release-20261023-feedback-b6e5afe/rollback.sh`。失败自动恢复脚本经过独立审查；本次切换成功，没有实际执行回滚或恢复演练。没有恢复共享数据库，不能用 DAQ 回滚去操作相机或平台并行版本。

独立生产观察复审确认 `daq_release_actual_success=true`、无 Important DAQ 发布缺陷。签名包 SHA `9db73b64836ea3b3b5b7c10637941f583c719ec1b88a6b58cee9ceebcb54bdc9`；独立观察报告 SHA `a1d002e47c04a1c6388f231bc397f61c52559e96730243dc0050fddc293f7516`。

受控冻结索引：`data/knowledge/curated/real-feedback-production-20261023/index.json`，64 个文件，SHA `3c43f14a6df0820271b5f92fda7d6d23f3ced902335d15857c62ec761f0309bb`。包含原失败/通过问答、独立复审、生产签名/公钥、构建/加载/切换/环境/备份/公网收据和真实平台差异，不包含私钥、环境原文或另一份原件复制。Git 文档整合、Dev 结果和生产部署分别记载。

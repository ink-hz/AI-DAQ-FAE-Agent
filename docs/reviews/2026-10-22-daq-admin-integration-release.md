# 2026-10-22 DAQ 管理平台接入发布记录

## 结论

已补齐并上线 DAQ 到 Platform 管理端的会话数据与健康观测链路。生产副本实际可读 **12 个会话、20 轮问答、20 条 trace**；本机源同步另有 2 条反馈。DAQ 与相机回答容器、代码、知识均未切换；相机仓库未修改。本任务真实模型调用 **0 次**。

原因为入口注册与管理观测脱节：Catalog 已有 DAQ，但远端采集只覆盖相机/ADMIN；云端原有远端轮询关闭，缺少 DAQ 健康探针。修复在相邻 Platform 仓完成，沿既有源校验、受限管理镜像、脱敏、稳定匿名标识、签名和加密链路接入。

## 发布身份

- Platform 主线整合提交 `3915dc7c` 已推送；准确生产候选 `b0301d9865d60d265ce67d2909b88f17975c8e33`，保留远端分支 `fix/daq-admin-runtime-20261022`。
- 新 Platform API 镜像 `sha256:1ff94eb92ab93b89d69ebd57ba7d589c24538949b4bf42b46d6a086dc0752994`。从原生产 `21d82d60` 构建本次受审更改，未发布无关 SDK 主线工作。481 个源码文件一致，原前端静态文件一致，只重建 Platform API。
- 本机管理迁移 15 的 SHA-256：`17e3ed5239e8935766779eed80571035809b404f7ef600c17eef8940823a6c8d`。增加独立 `platform_source_daq`，保留原视图和权限，不迁移或改写回答服务数据库。
- DAQ 保持镜像 `sha256:85ce9efaded21ab5bbc9f7c377af73ed31851763d34290c7e22494b6e27f0e60`、runtime `daq-5e86db35722f`、主知识 `8ab080a4fc404a2cb88b4c5f1f2ec4f660895b3525c39812f1d095f499300ea3`、SDK `514fbb3cc29c17bbcaa6b7b8b12ee6791b255d29d198cd5a4ef65b7eeb50a041`。
- 相机保持镜像 `sha256:d1f500357c6314cdadcf2c24f1939e92c8153b7a827714ac6553c38ed8d7218e`。最后核对全部原非 API 容器身份不变，三个目标服务健康、重启计数 0。

## 实际接入与边界

采集身份是 `daq`；管理 API 的 `source_kind=fae` 表示运行时家族，`agent_id=ai-daq-fae-agent` 表示数采服务。本机键为 `daq:`，云端使用既有匿名键，不能把 DAQ 当成相机统计。所有外键在 DAQ 镜像内，来源只读取 DAQ 容器实际 `DAQ_DATABASE_URL` / `daq_fae` 并验证安装身份，不回退到相机库。

两次真实导入均成功，12/20/20/2 数量不膨胀，无孤立轮次、缺根 trace 或坏 trace 行。按原队列顺序确认云端批次 `14246–14253`；用新容器实际只读账号及管理仓库读取，DAQ 全部记录归属正确，一个已有回答的 session → turn → trace 关联通过。trace 状态 completed 20；旧 outcome 为 resolved 9 / safe_abstained 11，原样保留，不代表本次重新评测了旧回答。

云端相机会话 1531，与本机一致；其中原积压 8 个经正常队列补齐，没有混入 DAQ。副本新鲜度 current，实际 DAQ HTTP 健康探针 healthy/connected，runtime/main/SDK 与上面身份一致。

管理端 trace 继续遵循原云端脱敏摘要策略，完整步骤不开放；`steps=[]` 与 `detail_availability=unavailable` 如实保留。源端仍保存完整 trace。没有真实阶段时间戳时不以阶段耗时伪造绝对时间线。

安装 DAQ 独立采集任务，每 300 秒同步；原签名加密云端推送任务仍为 300 秒。两个任务最终 loaded/enabled、最后退出 0。发布期间临时暂停云端任务，切换后已经恢复，未跳过/重置水位或删除未确认队列。正常约两个周期可见，积压或故障会延迟。

## 验证与未执行项

相关源契约 292 项通过；最终 45 项是受影响子集，不重复累加。健康链路 165 项通过；两个既存 HR 失败在旧生产基线复现并明确排除，未称全量测试通过。独立 Codex 已批准冻结代码和部署助手，部署助手 12 项本地故障模拟通过。没有重跑已通过的真实模型题。

生产只做现有记录和健康的只读观察、授权管理数据同步；未发送模型题、业务消息或伪造身份。实际 `/admin`、`/admin/agents`、`/admin/sessions` 和相关未登录 API 返回 401，`/daq/`、`/api/health` 返回 200，认证边界保持。实际云端认证配置是 `dingtalk`；仓库诊断收据的默认 `deployment.auth=ssh-tunnel` 仅为该构造器默认标签，不当作生产认证配置。

保留诊断脚本自身的失败：迁移凭据格式不匹配、诊断子进程缺少完整启动环境、健康字段误用，以及未登录页面误设 200 断言；均在原服务边界内纠正，没有放宽身份或伪造验收。原错误和最终成功收据并存。

**未执行**：登录态浏览器页面验收、生产模型评测、生产回滚演练；完整原始 trace 未向云端开放。开发侧没有宣称上述项目通过。

## 回滚与证据

Platform VM 备份及受审回滚脚本：`/opt/orbbec-agent-platform/staging/daq-admin-20261022/`。该目录还承载活动 Compose 覆盖，禁止清理。保留旧 API 镜像、原 17 份 Compose、环境和可列出内容的数据库 dump；本机有迁移前 schema-only 备份。加法迁移可留存，不自动恢复/删除共享数据库数据。回滚前暂停采集与推送，恢复旧 API 后须先核对标签式 importer 身份再恢复推送，保留所有签名队列。

详细操作与回滚说明在相邻 Platform 仓 `docs/operations/2026-10-22-daq-admin-observability-release.md`。本机完整证据保留于 `tmp/daq-admin-integration-20261022/`，对应[哈希索引](2026-10-22-daq-admin-integration-evidence.json)。索引不包含数据库凭据、Cookie、发布私钥或用户问答正文；不要随工作树清理删除原证据。

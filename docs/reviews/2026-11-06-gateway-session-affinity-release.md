# 2026-11-06 网关会话粘性（metadata.user_id）生产发布

## 问题与归因

外部网关按请求在多个上游通道间路由。DAQ 请求不带 `metadata.user_id`，同一会话的连续请求
落到不同上游，提示缓存无法复用，thinking 签名在换上游后被拒。归因 Loop/协议层（客户端 wire
形态）。相机 FAE 已于 2026-11-05 修复并发布，本次为 DAQ 同类修复。

## 改动

- 共享运行时重新固定到 `7d906ea`（= 原 `6bad945` + 会话粘性，未带入相机其它改动）：主 Loop 与
  视觉请求带 `metadata.user_id`（会话 ID 的不透明摘要），Loop 请求带顶层 `cache_control`。
- 网页聊天与 Platform 任务入口按 DAQ 会话 ID 绑定 `provider_session_scope`。
- 知识内容、来源、角色、提示、模型不变；主知识/SDK 仅按新 runtime 重新绑定签名（所有者授权）。

## 测试（按改动点）

- 新增单测通过；上游快照校验通过。
- 全量：2370 passed / 20 failed；这 20 项在未改动的 master 上同样失败，与本次无关。
- Dev：同网关、`DaqAnthropicAdapter`、DAQ 系统提示，3 段对话各 5 次工具续接：每段全程同一上游；
  `claude-opus-5-5` 上游每轮命中约 1.5 万缓存 token，`anthropic/claude-opus-5.5` 上游无缓存命中。

## 生产

- 源码 `f3092df`，runtime `daq-d3b554ded706`，main `16339dbc…`，SDK `6568f4e7…`（388/318/24/17）。
- 镜像 `sha256:447b69af…`（基于原非 root 镜像 `7360ead7…` 叠加 7 个改动文件；镜像内全部代码与
  `f3092df` 逐字节一致）；容器 `00e43a73…`，healthy、重启 0、UID 21081、只读根、CapDrop ALL。
- 只改 compose 中的镜像与两条只读知识挂载；附件/trace 读写挂载与秘密不变。
- 切换前：无 running 请求；备份 `/opt/ai-daq-fae-agent/backups/session-affinity-20261106-20261106T032313Z`
  （env、compose、容器 inspect、`daq_fae` schema dump）。
- 切换后：health 正确；`/daq/` 200；匿名 chat/history 401；相机容器 ID/镜像/健康不变。生产模型评测 0 次。
- 回滚：恢复备份 env，用原 `release-20261030T012018Z-03c3d2/rendered/runtime.json` 重建（原镜像与
  原知识挂载均保留）。

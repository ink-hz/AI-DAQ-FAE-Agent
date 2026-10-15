# 数采 FAE 真实问答修复生产发布

日期：2026-10-15。结论：**已部署，待业务观察**。本次只切换 DAQ 镜像、运行时、知识指针和 DAQ 源码链接；相机 FAE 与 Platform 镜像未改变，生产回答接口未用于模型评测。

## 冻结版本与 Dev 复核

| 项目 | 旧版 | 新版 |
| --- | --- | --- |
| DAQ 源码包 | `654a668a748960ea7069970878385a6f9fa4025b` | `234335e5bbea93184e0b5ecd108e85bc90d49ae5` |
| DAQ 运行时 | `daq-654a668a7489` | `daq-234335e5bbea` |
| DAQ 镜像 | `sha256:504563f3ec9c7aaf550afcae2fe0ac10834961059394878ab96f2c2ed2c5e259` | `sha256:96e13a1f6caac9148c4d169b2840d6065bd32f3b4a3d2ff5621b574b6b40e021` |
| 知识发布 | `42317417a8a9031dda4562c1a97d326ec1eeaa7216707d7f3d7233e9ed6913e8` | `9125d60608f12ff990bdaf9fcfea27bbd1807528ac41b7876e9cf46a2531c860` |
| 相机镜像 | `sha256:a3415a24298331f3e5c122d00952a1035562cd3c207adc6e92cef961a5a15031` | 相同 |

发布包 SHA-256 `544131c5405f462b4fa2a04a7885705a0c3cdbe10a0b02824da4920dd32a2656`，上传后哈希一致；新镜像内 237 个发布文件与源码包逐字节一致。依赖清单与旧版相同。上游固定修订仍为 `4f453b77073005d24d9f07f36016b7172009443a`，本轮没有修改共享相机 `src/`。新知识的 23 条已审记录为 6 个实体、17 条规格 claim；`internal_fae` 可见 23 条，`channel` 可见 0 条。软件、链接、拓扑和流程仍未发布。

[完整 Dev 复核](2026-10-14-daq-alias-variant-dev-review.md)记录主集 14 场景、16 轮和边界集 5 题的独立语义 PASS；Python 1109 题、WebUI 246 题及构建通过。[前次暂缓](2026-10-14-daq-final-release-hold.md)保留一次 FIELD01 终稿拒绝及 FIELD14 HTTP 402，不将失败抹去。2026-10-15 用最终发布 `9125d606…`、运行时 `daq-234335e5bbea` 在隔离 Dev 重试：FIELD14 结果 SHA-256 `b988f8746db5f2973c6ecb2b2f3cf43275b64f602004b2a126fc9953979e7832`；FIELD01 结果 SHA-256 `ee4615c4f5739674f45e2e05b0c834ec918358e4f024a8669d14a22f9b97a650`。两题均 HTTP 200、`safe_abstained`、无 fallback，五项 SSE/历史/trace/source 完整性均真；独立 Codex 核对答案、目标、来源、条件及缺口，均判 PASS。FIELD01 有一句“实测不能突破这个上限”比标称规格证据更绝对，属非阻断措辞，后续应改为“已发布规格不支持 1920×1200@60 fps”。

## 备份与切换

生产备份位于主机受限目录 `/opt/ai-daq-fae-agent/backups/field-qa-20261015T082236Z`，包括完整 `ai_fae` PostgreSQL dump、旧环境、旧知识指针、旧源码链接和两个容器身份。dump SHA-256 `1fc91e05988d09a3a88fdf4b86e44cefdc7b326e587d2bb53f888ef1fdaaa908`，`pg_restore --list` 可读；环境备份 SHA-256 `c6f9ca6b46bae2d3cc8b6aa9ccaed633822e5f0f3834c12b96a5b1d75e7a5291`，知识指针备份 SHA-256 `ade401368f21f96eee3e7a0a756ef4b5c9460a00e94236a5661effeafc5323e2`。新源码和知识 manifest 先作为不活动版本暂存并校验。没有执行数据库迁移。

切换脚本在锁内核对旧容器、新镜像、备份和 manifest，停止数采容器后原子切换知识指针、运行时环境与源码链接，只以 DAQ Compose 项目重建 `ai-daq-fae-backend`。脚本有 DAQ 独立失败回滚路径。一次切换成功，新容器 `healthy`、重启 0，`/health` 返回 `production`、`ai-daq-fae-agent`、新运行时和新知识发布。旧镜像、源码和知识发布均保留，可按备份恢复。

## 生产只读烟测与限制

- 公网 `/daq/` 返回 200；匿名 `/daq/api/authenticated/conversations` 和 `/daq/api/history` 返回 401、`Cache-Control: no-store`；相机首页返回 200。没有向生产 `/chat` 发送模型题。
- 在生产容器内以数据库现存会话 owner 与当前绑定角色调用只读持久化接口：两条旧会话均可见，每条详情有 2 条消息和 1 轮答复，`read_only=true`；旧会话续聊均被 `ConversationNotFound` 拒绝。个人浏览器登录态尚未由用户本人复验。
- DAQ 镜像 `sha256:96e13a…` 与相机镜像 `sha256:a3415a…` 均健康、重启 0；相机镜像与切换前相同。Platform 镜像未触碰。

现阶段仍是内部 FAE 试点，知识覆盖有限；生产个人交互、真实并发时延和业务反馈仍需观察。本次代码推送、Dev 回放、知识发布与生产切换分别有独立身份记录。

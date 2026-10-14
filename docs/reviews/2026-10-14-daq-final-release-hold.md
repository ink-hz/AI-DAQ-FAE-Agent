# 数采 FAE 最终发布暂缓记录

日期：2026-10-14。**生产未切换**。这是本轮完整 Dev 题集及独立语义复审通过后的发布绑定冒烟记录；失败保留，不能用前一批通过结果覆盖。

## 冻结身份与已经完成的门

- 修复代码提交 `234335e5bbea93184e0b5ecd108e85bc90d49ae5` 已推送到 `origin/master`。共享相机 `src/` 未修改，上游固定修订 `4f453b77073005d24d9f07f36016b7172009443a` 的快照核验通过。
- 完整主集 14 场景、16 轮及边界集 5 题的真实 Dev 回放和独立 Codex 语义复审均通过，结果哈希及范围见[Dev 复核](2026-10-14-daq-alias-variant-dev-review.md)。Python 1109 题、WebUI 246 题及构建通过。
- 按源码提交绑定的暂存运行时为 `daq-234335e5bbea`，不可变知识发布为 `9125d60608f12ff990bdaf9fcfea27bbd1807528ac41b7876e9cf46a2531c860`，含 23 条已核实记录，前驱指向线上旧版 `42317417a8a9031dda4562c1a97d326ec1eeaa7216707d7f3d7233e9ed6913e8`。原件归档重新核对 276 文件、2,038,212,224 字节，清单 SHA-256 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`。
- 发布包 SHA-256 `544131c5405f462b4fa2a04a7885705a0c3cdbe10a0b02824da4920dd32a2656`。生产主机隔离 staging 镜像 `sha256:96e13a1f6caac9148c4d169b2840d6065bd32f3b4a3d2ff5621b574b6b40e021` 已离线构建；237 个包内发布文件与镜像逐字节一致，新知识 manifest 的运行时及 23 条记录校验通过。生产环境、活动知识指针、源码链接、容器均未改变。

## 最终发布绑定 Dev 冒烟失败

隔离 Dev 使用新发布 `9125d606…`、运行时 `daq-234335e5bbea`，仍通过真实 `/chat`、企业身份、PostgreSQL、SSE、历史和 trace。第一轮结果 SHA-256 `31568c67896245ccf00c237a40a56005581b08cb583953fbf9b9062ced47f48c`：

- FIELD01 实体及四项 EGO 变体规格均查到并绑定正确，`selection` 组合结果 `not_found`。模型两次未按缺证反馈提交 `safe_abstained`，证据策略以 `daq_partial_evidence_only` 拒绝；终态 `invalid_answer_contract`、fallback。SSE、历史、trace 和来源一致。这是同一题在完整主集通过后出现的非确定性终稿失败，不能标为通过。
- FIELD14 在 schema 阶段收到 Anthropic 网关 HTTP 402，终态 `provider_protocol_error`、fallback，未进入产品取证。超过 120 秒后单题重试仍为 HTTP 402，第二次结果 SHA-256 `eb5aba4a6b64b6b96625b1e0bf5e9331c6f350d01534e8f598de2264c342c631`。没有把该供应侧错误记为资料缺失，也没有对生产回答接口重试。

下一次切换前，须在 Dev 使用最终发布身份重新跑 FIELD01 与 FIELD14，均得到无 fallback、完整性一致的结果，并独立复核答案语义；FIELD01 的缺证终稿若再次拒绝，需按其真实 trace 修复 DAQ 终稿机制并重新跑相关回归。HTTP 402 恢复前，不能证明新旧任一线上回答服务可正常使用主模型。

## 线上只读状态

切换前及 staging 镜像验证后，生产 DAQ 均为运行时 `daq-654a668a7489`、知识 `42317417…`、镜像 `sha256:504563f3ec9c7aaf550afcae2fe0ac10834961059394878ab96f2c2ed2c5e259` 且容器健康。相机镜像始终为 `sha256:a3415a24298331f3e5c122d00952a1035562cd3c207adc6e92cef961a5a15031` 且健康。**代码推送、staging 镜像及知识暂存均不是生产部署。**

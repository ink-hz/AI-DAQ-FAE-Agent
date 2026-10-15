# 数采 SDK 首批知识生产发布

日期：2026-10-15。结论：**已部署到数采 `/daq/`，待内部 FAE 实际使用反馈**。本次只切换数采镜像、运行时、主知识和独立 SDK 知识指针；相机 FAE 与 Platform 镜像未改变。生产回答接口未用于模型评测。

## 版本与真实 Dev 复核

| 项目 | 发布前 | 发布后 |
| --- | --- | --- |
| 数采源码提交 | `234335e5bbea93184e0b5ecd108e85bc90d49ae5` | `0cdf458368c5724477a2ccab5a4985eb0aeb5b95` |
| 数采运行时 | `daq-234335e5bbea` | `daq-0cdf458368c5` |
| 主知识 | `9125d60608f12ff990bdaf9fcfea27bbd1807528ac41b7876e9cf46a2531c860` | `75e2e02d0553c67136ba9255586572d068e34a76c40767abc7258fa0178b37de` |
| SDK 知识 | 未激活 | `4a6da8eb9e6d49e53da0740fafccfd44b0ffddf50f5645a8ecf81b2e2aab9113` |
| 数采镜像 | `sha256:96e13a1f6caac9148c4d169b2840d6065bd32f3b4a3d2ff5621b574b6b40e021` | `sha256:cfcd4841e6c8b770cc2df05bf4751935d49d5515fee71b4173e45dd0238777a9` |
| 相机镜像 | `sha256:a3415a24298331f3e5c122d00952a1035562cd3c207adc6e92cef961a5a15031` | 相同 |

主知识仍是同一批 23 条已审实体和规格记录；新旧 manifest 的 `records`、`sources`、`sections` 逐项相同，仅绑定新运行时和这次 Dev 复审。SDK 发布包含 15 条源码核对过的用法事实、42 处精确来源，目录事实摘要为 `220a7aa608a21877b883dac342549ae24f7e4060803c68c50ff37961bb03daaa`。SDK 用法不能充当设备、固件或镜像组合兼容证明，角色仅 `internal_fae` 可见。

最终运行时及最终主知识版本在隔离 Dev 对真实 `claude-opus-5-5` 重放[六道 SDK 题](../../evals/daq_sdk_dev_20261015.jsonl)：4 题 `resolved`、2 题因组合证据缺失 `safe_abstained`，均 HTTP 200、无 fallback，stage、SSE/历史、trace、终态与来源五项一致。Codex 逐题对照源码核实 API 类型、调用与清理顺序、旧构建接口及固件升级边界；结果 `results.jsonl` SHA-256 `b5621108141a5c4b26679d851b8bec935b34f99b9d587be394a7264b9fe3d233`，独立审查位于 Git 忽略的 `data/knowledge/curated/sdk-deploy-20261015/final-sdk-independent-review.json`。同一运行时补跑旧域 FIELD01、英文 FIELD14：均 HTTP 200、`safe_abstained`、无 fallback，完整性五项为真，结果 SHA-256 `dc597ee8844a4032515bdf50ee1c226bfb1a4d79f2b51efb65127447268b75b3`。FIELD01 保留既有“实测不能把标称上限推高”的偏绝对措辞，见[上次发布记录](2026-10-15-daq-field-qa-production-release.md)；本次没有把它当作 SDK 事实。

最终本机回归为 Python **1125 passed**、WebUI **246 passed**、前端构建通过；固定上游 `src/` tree 校验通过。发布包 SHA-256 `37b5b05aee0154a52e713c5f5beeed6504322e1b86049d1039ede29f2e2162bf`，上传后哈希一致。为避免完整重建从宽松依赖约束拉取新版本，数采新镜像以旧生产镜像为基础覆盖本次源码和前端，镜像内 245 个运行文件与发布包逐字节一致；依赖层沿用旧版。相机共享运行时代码和相机镜像均未变。

## 备份、切换与线上验证

切换前完整 `ai_fae` PostgreSQL dump、环境、旧主知识指针、SDK 指针缺席状态、源码链接和两服务镜像身份保存在生产主机受限目录 `/opt/ai-daq-fae-agent/backups/sdk-20261015T103833Z`。dump SHA-256 `d41ca44666487841c3cbdb599aa93aa9fb38e06e0e1eb00999f37cfe10e6c583`，`pg_restore --list` 可读；环境与旧主指针 SHA-256 分别为 `0f2f99f6ff45cfa9ed478fe5144a624e9121f83516aa4ce0052d48d425bcae22`、`0dad8cf10840614cc8ea7b55a655932163d3a1b555503eaec9f1ba7fa888771b`。没有执行数据库迁移。

新主知识与 SDK manifest 在不活动目录先按哈希暂存，并由新镜像验证；指针切换及回退在隔离目录中演练。新镜像还使用与生产同格式的环境、密钥和临时活动指针完成启动检查，`/health` 同时返回新运行时、主知识和 SDK 知识身份。正式切换脚本在锁内校验旧容器、新镜像和备份，停数采容器后切换两个知识指针、环境与数采源码链接，再只重建 `ai-daq-fae-backend`；失败路径可恢复旧指针、环境、源码及镜像。本次一次切换成功，数采容器 `healthy`、重启 0。

生产只读烟测：内部 `/health` 返回 `production`、`ai-daq-fae-agent` 和上述三个新版本；活动主知识、SDK 指针均与其一致。生产容器内只读加载 SDK 发布得到 15 条记录，`internal_fae` 的 EGO 同步检索命中 1 条，`channel` 命中 0 条。公网 `/daq/` 返回 200；匿名会话和历史接口返回 401 且 `Cache-Control: no-store`；相机首页返回 200，镜像仍为原摘要、健康且重启 0。个人登录后的 SDK 问答、设备实测兼容及业务试点反馈尚待观察，不能由 Dev 回放或只读烟测代替。

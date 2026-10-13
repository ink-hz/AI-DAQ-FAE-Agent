# 数采 FAE 工作流修复生产发布记录

日期：2026-10-13。结论：**已部署待业务观察**。本次只切换数采 FAE 的镜像、运行版本和同内容知识快照；没有修改相机服务或 Platform 镜像，也没有向生产回答接口发送评测问题。

## 精确版本与范围

| 项目 | 发布前 | 发布后 |
| --- | --- | --- |
| 数采源码 | `ace8726f2a74b9f8796b4498ab93f14c075a9a8e` | 构建包提交 `49345ccbed83489c6a315d9272f0ea18dfd473a3`；工作流代码提交 `8e1af259a377f3dde65c8158fd11797363bcb69a`，两者之间仅三份验收文档变更 |
| 数采镜像 digest | `sha256:504e1aaa5167ef2017eecae2c04ecdaa63a74c74557c652da0f6d50b2f8f488d` | `sha256:acbd04e1e5fcff70dca83ab44d2345c3ac9c844e1c532814ee0c5d13e1bd95a6` |
| runtime | `daq-ace8726f2a74` | `daq-49345ccbed83` |
| 知识 release | `a63baf103892250b8474ad9e464b8df47c889e020313e37dd71ad815af9d55a6` | `2f52df9cef39cf524628ba897ef53f027dff8e95396adef61dc79599f7710cb9`，`previous_release` 指向旧版 |
| 相机 `src/` | `e69021117158cbdc7e6d75220c5247fe631bbe58` | 未改；相机仓 `4f453b77073005d24d9f07f36016b7172009443a` |

新知识快照按旧活动 manifest 的 23 条记录和原始提案逐条对照，仍为 6 实体、17 claim、0 章节，仅 `internal_fae` 有 23 条可见记录；软件、流程、链接等未发布内容没有被加入。新 release 的运行身份绑定为 `daq-49345ccbed83`，manifest SHA-256 即其 release ID。上游 pin 没有更新，受保护持久 Git ref 仍缺核验证据。

发布包 `ai-daq-fae-49345ccbed83489c6a315d9272f0ea18dfd473a3.tar.gz` SHA-256 为 `573e34a85dcb1ae72b5e6d2b7be3fbba80b3337a6ba50d11e3625fab882fe8a9`；上传生产主机后哈希相等。新镜像以旧数采镜像的精确 digest 为基底，删除旧应用源码后覆盖发布包；镜像内 237 个 `daq_fae`、`src`、脚本、迁移、提示词和网页文件与包内逐字节相等。依赖清单及共享 `src/` 本次未变。浏览器实际下载的 `index-BfJHGgSB.js` SHA-256 为 `f0c1e6ebf18b643597cea81b9cba28dd5ec2764dea3395e48a4fbd127eebbd26`，与本机构建资源相等。

## Dev 门与发布前核验

- [工作流 Dev 复审](2026-10-12-daq-workflow-remediation-dev-review.md)记录新旧冻结集 48 行逐题独立 PASS，46 个真模型 turn，串行 P95 37.8 秒、最大 39.9 秒；回答模型没有自评。
- 新 release 精确绑定的隔离 Dev 认证路径再次跑 S01、M01、P02、C01、A01：依次为 `resolved`、`resolved`、`safe_abstained`、`resolved`、`resolved`；来源、runtime/release、SSE/历史/trace 一致且无 fallback。Codex 再次阅读这五题答案，未见严重事实、型号、权限或交付错误。前四题私有结果 SHA-256 为 `64f82cdec17bd18d1682bdc30994326c85dcf93a3ea896e7a1d59bb117ba8f43`；图片题结果为 `17cd9d228fecb58683c81a78c69d318777de8eea7251d2c5f400ae2368742502`。
- 第一次图片题回放从 Python 标准输入启动，子进程无法重开 `<stdin>`，上传返回 207；这是回放启动方式失败，未产生图片答案。改用文件入口后 A01 通过，原失败目录保留，不拿重跑覆盖。
- 发布前重新跑隔离 Python 全量 **957 passed**、WebUI **240 passed** 和构建。生产镜像以 `--network none` 对新 manifest 运行 `operator_release` 完整性与运行身份校验，通过；角色视图为 23/0（`internal_fae`/`channel`）。生产没有运行模型回放。

## 备份、迁移与切换

生产备份：`/opt/ai-daq-fae-agent/backups/workflow-cutover-20261013T014653Z`，包含旧环境、知识活动指针、源码 symlink 目标、两个服务的容器身份，以及完整 `ai_fae` 逻辑数据库 dump。dump SHA-256 为 `ab4a359341029f573966819f1763206a5d48c4f32f3be9063f147eedf7f0e0a1`，8,883,889 字节，`pg_restore --list` 校验通过。环境备份 SHA-256 为 `0f14b35d74aa1304964782c6361c3fae5bda21cfabf46f323730c336070b98b5`；旧指针备份 SHA-256 为 `e4ebe54bac6c422c2987b1fcf1332581a12e5d30a1e29117d892a92bfaddea1c`。备份在主机受限目录，不进入 Git。

仅对 `daq_fae` schema 应用向后兼容的迁移 `004_task_knowledge_binding.sql`：变更前表不存在，变更后表存在且 0 行；相机 `public` schema 未迁移。切换脚本先核对旧镜像、旧知识指针、新镜像、manifest 和备份，再停止数采容器，在知识发布锁下切换指针，原子更新 DAQ runtime 环境与源码 symlink，按原 Compose 项目名重建**仅数采服务**。脚本在健康或版本校验失败时自动恢复旧指针、环境、symlink 和镜像。实际切换一次成功，未执行回退。

若观察期需要人工回退：先停止 `ai-daq-fae-backend`，将知识指针在发布锁下切回 `a63baf…`，恢复备份的环境与 `current` symlink，然后用原项目 `ai-daq-fae-agent` 和镜像 tag `daq-ace8726f2a74-overlay` 仅重建数采容器；最后核对旧 runtime/release 和相机健康。旧镜像及旧知识目录均保留。迁移 004 是 DAQ 独立 schema 的附加表，回退旧应用时无需删除该表。

## 生产只读烟测与限制

- 数采容器 `healthy`、重启 0；内部 `/health` 为 `production`、`ai-daq-fae-agent`、runtime `daq-49345ccbed83`、knowledge `2f52df9…`。最近 5 分钟数采日志 8 行，未发现异常或错误行。
- 外网 `/daq/` 200；匿名会话、历史和附件能力均 401 且 `Cache-Control: no-store`；公开 `/daq/api/health` 404；Platform 登录页 200。相机首页 200，容器镜像仍为 `sha256:a3415a24298331f3e5c122d00952a1035562cd3c207adc6e92cef961a5a15031`，健康且重启 0。Platform API、loopback、钉钉 stream 容器健康。
- Platform brain 容器在本次检查时报告 `unhealthy`，容器已运行两周；本次没有修改或重启它。DAQ 当前 WebUI/API 试点的身份路径和数采后端烟测通过，但不能由此宣称 Platform 全系统健康。
- 未持有苍渊的登录态验证个人实际提问，也未向生产回答接口发题。当前状态为**已部署待业务观察**，不等于全量知识完成或用户侧最终验收。Platform Task 仍关闭。

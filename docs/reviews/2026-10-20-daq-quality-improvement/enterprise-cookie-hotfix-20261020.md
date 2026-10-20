# DAQ 企业会话失效 Cookie 入口热修复

状态：`released_observing`。2026-10-20 20:14（中国标准时间）完成 DAQ 单服务切换。用户报告访问 `/daq/` 直接得到 `enterprise_session_invalid` JSON；该热修复解决这个网页入口故障，不构成 IMP-06 回答质量门或企业账号端到端验收通过。

## 原因与变更

- 带失效的 `__Host-daq_enterprise_session` Cookie 请求网页时，身份中间件在静态页面渲染前返回 401 JSON。无 Cookie 的同一路径返回页面 200；问题是旧 Cookie 不能进入前端已有的 Platform 重新登录流程。
- 仅在 `daq_fae/webui.py` 对浏览器 `GET /daq`、`/daq/`、`/daq/conversations/*` 的该类 401 清除 DAQ Cookie 并返回同路径 303。API 的 401 和有效会话保持原行为。未修改相机仓库、共享 `src/`、提示、知识正文或前端代码。
- 回归测试先在修复前失败，再在修复后验证浏览器 Cookie jar 跟随 303 得到页面 200、API 仍拒绝无效 Cookie、有效会话仍能打开页面。修复前日志 SHA-256 `31b20636318c3684f2e74e25d3e1729c89a9781527b0a90278b56ed7d84496ac`；最终相关测试 **30/30**，日志 SHA-256 `3e6fa01e5347d280e11738c748996e967be770948426b95de49867b4c73a061f`。证据原件在本机 Git 忽略目录 `tmp/daq-stale-cookie-hotfix/`。

## 版本与生产证据

| 项目 | 切换前 | 切换后 |
| --- | --- | --- |
| DAQ 代码提交 | `eed26212ff9812651df5e954855695e14d9d0409` | `8dcc736a17ecb1de01c9f33cb0693d39740ecb9a`，已推送 DAQ `origin/master` |
| DAQ 镜像 | `sha256:620dfe28e8810c7c393a71151e2cd9a0b20423fa00f1c0bfbe5bd976f81f6b7e` | `sha256:f6844b8fe3735118cb78cab99e3e89648cbb0eb1f70e5cce09fdde04a55b7c19` |
| 运行时 | `daq-3d69fbd5d983` | `daq-5ebdc41f2f88`，DAQ 代码 SHA-256 `5ebdc41f2f882fed0291c2cd2ac799bb5ca5633f0b5a09b9aa732d649ef61d4a` |
| 主知识 | `3bb5a78f698555929fc1144719e02dabc677c78289a33bdbb6b22f904835f776` | `028aa5dd5bcea9422ad4482af8f434315245bf80ef51f4a59027e89cc31a6104` |
| SDK 知识 | `63c9e7094593c800c8e13fb696d0b6b9894fe834afeec380b398f843d2663462` | `a1f59cb38cd6f315f04391adb3b77dc5cb1a73a4c96597408fbb90b2e33e1bb0` |
| 相机镜像 | `sha256:d1f500357c6314cdadcf2c24f1939e92c8153b7a827714ac6553c38ed8d7218e` | 同一镜像，容器健康 |

- 上游固定源码仍为 `6bad94591dc4c40c29ad054b526c0ec526478607`，快照核对通过。新知识版本只重新绑定 DAQ 代码身份；主知识 27、SDK 24、发布说明 17 条内容不变。
- 发布包 SHA-256 `edc78d3a0432911996a4f2450d0fb9cc7c5ef93bbbbc9a17124cc2370a65fe23`。生产预检：新镜像对发布包的 253 个文件 `mismatches=0`；新主知识和 SDK 可加载。
- 生产备份 `/opt/ai-daq-fae-agent/backups/knowledge-20261020T121402Z/` 包含旧源码目标、环境、双知识指针、双镜像身份及共享数据库 dump；dump SHA-256 `ae19b2bc533b1ddd6bc7e3b060dff78f4f66464c2472fe56ce979fa345fae250`。DAQ 专用切换脚本 `/opt/ai-daq-fae-agent/staging/release-20261020-8dcc736/cutover.sh` 在切换失败时自动恢复旧镜像、源码、环境和双指针；本次成功，未触发自动回滚。后续手动回退须使用该备份按逆序切换并验健康。
- 切换后 DAQ `healthy`、重启 0；`/health` 报告上述新运行时、主知识、SDK ID。公网带虚构失效 Cookie 的 `/daq/` 返回清除 Cookie 的 303；用持久 Cookie jar 跟随一次后返回页面 200。相同失效 Cookie 的 `/daq/api/enterprise/session` 仍返回 401 `enterprise_session_invalid`；无 Cookie 时返回 401 `enterprise_session_required`，由现有前端发起 Platform 登录。生产未运行模型评测，也未持有用户真实企业登录会话做登录端到端验证。

这次修复只处理网页入口的失效 Cookie。原发布记录中的回答质量、线上原问与 trace、上游受保护 Git ref 等保留事项不因热修复完成而改变。

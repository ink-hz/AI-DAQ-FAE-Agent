# DAQ 规格增量发布记录（2026-10-20）

状态：`released_observing`。20:48–20:49（中国标准时间）按负责人要求发布本批已逐项核对的参数、概览能力及 SDK 资料绑定。生产只做只读健康与身份观察；没有运行生产模型评测。相机仓、共享 `src/` 和相机容器均未修改。

## 发布内容与身份

| 项目 | 生产发布值 |
| --- | --- |
| DAQ 源码 | `1112fdd05b3f6dfaadf08125509818a68390211a`，已推送 `origin/master` |
| DAQ 镜像 | `sha256:b5d864e7a518da6118c0f1e2775b96c1ae3936f39d4d3848c3ef28e972b35dcf` |
| 运行时 | `daq-b97f57f05418`，代码/提示 SHA-256 `b97f57f05418cbf9957546bc7e9b813cfb059f67fb60ecc65768953826de0478` |
| 主知识 | `1b6b2057f2ae9293204d3a522245d29f0bb1dbedd762e3bd9418a9060b6d2d3a`，65 条（原有 27 + 本批 38） |
| SDK 知识 | `9db9d0bed760ee282e71f4575dc3be9b21f14680a5457389303c42d80b8bf68d`，24 条 SDK 用法、17 条发布说明；内容沿用，绑定新代码与主知识 |
| 上游固定源码 | `6bad94591dc4c40c29ad054b526c0ec526478607`；DAQ `src/` 树与其相同，`1e462897a09c5ba08b92c1dddacdfcf4fef50897` |
| 相机镜像 | `sha256:d1f500357c6314cdadcf2c24f1939e92c8153b7a827714ac6553c38ed8d7218e`，切换前后相同且健康 |

新主知识覆盖 EG-DB 18 项、EGO 1600 变体 8 项、EGO 1920 变体 8 项、EGO Pro 5 项、HUB 4 项、UMI Finger 5 项、UMI 视觉模块 4 项、WristCam 3 项；另有 10 条实体、流程及拓扑记录。规格总览工具按已核对实体返回可见字段，泛称 EGO 不混合变体。冲突或语义未定字段维持不可答，详见 [来源裁决](spec-extension-source-disposition.md)。本批仍是**部分规格覆盖**，不是完整规格书发布。

## 失败、修复与通过证据

第一次候选 `35db6b3cf6d02421c5d98e799b9088c1808d3ab2`（镜像 `sha256:fb967c10709931bcf48a10707fdfc02577486f85d97071294810fae70a7728ce`、主知识 `b6ddd8da1c7384aa799c6c760bee9f4c2cdbb51088ebf9edc1e6c9fdf9d569f0`、SDK `71e1fe24ee0e911c6c8d236c4dba687d69f6dfc79ee4e8ec9c349344399448fd`）在生产切换时启动失败：`ReviewedKnowledge.load_active` 未接受 v4 发布模式，报 `release readiness rejected: readiness_schema`。切换脚本自动回滚到旧 DAQ 源码 `7eeb509`、镜像 `sha256:a0df8feecad7304040a06732afada02ca7804d65f1856ff47bb2cf893354186a`、运行时 `daq-3a3b44810bb8`、主知识 `25dabeaa34d64b8eae49ca714c63adf759c36fe1159736c65128cab3eb971af8`、SDK `9f8624c74d6846c2fa54eeefb63366e88bc5e530687b49e53c8866b19f1172f0`。回滚后 DAQ 与相机均健康；首次备份在 `/opt/ai-daq-fae-agent/backups/knowledge-20261020T124400Z/`，数据库 dump SHA-256 `03f08df496a67df3de782ae00e3988f46c8272e49bee31602298c135cb35d6c3`。

随后补了真实 `load_active` 路径的失败回归，修复 v4 路由；聚焦测试 32 项通过。重新绑定主知识、SDK 和 Dev 复审报告到修复后代码 SHA。第二次候选包 SHA-256 `8f7abb64ab84aa7c03237f5e513b7820cabaeed737ab73bed7dd2e1e2c38c08f`；隔离镜像预检核对 253 个文件，0 不一致，且**真实启动加载器**可读 65 条主知识、24 条 SDK、17 条发布说明。首次缺失的预检路径已加入脚本。

隔离 Dev 模型回答与独立 Codex 语义复审覆盖 EG-DB 宽问、EGO 1600、EGO Pro、WristCam、UMI Finger、HUB 以及 EG-DB 冲突负例。首次 EG-DB 回答因单单元引用过多失败，改提示后只重跑该失败题；HUB 同步上界把严格 `<` 写成 `≤` 后只重跑 HUB。最终通过答案与 trace、流式/历史/来源完整性保存在本机私有 `tmp/daq-spec-extension/`，复审报告 SHA-256 `03200773ee9642ddbf4c40dd21351fbf5f4b49b87f6b02a0cad96c523cacb228`。启动加载器修复没有改回答提示、schema、工具或策略；按负责人“不重跑已通过场景”的要求，已通过答案按依赖分析结转，**不声称修复后代码身份重新完成全套模型评测**。55 单轮、13 组多轮及先前质量门仍以原任务书的独立状态记录为准。

## 切换与观察

第二次切换备份：`/opt/ai-daq-fae-agent/backups/knowledge-20261020T124838Z/`，包含环境、双指针、源码与镜像身份及共享数据库 dump；dump SHA-256 `d7cadde802293d1e8df954a2b30cfd0c691cf4cdfac6f139d6c09c0c2fc5c8e0`，`pg_restore --list` 通过。自动回滚脚本和预检证据留在 `/opt/ai-daq-fae-agent/staging/release-20261020-1112fdd/`。主知识、SDK、运行时环境、DAQ 源码软链与 DAQ 镜像成组切换；本次未触发回滚。

切换后 DAQ 容器 `healthy`、重启 0；内部 `/health` 给出上述三项新发布 ID。生产活跃主知识指针实际指向 65 条记录，逐实体计数与预览相同。公网 `/daq/` 返回 HTTP 200，近三分钟 DAQ 日志 `Traceback=0`、`error=0`。相机镜像未变且健康。健康端点中的旧字段 `source_checked_records=0` 由旧计数键读取，与 v4 主知识记录数无关；应以活跃清单与加载器校验为准，后续修正这一观察字段。

这些检查证明代码、知识和页面已发布且服务能启动；真实用户问题的线上回答效果仍需以同请求原问、答案和 trace 只读观察，不能用 Dev 通过代替线上修复结论。

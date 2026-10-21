# DAQ 企业成员访问与 EGO 变体结论发布

状态：`released_observing`，2026-10-21 09:24（北京时间）切换。负责人要求钉钉企业成员登录后可使用 DAQ。此变更让当前 Platform 目录中的有效企业成员获得 DAQ Agent 使用权，并将其映射至当前正式资料的 `internal_fae` 查看角色。匿名用户、合作方和其他 Agent 身份没有获得该权限；会话仍按本人隔离，`/review` 仍拒绝。该选择意味着所有有效企业成员能查看现有已发布的内部数采资料；未发布 `tmp/`、附件和候选资料不会因此成为知识。

## 问题与修复

- 线上原问“给我详细介绍一下EGO”的只读 trace `e9125c93eb07215db0b6cafa2b1fa22d` 显示：工具已取到 EGO 1600 和 1920 两组核实规格，回答正文也分别列了，但请求账本仍只有泛称 `all_reviewed_specs` 目标，终稿提交 `safe_abstained`，确定性渲染器因此加上“当前无法下结论”。现在泛称产品整体规格目标在已审核、角色可见的变体上拆成独立取证和交付目标；全部满足时提交 `resolved`。具体设备、采购单或验收表要求单一值而变体未明时仍保留缺口。
- 原入口由 Platform 单人 DAQ grant、DAQ UUID 试点名单和单人知识角色文件三层限制。新模式使用 Platform 经审计的 `all_members` grant；DAQ 只接受当前 DAQ Agent 绑定的有效 `enterprise_member`，资料角色文件显式配置 `default_enterprise_role=internal_fae`。旧试点配置仍可恢复。

## 发布身份与验证

| 项目 | 结果 |
| --- | --- |
| DAQ 源码 | `c6e24d3f9693d6c4d4d55a4c25c8c7910854f763`，已推送 `origin/master` |
| DAQ 运行镜像 | `sha256:555703d45562d3d2c46086af7924fa99980b18e94dbbe5299d2b728efd161655`；253 个运行文件与发布包相同，0 不一致 |
| DAQ 运行时 | `daq-02c1e0dba588`，代码与提示 SHA-256 `02c1e0dba588f2afac8426fa2f2f2e1e1af46eba9154d4c52b3273ead6ecde8f` |
| 主知识 | `f0a11a13321d8531442e18b3187f26d4814c32fe5899f41dbcc6d1a686d117df`，原有 65 条内容保持不变并重新绑定代码 |
| SDK 知识 | `66fd7357335236db32ce9a6b583779e7e070f542222dcd4b53f139e567d0cf1d`，24 条用法、17 条发布说明内容保持不变 |
| 上游 `src/` | `6bad94591dc4c40c29ad054b526c0ec526478607`，未修改相机仓或共享运行时 |
| Platform 授权 | `all_members` grant `74649927-6c35-4ad4-9bb4-bea4be542939`，审计事件 `agent_use_scope_granted`、引用 `DAQALL20261021`；原单用户 grant 保留 |

隔离 Dev 用新问题“EGO 的两种分辨率版本分别有哪些已核实参数？”验证同一候选代码：HTTP 200、`resolved`、无 fallback，四项需求均满足，六个交付单元绑定两条实体与 16 条规格主张；回答按版本分别列出测量条件，并明确 1920×1080 帧率未单独核实。独立 Codex 语义复审通过，trace `e173727607e51dcc5aab78cafeade891`，结果 SHA-256 `edc89650aae7713dc6731f124b5ca8f83ec1714aceb206c6341ffbf26d0f4568`，复审 SHA-256 `d21bf745b32a4167a8fa5ecfc3413dd1ec10b6e937d137d7eeee0bb4c072ee98`。此前已通过的原问没有重跑。当前代码的权限与规格定向回归 43 项通过；相关更广静态回归 392 项通过。

生产切换前，新镜像在隔离容器中通过实际主知识和 SDK 加载器检查。切换后 DAQ `healthy`、重启 0，`/health` 回显上述三个 release ID；Platform 只读查询显示当前目录 401 名有效企业成员中 401 名的 DAQ 使用权限为真。以非试点 UUID 对生产权限文件做只读角色检查，企业成员得 `internal_fae`，合作方被拒绝。公网 `/daq/` 为 200，匿名 `/daq/api/history` 为 401；相机入口为 200，相机镜像仍为 `sha256:d1f500357c6314cdadcf2c24f1939e92c8153b7a827714ac6553c38ed8d7218e` 且健康。DAQ 最近五分钟日志无 traceback 或 ERROR。生产没有运行模型评测。

## 回退与未完成观察

DAQ 发布包 SHA-256 `ed89c941c5dd90d4b3209fa0de9246a126b28c9c0a61170d0ef78749df85fcac`。切换脚本与隔离预检留在服务器 `/opt/ai-daq-fae-agent/staging/release-20261021-c6e24d3/`；脚本 SHA-256 `67545b8d19bca76f784045b484db45646907d426dbc5dbdde1717267189f1431`。备份 `/opt/ai-daq-fae-agent/backups/enterprise-20261021T012404Z/` 保存旧环境、权限文件、双知识指针、源码目标和旧镜像身份；旧环境与权限文件 SHA-256 分别为 `2b52d163208e7a7d46e372113937f0477fe0d161b18acf5cd7f1fb7a3d7341f0`、`84a8a37d24d0e07007d6e9acbe956b7e70f9e2821b9b00a8184b65b3d9686f11`。脚本内失败路径会通过审计函数撤销本次 `all_members` grant，恢复旧文件、指针、源码与 DAQ 镜像并验旧健康；本次一次切换成功，未触发回退。若以后人工回退，也须先撤销上述 grant，再恢复该备份并只重建 DAQ。没有数据库 schema 或数采业务数据迁移。

生产尚无真实员工钉钉扫码后的端到端会话样本，也未观察新版对真实线上 EGO 问题的回答。上述发布和只读检查证明运行身份与权限决策已经切换，不等同于用户交互或线上回答质量已验收。

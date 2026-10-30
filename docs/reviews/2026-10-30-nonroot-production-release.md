# 2026-10-30 DAQ 非root生产发布

2026-10-30 10:18（北京时间）完成权限维护切换和只读生产验收；全局发布锁正常释放，持锁unit inactive，原锁文件保留。用户明确授权上线并要求保留其他仓库/会话工作。主线基线5f262708包含其他会话的提问人身份文档和发布工具合并，未覆盖其他工作树或重发相机、行政、平台API/worker。

## 准确版本与权限

当前容器 `9557de1b646fd73073fbd14acbfd56ba30dbf6ad078a841149629cf580d2fbed`，镜像 `sha256:7360ead746ecef1303f27cc85b560ee13807af4a2245c8963f3eef94468586c7`；权限recipe `9439b21b95ded7530e2f4205bf19a53b667b52ac`。实际应用仍为 `2caa860465820d0c4e6c0f42c5a15eca030389a8` / runtime `daq-062f0a7ae744`，两份知识、依赖、模型及业务代码字节不变。

当前实际UID/GID/唯一附加组21081，CapEff/CapPrm/CapBnd为0、NNP=1、根文件系统RO；两份知识和秘密三个RO leaf、附件目录及trace文件两个RW leaf；`/tmp`为256MiB、0700、noexec/nosuid/nodev。保留原网络、edge172.31.0.9、主机loopback8081，未变更Nginx、SSH或数据库角色。

原容器cff5d7f4…/镜像5ed10ead…单次无限grace SIGTERM，10:10:33.742正常退出0；旧PID生命周期和递归cgroup清空，无附件/trace外部写者，PG running0、completed26、interrupted1、27个terminal root全关联且未新增。停后仅已验证的可丢弃缓存层；冷捕获附件0个、trace50729字节，SHA256 `3bc2d5a2f14e3c36f4257a99d4ac5a59b700beb4b2019beac363354fd291c478`。冷材料/render/verify和启动前verify通过后移除准确已停实例，无-f/-v。stop/rm/create/start四个耐久终态ACK0。当前02:10:39.542517528Z启动，宿主PID768002、重启0；首次健康约在原退出18秒后恢复。

## 验收器首错与补验

首次apply验收1失败：误查根路径，并将原始index当作HTTP表示。固定应用实际使用`/daq/`及`/daq/assets/`，index按既有源码注入base和meta。保留 `release-failed.json` 和 `rollback-b-required.json`；B记录要求若回滚保留CURRENTNEW数据，并不要求无条件停止健康实例。

经独立审查C0/I0/M0，仅执行一次只读补验，未重放部署或变更候选：4份原始静态完整集合/字节一致，4资源经本机与公网TLS共8次完整HTTP字节一致，无重定向/非预期编码；3匿名接口分别本机/公网共6次401；2公网health404；健康、成功采集的日志、实际权限和34个非DAQ容器身份/配置/挂载/网络/进程/重启字段通过。补验脚本SHA256 `739b5af11277e8c641e2ba0c6041c74144b102a73254cf842ede8266fe2f0bb4`，结果为V12 PASS（只读范围），不将原apply重写为成功。

## 当前活动数据和后续维护

- 活动回执/Compose根R：`/opt/ai-daq-fae-agent/security-runs/release-20261030T012018Z-03c3d2`。
- 当前权威附件：R下 `captured-rw/attachments`；当前权威trace：R下 `captured-rw/trace/daq_traces.jsonl`。
- RO根P：`/opt/ai-daq-fae-agent/security-runs/20261029T084415Z-d1354e/prepared-ro`，下含 `main`、`sdk`、`secrets`。密钥字节保持，副本owner适配21081，原秘密权限未改。
- 当前Compose：R下 `rendered/runtime.json` 加 `candidate-owner.json`，项目仍为 `ai-daq-fae-agent`。完整工作目录、插值/env引用和实际挂载以R下 `active-runtime-binding.json` 为准。

**R/P均为活动依赖，不得按临时目录清理。** 原data和root秘密目录保留，但旧data不再是活动附件/trace来源。后续备份、发布、知识/授权更新与密钥轮转必须先读活动绑定。回滚须先证明已知当前写者闭合，使用原root镜像 `sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7`、原root密钥、当前新附件/trace；禁止恢复旧整块data或PG。

R下最终 `release-final-status.json`、`release-closeout.json` 和 `production-acceptance-reconciled-v1.json` 为本次实际回执。46份白名单元数据共73645字节已离机校验，未下载真实密钥/sealed env。平台仓同名日期的 `docs/operations/2026-10-30-daq-security-release.md` 记录跨仓完整过程；实际平台/worker身份投影及界面发布保留。

## 范围与未完成项

本地runner22通过、operator修正19通过，分别是定向工程组；沿用原生11门（最新8、合同未变继承3），本次不重跑。实际生产首次验收1失败、修正契约的独立只读批次1通过，原失败保留。

真实模型调用0、生产业务测试写入0。真实员工SSO、浏览器交互、自然业务trace新增及完整模型任务未执行；DB-01仍是superuser/createdb/createrole，需要独立迁移。现有断连daemon pump未join的通用生命周期缺口未修复；本次是源特定安静维护证明，不宣称通用无竞态排空。研发tokens未精确计量，不能称为零。其他Agent的恢复覆盖/常态备份不由本次权限发布推定完成。

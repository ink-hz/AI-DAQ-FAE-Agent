# 数采 FAE 23 条知识内部试点生产发布

日期：2026-10-12。项目负责人同意首批 23 条窄范围内测，取消独立事实/权限签字及发布签名，原件归档留本机；沿用相机的 PostgreSQL 逻辑数据库和 Anthropic 凭据，性能门槛由本项目制定。范围仅为苍渊一名内部 FAE。此记录区分代码发布、知识激活、生产部署和业务验收。

## 发布身份与内容

| 项 | 身份或结果 |
| --- | --- |
| DAQ Git / runtime | `124ae1adc715732d80522b30c1d51f8f67532e0f` / `daq-124ae1adc715` |
| 上游 `src/` | 与相机仓 `bf1ce9af2d467447e39e2be0caae0bb87b1ba689` 的 Git tree 相等；生产相机镜像 build identity `2e4c8e2b95b9fe664b6b9d8620ac9379d8251038` 已核对 |
| 活动知识 | `6a2225fde85cd09339620088011d25bf2331953b6f981be6f1f184ce5537e8f4`，manifest SHA-256 与 release ID 相同 |
| 知识范围 | 6 个实体、17 条 claim，仅 `internal_fae`；无候选章节、软件、链接、拓扑或流程 |
| 原件 | 本机 276 文件 / 2,038,212,224 字节，清单 SHA-256 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`；生产未上传原件 |
| Platform Git | `7bce35f0c334f1028b6f8090b2dcee2d1549606b`，控制迁移 113 SHA-256 `b19e818f1ee67af80ae5433916471ac8a29caa0401a5da3c004af9b64d7b0709` |
| DAQ 运行镜像 | `sha256:42691addf0ff2b5e881c862f99fa096e33b6186cc2e7e838e19e055ce737db57` |
| Platform API 运行镜像 | `sha256:79f8bcd40a04854319575c93f4036898e67bd9da5176a1516be7ae17ff85b064` |

数采发布包源码 tar SHA-256 `25b9cfc2f525ecdf4ca1020420293acb40f4e0cafdeaf0e924414f2392d4c315`。远端镜像中的 226 个 `daq_fae/`、`src/`、脚本、迁移、提示词和 WebUI 文件均与发布包逐字节相等。数采镜像以已核实的线上相机镜像 `sha256:0f227d1c67c09c4bf574b7776d266ddd4a66883779a775143b850bb4af9d2905` 为依赖基底，删除旧 `src/` 后复制本仓固定源码；构建定义 SHA-256 为 `97e421e14d4f743abda3ee9a9cd8083c552065d72f44bd4438a904806eb69f1f`。Platform API 镜像以当时线上镜像 `sha256:043f7ace1d590c74bace81cebcd888bafbf1720bac1ae39ebc07db5a8b193646` 为基底，覆盖当前提交的 app、控制迁移和已构建 WebUI；三类关键文件哈希与发布源码一致。构建定义 SHA-256 为 `2e445c2d9040102d402f4526591d4e3b62845624e13fc1e45ef799596b611e82`。两份构建定义及源码暂存留在生产主机受限目录。

两仓正式全量镜像构建因系统包和 PyPI 镜像下载缓慢而取消，未作为本次运行镜像。当前运行镜像是上述固定基底上的源码覆盖包；之后若换成全量镜像，须再次比较依赖和源码、重跑相关 Dev 门禁及生产只读烟测，不能只替换 tag。

## 验证

- DAQ Python 876 题、WebUI 235 题与构建通过。Platform 相关 DAQ catalog、launch、迁移和云端配置测试通过；Platform 全量测试仍有原有 HR/worker 问题，未宣称全套通过。
- Dev 真模型冻结 24/24 题通过 Codex 独立语义复核：15 `resolved`、9 `safe_abstained`，无回退交付或正文路径泄露。串行 P50 28.46 秒、P95 46.77 秒、最大 56.15 秒。首期观测阈值为 P95 不超过 60 秒、单题不超过 90 秒、最多 2 并发；并发负载未实测。详见[Dev 复核](2026-10-12-daq-operator-pilot-dev-review.md)。
- 生产同一个 `ai_fae` 数据库中新建 `daq_fae` schema，数采迁移产生 18 张数采表；`public.chat_sessions` 仍有原相机会话，数采 `chat_sessions` 初始为 0，`public` 没有数采安装标记。DAQ 环境使用与相机相同的 PostgreSQL 数据库和 Anthropic 网关/API 凭据，但 session/content keyring、entitlements 和数据目录分别生成。
- Platform 控制库迁移到 113，迁移 SHA 与源码相等；临时授予 migrator 的 owner 成员身份已撤销。DAQ 仅有苍渊一条 `user` grant；另外 371 名在职用户的 DAQ scope 为 false。相机原 `all_members` grant 保留。
- 线上 Platform 实际使用 7 份 Compose 输入，包括 HR 和四层页面覆盖；基础云发布脚本在 `CLOUD_DEPLOY_HR_OVERLAY_REQUIRED` 处拒绝替换。已恢复仍被实际容器引用的历史配置包到原路径。受控覆盖只改变 Platform API 镜像/版本及 loopback 对数采地址 `172.31.0.9/32` 的信任，其余 8 个服务定义逐项相等。切换后 API、loopback、相机与数采均 healthy、restart count 为 0；数采容器访问身份入口 `/api/health` 返回 200。发布锁已释放。
- Nginx 从线上配置增量加入 `/daq/`，保留原 office 路由；原文件哈希 `5c56831422fbf435e875d5ef7dd6db81ba9618ae43cc469b4b28b879dea4fcce`，新文件哈希 `723a54ff6d8b044e1b06fdf7f6c8c1440db388a507cc9366df4615387790d749`，`nginx -t` 通过。
- 生产只读 HTTP：`https://agent.orbbec.com.cn/daq/` 为 200；`/daq/api/authenticated/conversations`、`/daq/api/history` 匿名为 401 且 `Cache-Control: no-store`；`/daq/api/health` 和 `/daq/health` 为 404；相机 `https://fae.orbbec.com.cn/` 为 200。数采本地 `/health` 报生产、Anthropic、Platform 身份与正确知识 release。生产没有调用模型。

## 备份与回退

发布前备份位于生产主机 `/opt/ai-daq-fae-agent/backups/`：

- 相机 PostgreSQL：`ai_fae_pre_daq_20261012T0833Z.sql.gz`，SHA-256 `49b78bb9da89cd52d8a7c504676b44cdf1746657dae83cb30ab6ebc2f97fcebd`，gzip 校验通过。
- Platform 控制库：`platform_control_pre_daq_20261012T0833Z.dump`，SHA-256 `1f31cf09a42a6fb8aca7c63dc23519c8d9d827ab09db3eec461eb35401633a18`，PostgreSQL 17 `pg_restore --list` 校验通过。
- Nginx 原配置：`agent-domain-pre-daq-20261012.conf`；Platform 原 API/loopback 容器身份见 `platform-daq-cutover-baseline.json`，受控覆盖见 `platform-daq-api-overlay-7bce35f0c334f1028b6f8090b2dcee2d1549606b.json`。

若停止试点，先从 Nginx 备份恢复原配置并经 `nginx -t` 后 reload，再停止 `ai-daq-fae-backend`，按原 7 份 Compose 输入恢复 Platform API/loopback，最后通过 `platform_control.revoke_agent_use_scope_v29` 撤销 DAQ 的单用户 grant。控制迁移 113 为向后兼容扩展，不需回滚数据库 schema；保留数采 schema 和知识快照待后续核对。恢复前须核对当时线上 Compose 链和镜像，不能用基础 Compose 覆盖 HR/页面配置。

## 未完成验收与已知限制

网页已可访问，但未持有苍渊的浏览器登录态完成个人交互验收，也没有在生产触发真实回答；用户业务试点反馈、并发延迟及全量知识仍待后续。上游 SHA 的受保护持久 Git ref 未核实。生产健康字段 `source_checked_records=0` 只统计 Dev 候选模式；正式 manifest 的 `record_count=23` 已在容器中校验，不能用该健康字段判断知识为空。以上限制不改变首批 23 条内部试点已经部署的事实。

## 2026-10-12 直达登录热修

首轮上线烟测只检查了 `/daq/` 的 HTTP 200，漏掉浏览器身份启动。生产 HTML 把 `fae-api-base` 输出为空，导致浏览器请求 Platform 的根路径 `/enterprise/session`，收到 Platform 的 401 响应后停在“企业身份校验未完成”，无法进入钉钉登录。数采实际 API 经过 `/daq/api/` 反向代理；相机内部页使用对应的 `/fae/api` 前缀。

数采 Git `926667ed07bcfd5007f283a00718b138c5f7729f` 将内部页 API 前缀改为 `/daq/api`，本机 `/app/` 仍使用根 API。回归测试先复现失败再通过，DAQ Python 876/876、WebUI 235/235、WebUI 构建通过。发布包 SHA-256 为 `07dbd36feadcd8e282bd8a847d8f1408bfce2926fdcae762a5d6619b1082972b`；生产镜像为 `sha256:70b351a7a3498b7e5466faa849b2b2173ac33825d1e9658ed67a21890600f5e5`。此变更只修改数采 WebUI 挂载配置，`src/`、提示词和知识未变，故回答运行时标签 `daq-124ae1adc715` 与知识 release `6a2225fde85cd09339620088011d25bf2331953b6f981be6f1f184ce5537e8f4` 保持不变；镜像 tag `daq-926667ed07bc-overlay` 单独标识此次部署代码。

切换后生产 `/daq/` HTML 带 `fae-api-base=/daq/api`；匿名 `/daq/api/enterprise/session` 为预期 401，`/login?return_path=%2Fdaq%2F` 为 200。无登录态的 Chrome 从 `/daq/` 实际导航至 Agent Platform 登录页，渲染出“钉钉扫码登录”按钮。DAQ 容器 healthy、重启计数 0，相机入口仍为 200 且相机容器 healthy。本次未使用苍渊的钉钉身份完成扫码后授权与回答验收。

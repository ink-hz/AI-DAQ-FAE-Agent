# DAQ 提问人身份接入平台

本任务授权端到端修复、测试、补齐可信历史和上线。回答模型、知识及相机仓不修改。

基线：2026-10-30 实际 DAQ 镜像5ed10ead、源码2caa860、容器cff5d7f4；平台API/worker实际镜像71330ae2、运行增量b4c258b4。继承镜像标签28272ec4不是完整代码身份。DAQ源17个会话均有匹配的企业owner/user_id/认证模式。

根因：平台 DAQ 投影不产生 verified_subject；wire契约和姓名解析仅允许相机FAE，UI列表与详情也有相机限定。修复位于平台下游身份投影/授权/展示；DAQ现有写入端从验证后的PlatformSubject保存，已通过真实HTTP+PG账号切换验证，无需发布回答容器。

Dev证据：DAQ新增真实Cookie/CSRF和PG持久化测试1通过（2账号、2会话、3轮）；相关既有49项在首轮通过，新测试首轮仅错误地断言turn表有user_id列，修正测试后独立通过，不将该fixture错误称产品缺陷。问答模型与Platform提供方验证为明确测试边界。

平台新增DAQ测试初始16失败/4通过；前端3项初始均失败。修复后源/身份/副本/worker/store/SQL受影响304项通过；补profile和发布stage后相关158项通过（有重叠，不累加成独立覆盖）。前端5文件23项通过；构建曾因新测试fixture缺3个必填字段失败，修正后构建结果另存。独立真实PG→源读取→worker→加密副本→真实登录Cookie→HTTP列表详情及Agent隔离通过，DAQ真实生产者合成快照2会话/3轮补引用前后其他字段和键全等。缺profile导致整页503的问题经RED/GREEN及独立验证关闭。

发布前核验：5个受影响线上后端文件与平台基线570587fc一致，64个现役静态资源内容与基线构建一致。完整私有失败/通过、独立复审、生产源行摘要和发布材料位于Git忽略的tmp/daq-session-identity-20261030/。

## 实际发布和验收

已上线平台API与原cloud-sync worker：实际发布`c13433f4580a54629626daf9017b642d44bed009`，镜像`sha256:048e90d5bba6303fcb68f08f1ba4f69796295a171f1066c51be0d6af5acc687a`。实现提交为2f539f59；c134还保留并行任务已上线的Agent状态页调整，未覆盖回旧页面。127已应用，旧相机126函数/ledger摘要不变，临时迁移授权已撤销。

实际17/17会话可信引用已由正常云同步补齐；引用逐条匹配源编码，列表、详情和全部26轮姓名一致。原副本去新增引用后的完整payload摘要不变；源17会话、26轮问答、2反馈、0复审逐行摘要前后一致，轮次与会话身份冲突0。无法确认身份的处理在Dev负例验证，当前17条生产历史都有可信owner，没有伪造未知样本。相机内部/外部各类及1个旧未知样本的生产只读观察通过。

234项新旧公开资源HTTP状态和摘要通过；72个运行payload与发布manifest一致；API与worker健康、重启0。33个非目标容器字段及共享Nginx/行政服务guardrails一致；DAQ原回答容器cff5d7f4、镜像5ed10ead保持，模型、知识和上游pin不变。发布锁已释放。

可核验会话：[平台详情](https://agent.orbbec.com.cn/admin/sessions/zxmcvgkeychqpzcdvqjdtluvvgyf2j36ihjugablyjgudaxdyrwa)。实际可信提问人及来源记录保存在私有回执；不把昵称当身份。

生产使用已有有效管理DB会话核对真实repository+授权resolver，即路由消费的数据路径；不是生产登录态HTTP或浏览器验收。真实登录Cookie/HTTP端到端在Dev验证，提供方/模型为测试边界。生产模型评测、业务测试消息及浏览器自动化均未执行。

首候选发布前遇到并行UI切换，保护检查在改库前停止，重新核验最新86f5d9a2基线并保留其组合静态来源后才发布c134。worker重建一次后的后置检查曾拒绝正常Compose replace标签；经严格派生值、独立RED/GREEN及只读完整复核后完成收据，未重复重启健康worker。所有旧失败材料保留。

备份集`20261030T011956089325Z-2da42c88`三个密文校验通过。原API/worker镜像及选择保留；worker重建后API回退需要fresh受审事务，不能强跑旧CID基线rollback，不恢复共享DB覆盖新记录。回滚未实际演练。完整实际版本、验证与回退见相邻平台仓`docs/operations/2026-10-30-daq-session-identity.md`及本机`tmp/daq-session-identity-20261030/retarget-v2/`。

最终复核时，另一Session展示任务基于本次048e90d5发布纯静态层，当前API页面版本为`c6ee87f25e4562ee9ce84a4508491a3a089dbd4e`、镜像`sha256:15ad498db1afce24eb8064b72e64465643141dc6f745a44ebe12398053edc773`。worker仍为c134/048e90d5；5个身份后端文件实际摘要不变，DAQ身份相关3项显示组件检查通过。该后续版本继承本修复，不代表再次修改DAQ回答或重跑已通过的模型场景。

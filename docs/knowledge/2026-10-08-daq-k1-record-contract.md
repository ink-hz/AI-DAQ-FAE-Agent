# DAQ K-1 受治理记录合同

此合同只说明离线 Dev 候选与本地快照格式；当前在线服务仍用 `empty-dev-v0`。原始资料、候选片段、附件和本地软件包都不能因被导入而成为确定答案。

## 来源与更新

每批原件先生成不可变仓外归档及外部记录的 manifest SHA-256。`import` 验证逐文件哈希后生成私有候选快照：`sources` 记录文件身份，`chunks` 记录 Markdown/TXT 行范围或 PDF 页码。图片、固件、软件压缩包、视频及 DOCX 仅记录元数据；空白 PDF 页单独列为待检查。抽取规则变化时提升 `extractor_version`，同一原件的新抽取结果另存，不覆盖旧候选。

对比新旧候选快照时按相对路径和文件 SHA-256 报告新增、变更、删除。旧主张的来源哈希不匹配新快照时拒绝发布；新增来源会提示复审既有 `unknown/unsupported`。涉及同一实体、字段、条件及适用范围的多个已核验值若相斥，或其中仍有未裁决冲突，该字段不可答，新快照发布失败。

## 记录结构

受治理记录使用 JSON 对象，公共字段为 `id`、`kind`、`status`、`scope`、`source_refs` 和 `data`。`kind` 为 `entity / claim / topology / procedure / software / link`；`status` 为 `candidate / verified / conflict / unknown / unsupported`。`id` 是持续沿用的业务 ID，不由文件路径或内容哈希生成。`source_refs` 每项包含当前归档相对路径、文件 SHA-256、`{"kind":"page","page":1}` 或 `{"kind":"lines","start":1,"end":2}`。资产元数据定位只允许 `{"kind":"file"}`，不能单独证明设备兼容。

`claim` 的 `data` 含 `entity_id / field / value / unit / conditions`；冲突记录改用至少两个保留各自来源的 `candidates`，不可挑选一个覆盖。`topology` 显式列成员、角色、连接、供电、平台、同步对象和存储位置；`procedure` 指向已核验拓扑，并保留准备、步骤、检查点和失败分支；`software` 保留实体/修订、平台、连接模式、软件、版本、功能和证据层级；`link` 单独记录交付入口。已核验主张、软件关系、拓扑与流程的实体/拓扑引用必须在同一发布中闭合。

## 双重审核和可见范围

`verified` 和明确 `unsupported` 需要具名 `fact_review` 与独立的 `access_review`。两个审核均写日期与 `record_sha256`；后者还含 `view_roles`、`forward_roles`。当前角色键是 `internal_fae`、`tmall_support`、`channel`，最终资料角色表须由 K-2 负责人签认。`link` 的已核验状态还需要逐页核验的 `link_review`，其 `final_url` 必须与交付 URL 完全一致。

`scripts/daq_knowledge.py fingerprint --records <记录文件>` 分别计算事实、权限和链接审核应绑定的摘要。摘要只是把审核结果固定到具体记录和权限集合，**不能代替审核人判断**。修改值、条件、适用范围或来源后，旧事实和权限审核失效；扩大可见/转发角色时权限审核单独失效。数据所有者和产品/研发负责人可以是不同人。

## 发布边界

`publish` 重新从归档生成并比对候选快照，再做静态校验，写入只读、内容寻址的本机 release。它不自动激活；`activate` 原子切换活动指针，`rollback` 切回已有快照。当前 CLI 只用于 Dev 演练，未接在线工具、Platform 或生产。真实资料发布还需 K-2/K-3 裁决、持久受控归档、K-4 审计与 Dev 独立答案复审。

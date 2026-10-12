# 数采知识来源定位与 D4 题族准备（2026-10-12）

**状态：私有候选核对材料；没有新增已核验事实、冻结题批或真实知识发布。** 活动 Dev 知识仍为 `empty-dev-v0`。本次只处理此前 B2 留下的 102 处历史来源定位差异，并补写 D4 十类具体候选题。

## B2 历史定位差异

对 `original-section-audit.json` 中的 102 处 `context_source_locator_differs`，逐条保留历史记录引用、当前章节引用、原件摘录及摘要，并重新校验涉及的 11 份原件。私有核对包位于 Git 忽略的 `data/knowledge/curated/historical-locator-triage-20261012/v1/`，目录为 `0700`，文件为 `0600`。

| 核对结果 | 数量 |
| --- | ---: |
| 差异项 / 历史记录引用 | 102 / 323 |
| 同来源、行范围重叠 | 149 |
| 同来源、行范围不重叠 | 154 |
| 同来源、页码不同 | 5 |
| 不同来源 | 15 |
| 全部重叠 / 混合重叠与不重叠 / 全部不重叠 / 含不同来源的差异项 | 22 / 51 / 16 / 13 |
| 已签认处置 | 0 |

核对包 `packet.json` 的 SHA-256 为 `f664c532c407b79342691bcead61623e53862376db4ebf991129d5c619482125`；空白 `review.csv` 为 `b65dbe13850bd8c8b05ee4ae9fa59c290207dd2e0106f510c0f64a508023bb6b`；`summary.json` 为 `a7f309aefbbdf1dd8cd6f0dca3293c2f775356c8057cb6dc5dcf591ddd2c8964`。原件摘录和具体路径仅保留在私有包中。

这些是历史 **context** 引用与章节引用的定位关系，并不自动构成 102 条事实冲突。定位重叠也不能证明事实或章节正文等价。原始引用、章节正文和已有审核状态未改变；逐项来源及内容复核仍待完成。

## D4 具体题族草稿

私有 `data/knowledge/curated/d4-20261012/family-candidates-v1.json` 包含十类各一题：`product_identity`、`spec_conflict`、`combination_flow`、`software_links`、`permissions`、`multi_turn`、`evidence_gap`、`provider_http_400`、`provider_http_503`、`update_rollback`。题目共绑定 15 个当前 B3 覆盖 ID；其中多轮题有多个用户轮次。题目仅提出待验证问题与证据边界，不预填产品事实答案。

题包 SHA-256 为 `0cd94c34fc89049a85320e6167f403d98b61af78dea839f590184847d290749d`；摘要 SHA-256 为 `c03eb91d0fea3ac6d4b94326bb1ada3202373fe9d7ba2a0a58855a093c48db72`。当前状态为 `draft`、`frozen=false`、`review_status=unreviewed`，发布 ID 为空，模型回放数为零。需在 D3 实际发布范围内复核题目和预期边界、补齐影响图要求的题，再按 [D4 合同](../knowledge/2026-10-10-daq-d4-dev-evaluation-contract.md)冻结并在 Dev 回放。此前 B3 的 712 道同族缺证草案保持原样。

## 尚余五项知识库收尾工作

1. B2 来源与章节一致性：使用上述核对包逐项复核 102 处历史定位差异，并处理当前内容队列的 52 项裁决、114 节正文/元数据及字段定义；未裁决内容继续不可答。
2. D1 原件保管：在独立受控持久位置复制、重取并验收 276 份原件及访问控制；本机副本不构成完成。
3. D2 事实与权限：苍渊对拟发布事实、章节、软件、链接和角色逐项作内容绑定签认，并完成链接页面核验。
4. D3 Dev 发布：依据已签认范围重建不可变知识包、审计、激活和回滚验证；当前 `ready=false`。
5. D4 真实问答：在发布范围内冻结十类题，只在 Dev 回放，并由独立 Codex 或具名 FAE 复审答案。

这些是知识库专项的收尾包；总项目的上游运行时 pin、Platform 联调和试点验收另按总任务书记账。代码提交或推送不是知识发布或生产部署。

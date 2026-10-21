# DAQ 知识更新维护示例

适用本机知识审核与隔离 Dev。当前已通过身份、未审范围和私有证据哈希见[首批验收报告](../reviews/2026-10-21-daq-knowledge-quality-status.md)。下面的命令是维护入口，不是让维护者重跑本批已通过场景。

## 1. 接收一份变化的资料

先读取实际目标的 `active.json`、主知识/SDK 身份与代码 SHA，检查 `git status`。保留旧原件、抽取、审核和发布目录，用新批次接收新字节。来源路径、SHA-256、大小、文档内版本、页码和授权范围都必须记录；相同文件名不能决定版本优先级。PDF 页码用物理页，印刷页码另记。

新增、改动、删除都先运行既有 `compare_snapshots` / `plan_update`。删除采用明确清单或 tombstone，不删唯一历史原件。字段变更同时查看章节单元、条件、覆盖缺口和 SDK/链接义务；新增资料也要重查相关的有界“无资料”结论。

只需要基础来源盘点时，可用已有 CLI。必须把 `--release-manifest` 指向明确的内容寻址清单，不能拿本机旧指针推定生产。

```sh
PYTHONPATH=. .venv/bin/python scripts/audit_daq_knowledge_quality.py \
  --archive data/knowledge/local_archive/candidate-originals-v2-local-copy-20261010 \
  --release-manifest tmp/daq-answer-template-20261021/main-working/releases/cdd8df9e19b14219635f940947d56a5cee79ddaa967e6ea39255b14b04475629/manifest.json \
  --root . \
  --output data/knowledge/curated/kq-next-source-audit
```

输出必须是新目录。这条命令只制备基础盘点和库存矩阵，不会执行事实复审，也不会自动补完表格行拆分、视觉审核或软件关系验证。后续补充候选要保留其独立输入哈希和转换记录。

## 2. 清洗与复审受影响的内容

对受影响页重做抽取和表格视觉核对，保存抽取器版本及原件、抽取文本的不同哈希。不同版本的抽取器可能改变空白或布局，不以新抽取文本去冒充旧字节身份。

每个段落/表行/步骤建立稳定单元 ID、精确正文跨度、原件引用、精确摘录、依赖记录和范围。硬参数使用 `render_narrative_unit` 的精确渲染；普通解释用独立逐段语义复审。将合并备注保留为其真实语义：原表备注不能无依据升格为用户必须输入的测量前提，也不能丢掉。

事实审核者必须先读原件，再留下逐项决定；权限审核决定与事实决定分开。`unit_fingerprint`、`section_fingerprint` 只计算绑定值，不批准事实。内容、条件、来源、依赖或角色改变后旧审核失效。格式变化也必须被显式处理，不能靠重新填审核人名字继承旧决定。

状态要说明对象和审计范围：库存无记录是 `not_audited`；有来源未审核是 `candidate_pending`；明确冲突是 `conflict_pending`。真正的 `audited_no_source` 要有具名原件复核、精确来源引用、有界审计范围和日期。它仍不能推出 `unsupported`。

## 3. 用已有公共发布器构建本机候选

已审输入 bundle 包含 `snapshot/records/sections/bodies`、审核/权限决定、运行时、实际旧发布 ID、影响与缺口。审批适配器要验证**实际审核决定中记录的精确 payload 指纹**；禁止 `lambda: True` 或脚本自行生成“已批准”。本机授权不要求外部 D2 签名，不能因此伪造签名或持久托管证明。

公共 API 入口如下；`verify_actual_decision` 和 `observe_actual_load_and_tools` 必须来自操作者受控的真实决定/观察适配器，不能是接受任何输入的示例桩。

```python
from daq_fae.knowledge.releases import publish_release, activate_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

release_id = publish_release(
    dev_root, bundle['snapshot'], bundle['records'], bundle['previous_release'],
    {**bundle['review'], 'readiness': bundle},
    sections=bundle['sections'], bodies=bundle['bodies'],
    verify_approval=verify_actual_decision, allow_local_dev=True,
)
# 此时只是不可变暂存；旧活动指针保持原样。
activate_release(
    dev_root, release_id, verify_approval=verify_actual_decision,
    observe=observe_actual_load_and_tools, allow_local_dev=True,
)
knowledge = ReviewedKnowledge.load_active(
    dev_root, verify_approval=verify_actual_decision,
    runtime_release=runtime_release, upstream_sha=upstream_sha,
    allow_local_dev=True,
)
assert knowledge.release_id == release_id
```

本机 bundle 的 `scope.execution_scope` 必须是 `loopback_dev_internal_fae_only`；原件托管声明必须使用现有授权并实际验证全部原件字节；可答记录和章节只允许原先授权的 `internal_fae` 角色。`readiness.scope.domain_evidence` 保留已审字段别名和操作场景保护，目标字段/实体必须能验证。不能混入另一种 `operator_release` 权威信封。

观察适配器要用实际加载器重新读取当前 ID，再执行所改工具，保存工具内容、结构化来源、角色和身份。探针明确标成确定性知识工具检查，不能伪装成模型回答 trace。只执行新增/变更/失败范围及相邻负例，已有通过结果按依赖结转。

## 4. 验证变化、失败及回滚

首批实际演练以六类变化说明更新机制，原件和活动指针全程只读。后续需要验证更新机制时使用新输出目录：

```sh
PYTHONPATH=. .venv/bin/python scripts/rehearse_daq_knowledge_update.py \
  --bundle data/knowledge/curated/kq-dev-20261021-v4/candidate-bundle.json \
  --source-root data/knowledge/local_archive/candidate-originals-v2-local-copy-20261010/files \
  --release-root data/knowledge/curated/kq-dev-20261021-v4/published \
  --output data/knowledge/curated/kq-next-update-drill
```

该脚本优先选择真实章节依赖的参数，验证旧源字节后生成非权威模拟，不修改原件或发布文件。`*-impact.json` 是每场景检查点；`failure.json` 保留异常和前后指针哈希；`report.json` 只在六类图完整后生成。失败输出不能用空目录或“通过”覆盖。若需要恢复，复用已有场景证据，只补缺失部分；尚未保存的纯静态计划可在明确记录原因后恢复，不能声称失败前已有文件。

撤回提案必须列出删除来源支撑的正向记录，依赖章节和有界负面覆盖须同时处理。SDK/链接列出独立重绑/核验义务。模拟文件仍是未审候选，不能拿来发布。

实际回滚使用现有 `release_readiness.rollback_checked(..., allow_local_dev=True)`，验证真实活动加载 ID；如果要恢复候选，再用同一套显式激活 API。构建失败时不应出现新活动 ID，激活观察失败由既有补偿逻辑恢复旧指针。不要手改 `active.json` 或不可变清单。

## 5. 交接及进入完整应用

交接记录至少包含：原件/抽取/正文/审核哈希、实体和条件、每项决定、主知识与 SDK ID、代码/上游身份、实际工具结果、失败/通过证据、旧版及回滚、每个未发布候选的原因。私有索引绑定这些文件，Git 只提交合同、工具及脱敏汇总。

默认认证应用拒绝本机发布模式，不能只把这个目录复制到生产。完整应用先另外构建符合目标环境合同的主知识与 SDK 绑定，完成变更范围内的 Dev 问答和独立答案复审，再走现行发布/回滚程序。来源候选、代码提交、Dev 工具通过和生产生效分别记账。

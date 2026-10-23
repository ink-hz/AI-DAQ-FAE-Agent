# 真实反馈根因修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修复 10 月 21 日五个真实会话暴露的知识发布、目录集合和组合取证缺陷，同步更新 DAQ 工作流，保留准确的资料缺口。

**Architecture:** 前置模型负责提出用户目标与必要证据子目标，Loop 自主取证。程序区分目录集合、精确规格和组合范围，按实际来源绑定交付；知识通过既有不可变、签名/角色发布合同进入隔离 Dev。禁止原问特例、固定产品答案和绕过证据门。

**Tech Stack:** Python / pytest / DAQ EvidencePolicy / reviewed knowledge v2 / pinned shared Loop / Anthropic Dev transport。

## Global Constraints

- 依据 [根因调查](../../reviews/2026-10-23-daq-real-feedback-root-cause.md)，调查基线 `9331d7d6d1e0dbfb6af65de9c52fa25c5cab5e9e`。
- 用户批准该根因修复方向并要求连续执行；沿用指定当前目录。开发分支 `fix/real-feedback-20261023`，保留上一轮未提交调查文档。
- 相机仓只读，共享 `src/` 及 upstream pin 保持不变；如必须改共享代码，先补相机旧域保护。
- 生产只读，真实模型仅在独立 loopback Dev；此次不把 Dev 通过称为生产修复。
- 复用无关已通过证据，只跑本次失败路径及具体受影响邻例；回答由非回答模型的 Codex 独立复审。
- 原件、正文、完整会话和审计批次放 Git 忽略的私有目录。对源缺口不伪造审核、签名或事实。

## Task 1｜目录成员绑定和完整集合交付

**Files:** `daq_fae/domain_tools.py`、`daq_fae/domain_evidence.py`、新增 `tests/test_catalog_collection_contract.py`。

**Interface:** `catalog.content` 返回目标绑定的完整成员；EvidencePolicy 观察实际目录成员集合，所有交付单元的来源并集必须覆盖该目标集合。此合同不允许目录来源替代规格证据。

- [x] 用合成变体/组件目录和真实清单探针写失败测试：无变体选择器也能列出两个变体；过滤目录遵守过滤范围；部分交付/空来源不能通过。
- [x] 运行定向测试保留 RED：`PYTHONPATH=. .venv/bin/python -m pytest tests/test_catalog_collection_contract.py -q`。
- [x] 修改目录目标匹配，单变体规格和身份条件仍保留；为目录提供集合完整性元数据并检查交付集合。
- [x] 运行同一新测试及受影响旧目录/交付保护；保留 GREEN、代码身份和相邻负例。

## Task 2｜通用取证目标与部分交付

**Files:** `prompts/daq_schema_extraction.md`、`prompts/reviewed_knowledge_system.md`、`daq_fae/workflow.py`（仅确有必要的能力可达性修复）、对应 schema/workflow 定向测试。

**Interface:** 模型区分用户求解目标、已给条件、所需输入和证据缺口；依赖规格不得承载另一实体或用户未给的条件。工具可达性不能使前置漏项永久锁死。

- [x] 以原组合续航 schema 冻结取证受阻证据；加与未知同步、非定量组合、身份/服务、附件权限的相邻用例。
- [x] 明确通用定量拆分及“先交付已知输入、未知输出局部保留”协议，不在代码按续航/型号句式插入目标。
- [x] 只修改已证实阻断必要工具的合同；不得把任意相关片段标为 typed requirement 满足。
- [x] 定向测试确认可取证、精确实体/条件、未知规格不被目录或拓扑满足；不运行历史整套模型题。

## Task 3｜补齐来源到发布闭环

**Files:** 私有 `tmp/daq-real-feedback-repair-20261023/` 新批次；必要的通用 `daq_fae/knowledge/` 合同和测试；沿用现有发布器。

**Interface:** 不改历史知识清单，新增事实、章节/拓扑/流程带原件哈希、页/行、对象/单位/比较符/条件、事实与权限审核指纹；新 runtime/main/SDK 成组绑定。

- [x] 从四个相关产品/变体规格表及 EGO＋双 Wrist＋HUB 原指南逐项制作覆盖矩阵，不把额定供电、预计续航、功耗上限、单只和组合混为一谈。
- [x] 制备新记录和组合流程；明确四目指南声明与未完成操作、63mm 主控未核实；保留旧 66 记录和 2 章节。
- [x] 由独立 Codex 对原页、记录、适用范围和权限进行复审；有争议项不发布，继续可独立通过部分。
- [x] 在隔离 Dev 用实际默认加载/角色工具核对新记录可见，缺口保留；检查 source→candidate→release→tool 双向覆盖及回滚目标。

## Task 4｜冻结候选身份、通过证据结转和独立复审

**Files:** 私有定向重放脚本/原失败/修复后结果/审计索引；必要可复用脚本置 `scripts/`。

**Interface:** 五个原会话共八轮保留原语境，按当前候选身份取证；记录完整答案、模型/Provider、目标、工具结果、覆盖、fallback、trace、耗时及实际知识身份。

- [x] 代码和知识定稿后冻结 runtime/main/SDK/模型/提示；确认 Dev loopback 与生产配置隔离。
- [x] 仅重放五个原失败会话，以及英文/集合或数量边界中受改动影响且无可复用证据的邻例。
- [x] 独立 Codex 逐题复核，确实缺原件的题以准确部分交付/局部未知为正确预期，不能要求编出 SoC 或四目同步细节。
- [x] 若失败先查根因，再只重测失败或受修改影响的路径；已通过题不重复。

## Task 5｜工作流、交接和发布就绪状态

**Files:** `AI_DAQ_FAE_Agent_工作流设计.md`、`docs/reviews/2026-10-23-daq-real-feedback-repair.md`。

- [x] 更新完整工作流：目录集合、必要取证输入、部分交付、知识发布覆盖、观测边界和本批验证状态。
- [x] 执行 `git diff --check`、本次有风险的代码定向保护及源码 pin 校验；独立代码复审并修复重要问题。
- [x] 冻结失败/通过证据、精确身份、可发布产物和整组回滚记录。报告资料缺口、Dev 与生产的区别，不宣称未发生的部署。
- [x] 按用户已授权的直接整合偏好整合 DAQ；不写相机仓，不将 Git 推送当部署。

## 实际收尾

- 11 个不同问题均独立复审接受；9 轮按原身份结转，2 轮最终候选新执行。未重复通过场景。
- 真实 Dev 又暴露 schema 纠正结构、主题混排/摘要截断及 clarify 吞目标，已分别红绿、来源重审与失败定向回放关闭。
- 最终 code `dc16f42d8cbe4136efacf2e33d8e78fbb20dc8f9269f6f5623432d887f54ce9c`，78 记录/8 章节/19 个总证据单元。生产未写入，local Dev 签名不具备生产资格。
- 当前复选框为收尾状态；签名引用的原计划字节保留在私有 `implementation-plan-frozen.md`，不改写历史输入。
- 完整身份、失败/通过、相邻负例、独立复审、保留资料缺口、模型回显限制和整组回滚见[修复记录](../../reviews/2026-10-23-daq-real-feedback-repair.md)及其证据索引。

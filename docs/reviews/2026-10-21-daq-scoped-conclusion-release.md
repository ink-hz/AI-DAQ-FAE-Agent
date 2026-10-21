# DAQ 部分有据回答的结论模板发布

状态：`released_observing`。2026-10-21 09:40（北京时间）完成 DAQ 单独切换。用户要求终稿按当前证据直接给出能回答的部分；完全未知也是明确结论。此次只调整 DAQ 的呈现与终稿字段传递，不改相机仓、不改共用 `src/`、不改知识事实或 Platform 授权。

## 根因与改动

共用运行时把 `safe_abstained.missing` 固定渲染为“当前无法下结论”标题，即使同一终稿已经交付已核实的产品事实。DAQ 三个入口现在从本轮 `DaqEvidencePolicy` 取出已校验的结构化 `AnswerSubmission`，仅当最终答案仍与共用运行时的原始渲染完全一致时，按 DAQ 模板呈现：已确认内容进“结论”，具体缺口进“尚待确认”；完全无已确认内容时，具体缺口直接成为“结论”。英文标题为 `Conclusion` 和 `To confirm`。`outcome`、来源、覆盖账本、fallback 和 trace 不变。拒绝、空知识整理、附件失败和系统降级答案不作该变换。

代码复审发现早期按 Markdown 标题切分正文会误认模型文字中的同名标题；已改成只读结构化字段并增加相邻负例。相关静态回归 `102 passed`，包括 Web/API、认证会话、Platform Task、证据门和呈现测试。测试命令：`PYTHONPATH=.:tests DAQ_KNOWLEDGE_RELEASE_ROOT=/tmp/daq-fae-template-isolated-no-active-20261021 .venv/bin/python -m pytest tests/test_api_transport.py tests/test_authenticated_chat.py tests/test_platform_task_adapter.py tests/test_response_language.py tests/test_domain_evidence.py -q`。代码复审未发现阻断项。

## Dev 真实回答与独立复审

最终候选代码仅运行了一道**新**的隔离 Dev 问题：`EGDB_PARTIAL_SPECS_NEW_20261021`，问 EG-DB 的 RGB 分辨率、整机重量和供电参数。HTTP 200，`safe_abstained`，无 fallback；结论先给已核实的 `1600×1200 px`，随后分别说明重量与供电没有已核实数据，并明确未知不等于不支持。RGB 数值与发布记录 `claim:eg-db-rgb-resolution` 一致，交付单元绑定该来源；覆盖账本为一项 `satisfied`、两项 `missing`，SSE 与历史记录一致。独立 Codex 复审 `pass`，trace `59f3c4d61fb714f8130be6f06132e97f`，答案 SHA-256 `1ae8fea2c64f3057546148bfab783afdd32b07455c927abb78bc3e252b23be78`，结果 SHA-256 `7974d1f26225f086e746720e90f6ec45d22db7dc7015fae84bf5bc8eff60beee`，复审 SHA-256 `c5a59490fd1eac7bea12305d258b4362ad018f997b5c7f4da84c919ab882197c`。模型在结论和缺口中重复提到未知字段，属轻微行文重复。

此前另跑过一道 EGO 新题（trace `651042b312958d0473c869b09e94063e`），数值与来源相符且新模板显示正确，但两个规格目标在机器覆盖账本仍为 `unknown`，因模型把数值放在 `basis` 而未交付对应目标单元。这是现存账本/提交问题，未将其计作最终候选的通过证据，也未重跑已通过的场景。当前质量门仍标为 `user_directed_partial_no_repeat`；55 道单轮完整重测未执行。

## 发布身份与只读观察

| 项目 | 身份 / 结果 |
| --- | --- |
| DAQ 代码提交 | `b6b8e3c20bca0462c28174d0e1e660e1d9279388`，已推送 `origin/master` |
| DAQ 代码与提示 SHA-256 | `ef2c4e87f0efe6c167a7d3083d3a3be23b8738fd52ed9b8769851debf05a5b1e` |
| DAQ 运行时 | `daq-ef2c4e87f0ef` |
| 主知识 | `cdd8df9e19b14219635f940947d56a5cee79ddaa967e6ea39255b14b04475629`；65 条内容未变 |
| SDK 知识 | `fa15e496f9528888a7850d706945b0479f9573425e4f3b7b8fac072dbfb19032`；24 条用法及 17 条说明未变 |
| DAQ 镜像 | `sha256:633189d6c47755fb74f6bf195ae4db87efbf99d19f399b6ff231a99d9a6125fa`；253 个运行文件比对，0 不一致 |
| 相机镜像 | `sha256:d1f500357c6314cdadcf2c24f1939e92c8153b7a827714ac6553c38ed8d7218e`，未变且健康 |
| 固定上游源码 | `6bad94591dc4c40c29ad054b526c0ec526478607`，未改 `src/` |

新镜像在隔离容器中通过主知识/SDK 加载器检查。切换后 DAQ `/health` 回显上述三个版本，容器 `healthy`、重启 0；公网 `/daq/` 200，匿名 `/daq/api/history` 401。相机入口跳转登录页后 200，相机容器健康。Platform 当前仍有 DAQ `all_members` 有效 grant；本次未调整它。DAQ 最近日志只有启动和健康/匿名历史请求，没有错误或 traceback。生产没有运行模型评测，也尚未观察真实员工的线上新答案。

## 回退

发布包 SHA-256 `a9daa92e10e77903ff8e198a82a61dde18ae789c4edd5ce4b1c1a21fb17689ca`；切换脚本 SHA-256 `84aefa5d723951741243dae7b657b073b21b23cdb8661994abe4a8d1a0b6b663`。服务器预检和切换脚本位于 `/opt/ai-daq-fae-agent/staging/release-20261021-b6b8e3c/`，备份位于 `/opt/ai-daq-fae-agent/backups/scoped-conclusion-20261021T014039Z/`。失败自动回退路径只恢复 DAQ 旧环境、两个知识指针、源码链接和旧镜像，并验旧健康；本次切换一次成功，未触发回退。人工回退使用同一备份与旧镜像 `daq-c6e24d3-enterprise`，不撤销现有企业成员授权。没有数据库迁移。

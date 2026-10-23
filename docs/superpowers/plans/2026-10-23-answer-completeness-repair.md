# 回答完整性与信息密度修复

> 执行使用 systematic-debugging、writing-plans、test-driven-development、requesting-code-review 与 verification-before-completion。用户已批准“完整交付相关内容、按复杂度展开、限制集中一次”的方案，并授权直接修复、验证和上线，不增加逐项确认。

**目标：** 修复已发布信息遗漏和过度保守表达，让丰富程度来自相关事实、步骤和条件，不来自免责声明或人为加字数。

**基线：** `6707a49`；实际生产源码 `5a55dff`、代码摘要 `772ca8f76d079e42aeadbfcac09ce1a0c41f6e00d390686dab90ca1cd009604b`。生产 main `64a484100fda4e38d519450745700cf978017cb73816019f23f47dd6b57c833c`，SDK `3c00fa1c798aa90894391583b97368125e2e7c1ce2740a77b28485484fb679a9`。

## 已有失败证据和选择

完整问题、schema、目标、答案、工具和来源见前批私有 `DOC-IOS-WIFI-T1`。13 次工具操作中多次搜索已返回“仅支持 WiFi”和已配网设备章节，最终未精读/交付；同章 TF 落盘和预览文字也未读取。`supporting_specs` 总选择第一个同实体目标，导致流程资料挂到 resolve_entity。原文分段没有父子导航，检索还把冗长来源治理字段当正文计分。表达上全局“优先简洁”与多次重复保守规则、basis 在交付前显示共同降低信息密度。8192 输出额度未截断，增加额度没有根因证据。

选择：修复目标绑定、来源章节导航与任务导向表达。拒绝增加字数下限/输出额度、问句关键词补答、自动拼原文补全、放宽文档为实测保证或修改共享运行时。

## 任务 1：来源证据与章节导航

- [ ] 在 `tests/test_answer_completeness.py` 写 RED：同实体 resolve + procedure + limitation 目标，doc support 不归给身份目标，分别可支持实际任务但不满足硬目标。
- [ ] 写 RED：合成说明书含两个平台、同名步骤、跨文档/角色段落；父标题与子章节可导航和检索，导航不等于已读证据，不外泄路径，不跨来源哈希/作用域。
- [ ] 新建 `daq_fae/knowledge/document_navigation.py`：仅消费角色过滤的正式 sections/records，按 exact source hash/path、scope 和行/页定位派生 heading_path、parent/children/siblings/previous/next ID。只暴露来源已有标题和 section ID，不产生答案。
- [ ] 修改 `knowledge/section_search.py`：source_text 用真实 value 检索，父标题可参与语义召回，治理说明不参与正文计分；其余已审事实检索不变。
- [ ] 修改 `supporting_evidence.py`：文档参考绑定所有匹配的实质交付目标，不被身份/目录目标抢占；原来源、角色、变体、satisfies_parent=false 门保持。
- [ ] `domain_tools.py` 接入导航，搜索时简化重复治理元数据，精读仍保留完整 scope/limits。`domain_evidence.py` 继续从可信记录重算支持，导航不给覆盖或满足状态。
- [ ] 运行 `PYTHONPATH=.:tests .venv/bin/python -m pytest -q tests/test_answer_completeness.py tests/test_document_references.py tests/test_supporting_evidence.py tests/test_section_tools.py`，保存 RED/GREEN、相邻负例及独立代码复审。

## 任务 2：完整交付与呈现

- [ ] 写 RED：DAQ partial 显示先交付已知内容，再呈现依据/边界；保留 gap/outcome/sources，不拼补或删除模型语义，不改 `src/`。
- [ ] 修改 `daq_fae/response_language.py` 的结构顺序，修改 `prompts/reviewed_knowledge_system.md` 中统一表达合同：按问题子目标完整交付，复杂流程覆盖准备/操作/检查/限制；跟随来源导航查相关子章节，禁止靠重复关键词搜索判无资料；不机械输出无关产品身份。
- [ ] 限定只说影响用户目标的缺项；资料查询不主动扩成实际兼容验收。缺少结构化字段不抹掉来源原文。共用版本/来源边界集中一次，文内特定冲突/条件随事实保留；不靠长免责声明增字数。
- [ ] 统一现有重复的 partial 交付说明，消除 basis 与 delivery_units 位置冲突。保留硬事实/用户条件/附件/错误类型及已知风险规则。
- [ ] 定向测试与独立审查后固定代码摘要，不扩大模型额度，不修改相机或共享 pin。

## 任务 3：局部真实验证与生产

- [ ] 仅隔离 Dev：重测此前 iOS 完整性交付失败；新增另一平台英文导航负例、资料陈述与实际兼容的多轮区别。最多 3 组/4 轮初始执行，仅失败重测，其他已通过历史题复用原身份。
- [ ] 独立 Codex 审查信息覆盖、来源忠实、平台/变体不迁移、无虚假认证、相关限制集中、简单问题不灌水；字数/重复率只诊断，不代替语义判断。
- [ ] 源文、事实、角色保持原字节，按新 runtime/code/main/SDK 绑定重新独立签名；使用非 Dev 默认加载器校验。
- [ ] 单 DAQ 生产切换；新 main/SDK 根、完整 image/env/source/指针备份，保留可审回滚；生产只读身份/健康/公网/相机边界观察，不模型评测。更新工作流与发布记录，真实剩余项如实保留，不以 Dev 通过冒充生产修复。

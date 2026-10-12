# DAQ 工作流修复前基线（只追加后续结果，不改写本节）

2026-10-12 本机 Dev。DAQ `aefdb26b1b6d9e788262ef5a1cf070c01c516265`；相机 `4f453b77073005d24d9f07f36016b7172009443a`；两仓 `HEAD:src` 均为 `e69021117158cbdc7e6d75220c5247fe631bbe58`。相机工作区原有未提交改动，本轮不触碰。DAQ 活动知识 `a63baf103892250b8474ad9e464b8df47c889e020313e37dd71ad815af9d55a6`，manifest SHA 与 release ID 相同，format 2、23 记录（6 实体、17 claim）、0 章节；`internal_fae` 可见 23 条，`channel` 可见 0 条。

DAQ prompt `prompts/reviewed_knowledge_system.md` SHA256 `e00bd0b10ddb977624b15f0f1714ae0f3981dafcb9f82fee3308083103a92db7`；上游 pin 清单 SHA256 `9fb15a6f547d4cce726d1a05a7cc5af158d2177f1278fb373048fb41e56e9a43`。Dev 模型配置由 `daq_fae/app.py` 使用 `DAQ_ANTHROPIC_MODEL`（默认 `claude-opus-5-5`）、adaptive/high、`submit_only_auto`；未记录密钥或其值。冻结新题集 `evals/daq_workflow_20261012.jsonl` SHA256 `ad7735f26efb65f1d390d907d38645c7d988bd75aaa57461cdd5452383190916`。旧 24 题保留在原 Dev 评测资产，不改写。

修复前四个新合同在旧代码下 **4 failed**（命令：`DAQ_KNOWLEDGE_RELEASE_ROOT=/tmp/daq-fae-empty-release-test-20261012 .venv/bin/python -m pytest -q tests/test_task_context.py::test_service_identity_is_not_planned_as_missing_product_evidence tests/test_task_context.py::test_service_and_product_request_keeps_two_independent_goals tests/test_reviewed_knowledge.py::test_records_only_active_release_is_searchable_without_sections tests/test_authenticated_chat.py::test_authenticated_stage_lifecycle_is_closed_and_persisted`）：

1. `介绍一下你自己` 实际 `planned_capabilities=('search_knowledge',)`，预期为服务目标与服务合同。
2. 服务加 EGO 规格的混合问题实际只有 `lookup_spec`，丢失服务子目标。
3. 活动 23 记录/零章节 release 的 `search_knowledge('EGO')` 实际 `not_found`，预期命中有权记录。
4. 认证 SSE 的 `loop=running` 无任何 completed/error，且 `ChatTurnRecord.stages` 只存工具调用。

用户提供的线上样本：`介绍一下你自己` 显示“当前无法下结论”、尝试产品目录与服务范围检索、`search_knowledge: unknown`，页面呈现 `answered · no sources` 和 `loop running`。这是生产反馈，**没有**在生产执行模型评测。

隔离测试根目录无 `active.json`。从指定六个核心测试文件排除四个新失败合同运行旧合同：**98 passed, 4 deselected**（3.69s）。完整命令见任务书 R0；测试根目录单独设置，避免默认应用在测试收集时误读活动 release。

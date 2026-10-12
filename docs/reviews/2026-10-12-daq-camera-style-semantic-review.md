# 数采 FAE 真实 Dev 回放与 Codex 逐题语义复审（2026-10-12）

## 结论

按相机 FAE 的 Development 验收方式，在**最终数采代码、提示词和来源账本**上重放预先列好的 17 轮题：**17/17 自动硬门通过，17/17 Codex 逐题语义通过，0 fallback，0 Provider 传输重试**。9 道已核对规格题给出正确型号、字段和值；8 道变体歧义、缺证、冲突和上下文题保持安全边界。Codex 与作答模型 `claude-opus-5-5` 分离，逐题阅读完整正文、结构化来源与 trace；没有让作答模型自评，没有向生产发送评测请求。

这只证明当前 **9 条事实、内部本机 Dev** 范围的问答质量。相机 FAE 另有 50 题 L0、广域泛化和正式发布前门禁；数采的认证角色、软件/链接正向交付、真实组合操作、Provider 故障题、知识更新/回滚及正式 D4 十类冻结题批仍未完成，不能据此宣布数采知识库达到相机全量标准或已上线。

## 回放身份和原始证据

- 数采代码基线：`59669ea` 加本轮变体范围提示、Mono 基线字段别名与提示词修复；该增量的代码/账本/提示词拼接摘要：`68c07aa7444e081a993ffeeae5eaeb6f3ea0186c53a34a3614c192168b7ff1af`。
- Dev 知识 release：`candidate-dev-6fba79587651f5b5c29b26a84019dc1c0b6ac7d96e00c3455459dd8fc2f4e792`；真实 `/health` 与 17 个 `/chat` 终态一致。账本 SHA-256：`3a0c513da28307c227268e0cd3913e3f1f579ebbc66a0d7710b9ef5b1e584494`。
- 环境：本机环回 `TestClient` 调实际数采应用，`environment=development`，`DAQ_DEV_CANDIDATE_MODE=true`，内部草案角色；Provider 实际回显每题均为 `anthropic/claude-opus-5.5`，`complete=true`、`consistent=true`。正式 `active.json` 仍未切换。
- Git 忽略的受限原始目录：`data/knowledge/curated/camera-style-final-v3-20261012/`，目录 `0700`、文件 `0600`。预注册 17 题的 canonical SHA-256 为 `a8f75d78031ba861fb29e3a27c18263ac0cfb4a35a848d950bb8bdfd1dd76e40`；完整逐题 SSE 与终态 `case_results.jsonl` SHA-256 为 `fe5dc256aaf0b3494cb3868c5188149de1c5f86f214c212625119602c49b0fb6`；根 trace 日志 SHA-256 为 `3ddc6a48f45eb5c8fa5ec94c4b07a3789a1d9d0ca83a30ed36049646f5bb66c3`。
- 自动硬门逐题校验 HTTP 200、精确 outcome、结构化终稿、版本与 Provider echo、无 fallback/重试、本地路径不进入正文、trace；正向题还要求对应 `source_checked` 规格来源、完整能力覆盖和值，缺证题要求无未经审核 URL。结果 `17/17`，`hard_gate.json` SHA-256 为 `9807c97a81d0dff88277b9bf3e4930b3f0b7fe8a3b5a13af3d169f23167791e1`。
- Codex 语义复核逐题绑定原始回放行摘要、答案摘要、release 和 trace；`codex_semantic_review.jsonl` 为 `17 pass / 0 fail`，SHA-256 `d8d8c36f46641011631d7382d9e424e12baf1398b06d6a206035eb556c9ab43c`。这是本机独立阅读记录，不是假冒可信签名的正式 D4 审批。

## 最终逐题判断

| ID | 终态 | 耗时 | trace | Codex 语义判断 |
| --- | --- | ---: | --- | --- |
| `ego1600_hfov` | `resolved` | 11.4 s | `31198f5c202339e775f12ad9116ead01` | PASS：1600×1200 的 165°，不跨变体 |
| `ego1600_vfov` | `resolved` | 9.2 s | `50234c3bc1290837fa2b2d50a98eaf5a` | PASS：1600×1200 的 130° |
| `ego1920_hfov` | `resolved` | 13.5 s | `1ba9060097b39c34ade886cd28270b45` | PASS：1920×1200 的 149° |
| `ego1920_vfov` | `resolved` | 8.9 s | `f2f4533f6e98e788a33f810cfc7a9583` | PASS：1920×1200 的 100° |
| `egopro_rgb_count` | `resolved` | 8.1 s | `484ce7b52b63a1568654a9cb13ad9100` | PASS：EGO Pro 为 4 颗 RGB 相机 |
| `umi_finger_rgb` | `resolved` | 8.8 s | `c0a161557c4d2d37828b220ee4dfd59b` | PASS：UMI Finger 鱼眼 RGB 为 1600×1200 |
| `umi_finger_mono_baseline` | `resolved` | 8.4 s | `0fbc5de10184be37a4b62b0a9a8e8b4e` | PASS：UMI Finger 双目 Mono 基线 50 mm |
| `umi_module_rgb` | `resolved` | 9.7 s | `c6a7f08b66308f0e4b2ccbf054b9d4de` | PASS：UMI 视觉模块 RGB 为 1600×1200，未冒充组合指标 |
| `hub_port_count` | `resolved` | 8.6 s | `77ae482fb33cd3d88ec9fa94bfb8f2d9` | PASS：数采 HUB 4 个下游端口 |
| `ego_ambiguous_variant` | `safe_abstained` | 9.6 s | `5ea8875874ff35c6bc14b27c78133541` | PASS：说明已有两个按变体核对的范围，先问用户变体 |
| `ego1600_rgb_gap` | `safe_abstained` | 29.5 s | `201f60c381242ab498095ee8592cc190` | PASS：未把型号分辨率冒充 RGB 分辨率 |
| `egdb_depth_gap` | `safe_abstained` | 14.6 s | `4e2619c35b45ae62610ff2ab355d41f5` | PASS：未把 Gemini 模块精度当整机精度 |
| `combo_flow_gap` | `safe_abstained` | 16.0 s | `40ca94b2ec36c687408583a52859fff0` | PASS：未交付未经核对的组合操作步骤 |
| `software_link_gap` | `safe_abstained` | 17.7 s | `dea1c9f201621ee0ae1998baa4fae36d` | PASS：未编造 Viewer 版本或可转发链接 |
| `wristcam_fov_conflict` | `safe_abstained` | 16.2 s | `175d52b576676cd713d9357e5a6cce69` | PASS：候选冲突未变成确认值或范围 |
| `multiturn_setup` | `safe_abstained` | 8.0 s | `1267e326e25e77ced6b566ba50d76fbe` | PASS：只确认用户陈述并询问目标 |
| `multiturn_followup` | `safe_abstained` | 19.9 s | `d563cbab120490a6d66fdd6657d9c719` | PASS：保留上轮组合，不外推 Windows 11 软件或落盘步骤 |

## 失败、修复和复测留痕

最初 17 轮的程序硬门是 17/17，但 Codex 读正文发现泛称 EGO 题错误声称没有已核对记录，实际已有按 1600×1200、1920×1200 变体核对的事实，属于 **retrieval / synthesis**。工具现只返回可选的已核对变体范围、不返回值，模型重新回放后正确要求用户先选变体；旧错误 trace `378afc1fee578d9c0726e6974b387f0d` 保留。

下一轮暴露 UMI Finger Mono 基线字段在 `mono_stereo_baseline`、`mono_baseline`、`stereo_baseline`、`baseline` 等模型写法之间漂移，漏检后误说没有已核对记录，属于 **schema / retrieval**。账本补了通用别名并用真实模型单题预检，最终整批再次正确取到 50 mm；旧失败 trace `6a1d3a2e9cc3b11bf7561d4d688be301` 保留。只陈述组合的首轮曾输出冗长缺口清单，提示词改为简短追问并做两轮真实复测。三批原始结果均未回写覆盖。

相机 FAE 的相关先例见[50 题 L0 复审](../../../AI-FAE-Agent/docs/reviews/2026-10-08-l0-release-codex-review.md)和[已确认反馈泛化复审](../../../AI-FAE-Agent/docs/reviews/2026-08-27-fae-confirmed-feedback-remediation-codex-review.md)：保留原始失败、在最终行为版本重跑受影响题、分开统计自动门和 Codex 语义门。本批沿用这一口径，但题量和产品覆盖远小于相机 FAE，不能代替数采正式 D4 或部署验收。

最终工程回归：Python 全仓 `841 passed`；WebUI `235 passed` 且构建成功；新增加载器与测试的 Ruff 检查、`git diff --check` 通过。未改共享 `src/`，因此本轮没有改变相机 FAE 行为。

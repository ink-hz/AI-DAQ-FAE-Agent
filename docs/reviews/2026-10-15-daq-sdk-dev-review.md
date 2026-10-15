# 数采 SDK 首批事实与 Dev 答案复审

日期：2026-10-15。按相机 FAE `Knowledge_SDK` 的事实记录、源码定位、索引、覆盖审计及代码输出分层做法，为数采增加独立的 SDK 用法证据层。相机库有跨 C/C++、Python、ROS、.NET 等多层的 117 条记录；本轮数采只审核了明确列出的两个 SDK 包范围，不能视为完整覆盖。

## 来源与范围

- `1015/开发工具包` 原件已在本机受限归档中按文件哈希保存：143 个文件、6,826,174,127 字节，归档清单 SHA-256 `56b3c4411956290c3d9bc1dacbccc228e97f489feae649f9389a8a09303bd211`。原件和回放数据均被 Git 忽略。
- [SDK 目录](../../Knowledge_SDK/README.md)含 15 条审过的用法记录和 42 处包成员/行级来源；目录事实摘要 SHA-256 `220a7aa608a21877b883dac342549ae24f7e4060803c68c50ff37961bb03daaa`。涵盖 OrbbecSDK 2.9.0 的 EGO Linux x86_64 Host C++ 构建（20260921、20260923、20260929）与 libobkit 20260909 RK3576 AArch64 设备端 C 包。
- `sdk_evidence` 只返回源码核对过的 API、示例和编译用法，设备支持字段保持 `unknown`；具体设备、变体、固件、镜像和平台的组合仍交给精确的 `check_software_support` 证据。未核实的包内示例不提升为兼容结论；没有验证过的完整代码块也不输出。
- [更新审计](../../Knowledge_SDK/updates.md)按原件哈希、构建日期与 tar 成员的新增、删除、变更发现候选。改变过的源码需重新核对行级证据并重跑 Dev 问答复审。Android、BLE、Viewer 和其他 Host 平台尚未形成可回答记录，见[覆盖缺口](../../Knowledge_SDK/audit/coverage_gaps.md)。

## 真实 Dev 回放与独立语义复审

真实 `claude-opus-5-5`，隔离 Dev `/chat`、企业身份、PostgreSQL、已发布主知识与 Dev SDK 候选视图；未调用生产回答接口。固定题集为 [六道 SDK 题](../../evals/daq_sdk_dev_20261015.jsonl)。最终结果在 Git 忽略的 `data/knowledge/curated/sdk-dev-20261015/final-v14`，`results.jsonl` SHA-256 `b2ce0d901003f191a62f686d0a4bad6dbc735912cdc886b7cb7439c29c354784`，DAQ 代码摘要 `05710888a52cdbcbbb82d2ef3fcbedb638870cd095553ece9a1cb7d5b6f3df69`，上游固定 SHA `4f453b77073005d24d9f07f36016b7172009443a`。逐题答案摘要、trace ID 与审查意见保存在同目录 `independent-review.json`。

| 题目 | 终态 | Codex 对照源码的复审结论 |
| --- | --- | --- |
| `SDK_HOST_SYNC`：EGO 主从同步 | `resolved` | 读写权限、`uint16_t` 模式位图与设置条件正确；示例固件提示未写成兼容认证。 |
| `SDK_HOST_CALIB`：标定 YAML | `resolved` | `getRawData` 分块回调与 offset、两个文件名及不解析 YAML 的边界正确。 |
| `SDK_DEVICE_BUILD`：libobkit 编译及任意镜像 | `safe_abstained` | 工具链、RPATH、整机镜像强绑定来自 README；拒绝任意镜像兼容结论，也未认证“四目 EGO”身份。 |
| `SDK_DEVICE_CAPTURE`：相机/IMU/音频采集 | `resolved` | 初始化、事件注册、三路配置、启动停止和退出清理顺序与示例一致。 |
| `SDK_NEGATIVE_FW`：EGO 固件 1.1.8 | `safe_abstained` | 无精确组合支持记录；1.1.9 只是示例前提，未建议未经核实的升级。 |
| `SDK_OLD_BUILD_SDCARD`：9 月 23 日 SD 卡 API | `resolved` | 公开头文件和示例共同证明包内 `downloadSdcardFile`，没有推广为设备实测支持。 |

六题均 HTTP 200、`fallback_used=false`，每题的 stage 生命周期、SSE/历史、trace ID、终态和来源 ID 五项完整性核对均通过。英文开关另以 `SDK_HOST_CALIB` 在 Dev 重放：`en-US`、`resolved`、无 fallback、五项完整性通过，答案全英文且保留同样的用法/兼容边界；结果 SHA-256 `6e34cc10bcdb3e771bab2fac4fe6b52e5698052c876ef940df34946b11d4df0b`。

诊断回放中曾发现返回类型、设备端清理顺序、旧构建 API 识别和固件升级建议的错误，已按源码修正并以这组六题终稿复测。发布门又补了回放运行时与上游版本身份核对，对错误身份有失败测试，最终六题是在该代码上重跑的。

## 验证与发布状态

最终代码下 Python **1125 passed**；WebUI **246 passed** 且构建通过；相机固定上游 `src/` tree 校验通过，未修改共享相机运行时。独立答案复审报告已用于**本机暂存**不可变 SDK 发布包 `6cffd5a0e42aad4293a3548f7fa0adbab9a462c246ec1d502de9c539c98148b7`，未切换活动指针。生产 `/daq/` 仍为原运行时与主知识版本，本轮 SDK 不在线上。若要接入生产，需连同匹配运行时和受控发布包部署、执行线上只读烟测与回退核验；本轮 Dev 回放不等于生产部署或设备实测兼容。

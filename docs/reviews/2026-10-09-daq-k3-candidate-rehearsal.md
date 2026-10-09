# DAQ K-3 软件与交付证据候选包本机演练

**日期：** 2026-10-09。**范围：** 已逐文件校验的同机受限归档、extractor 1；无在线知识发布。归档清单 SHA-256 为 `e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`；K-3 配方版本 `k3-20261009-2`，SHA-256 为 `968c0c5eca0dea8584ffcd2918af8dc78dd52b16d45becc9cb1945ec4db1c330`。

私有包位于 Git 忽略的 `data/knowledge/review/<归档清单哈希>-k3-20261009-2.json`，183,543 字节、权限 `0600`，文件 SHA-256 为 `b3c201fc7dfee8f1f19312b63cf67ee0fb515ff1eada3381fcb66c6abd8e5f24`。它含有原件路径和短摘录，不提交 Git。

- 276 个来源形成 15 个待审议题；软件/固件目录的 30 个二进制资产均被文件级选择器定位，6 份变更记录被文本选择器定位；该目录的 `.DS_Store` 留在未映射列表。
- 15 个议题中有 2 个选择器缺少来源：外部官网逐页记录和固定版本的相机模块事实投影。全归档仍有 238 个未映射来源，主要是 K-2 管辖的规格、指南和图片；未映射不等于可忽略。
- 原件自称密级为 5 份 `confidential`、9 份 `public`、262 份 `unknown`。每份资料对内部 FAE、天猫客服和渠道的查看与转发授权仍均为 `pending`。软件兼容、固件升级、下载链接均未核验。
- 官网的 [Robot Free Data Collection 产品页](https://www.orbbec.com/robot-free-data-collection/) 和 [2026 年产品新闻](https://www.orbbec.com/news/orbbec-unveils-two-new-product-lines-at-wrc-2026-advancing-scalable-physical-ai-data-collection-and-human-like-robotics-vision/) 可作为外部来源采集候选。当前未封存到受控归档，也未裁决 Quad-EGO 与 EGO Pro、RGB-D EGO 与 EG-DB 的 SKU 映射。该产品页注明展示形态与实际产品可能不同；官网描述与原包规格不一致之处仍需逐产品修订审查。

**下一门：** 产品/研发逐组合实测和事实裁决、资料所有者/渠道角色授权、链接逐页核验、相机模块固定投影、原件持久受控存储、K-4 发布审计与 Dev 独立答案复审。`empty-dev-v0` 未改变；本包没有修改相机运行时或知识。

**工具验证：** 相同输入重跑得到同一文件 SHA-256，输出权限仍为 `0600`；后端测试 `214 passed`，上游源码快照校验通过，`git diff --check` 通过。独立代码复审发现文件选择器会被父目录名误触发；增加失败测试后改为文件名匹配，真实包的哈希和统计未变。未改动共享运行时，因此本批无需重跑相机模型质量门。

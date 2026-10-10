# A2 产品候选章节清洗审计

日期：2026-10-10。任务状态：**候选已备**。事实审核与权限审核待苍渊逐条裁决；本次没有知识发布、激活或生产部署。

## 私有产物与输入

在主工作树 Git 忽略的 `data/knowledge/curated/a2-20261010/` 制备八个稳定产品实体的 31 个 Markdown 文件、66 个 H2 章节。每个实体均有 index、product、hardware；其中七个具有真实软件声明/资料条件的实体另有 software，HUB 的软件缺口在 index 明示。

正文为产品定位、硬件规格、适用限制、软件声明与数据格式，未混入 Agent 使用流程、下载 URL 或本地来源路径。来源和依赖保存在独立私有机器索引。目录权限为 `0700`，文件为 `0600`；正文、原始抽取、制备与检查脚本均不提交 Git。

输入清单 SHA-256：`e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`。沿用 A1 来源处置和 K7 稳定记录；未覆盖或修改旧候选。实际逐位置重读十五份原件，核验文件哈希，并以 Poppler 重新提取三十五个精确页/行位置，保存原件位置和新抽取哈希。另目视检查三页涉及合并单元格、外部供电条件和比较符的规格表。

## 章节合同及验证

私有 `section-index.json` 对每节保存稳定 ID、实体 ID、变体范围、修订/软件版本未确认状态、条件、精确来源与 SHA-256、正文哈希、K7 依赖以及本批稳定候选依赖 ID。新增依赖是 B2 待逐字段归并的私有章节主张草稿，不冒充已通过 K1 合同的类型化事实。

全部章节保持 `review_status=candidate`，事实/权限签认留空，可见/转发角色均为空；角色草案仅建议内部 FAE。七节显式保存冲突或限定语差异，其候选值及来源在私有正文与索引内保留，没有自动裁决。旧 K7 的冲突和未决条件仍有效。

静态检查通过：八个实体入口齐备、章节 ID 唯一、每节正文哈希匹配、每个引用精确存在、依赖 ID 可解析、相对目录链接可解析、无 URL/本地路径/Agent 流程正文、访问权限正确。数字存在性检查无告警；该机械检查不代替事实及适用条件复审。

产物指纹：

- 章节索引：`d86b13baee11d735392dfd4f69398df30dde9a745664031602e2cccedc513cc1`
- 重抽取审计：`d021290849ee6303d0d26e05ae9134ab7a3ece7d14554a3ce8fd459114744637`
- 静态验证报告：`4135b43a0e4b7516b8e20548d2cd265ed6151b1ae4c5ec2b6ba242d41523bd73`

后端完整测试使用已有 Python 3.11 环境执行 `python -m pytest -q tests`：**267 passed，6 warnings**。最初系统 Python 3.9 因语言版本及缺包无法收集测试，随后使用项目兼容环境通过；未通过改动运行时代码掩盖环境问题。本批仅提交本脱敏审计，没有新增运行时代码。

## 材料缺口与交接

型号映射、版本兼容、模块修订、原件内部/语言版本差异及部分待定电气参数仍待裁决。软件内容仅为有来源的文档声明；无完整软件验证矩阵的产品明确保留缺口。产品规格没有推导组合性能；泛称 EGO 的指南没有复制为两个变体的确定操作流程。

B2 需将新增章节主张逐字段与既有记录对齐，并建立跨章节条件/冲突的强一致性；C3/C4 需核验软件和交付入口；D2 需具名签认事实和角色。受控持久归档与独立恢复验证仍为真实发布依赖。此候选内容不能直接进入线上知识视图。

## 独立复核后的追溯修正

独立复核发现，部分指南边界断言误引规格书，外部型号映射措辞也超出了本批实际核验范围。已为 Pro 两个入口/边界章节补引快速指南场景表的精确行；双目变体对应章节补引快速指南和详细说明的适用产品声明行。全文适用矩阵与外部 SKU 映射改成“本次未核验”，没有宣称完成跨来源缺失审计。

私有生成器、验证器及索引已同步更新。新增回归检查约束指南行引用和未核验映射措辞；重新制备后逐节哈希、来源、候选状态、空审核字段与角色限制均通过。没有新增审核签名。原有完整后端测试结果仅适用于上述代码基线，本轮仅改变私有内容及脱敏审计，未修改后端代码。

## 修正批次的确切验证命令与实际输出

以下两项在追溯修正后重新执行，退出码均为 `0`。没有重跑 pytest：本轮只改私有内容、生成器/检查脚本与审计，前述 `267 passed` 是初次制备批次的完整后端结果，不冒称本轮测试。

章节、权限与追溯回归检查：

```sh
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a2-20261010/validate_private.py
```

实际输出：

```json
{"checks": "section identity/body hashes/source locations/dependencies/permissions/relative links/no URL or local-path or Agent prose", "products": 8, "markdown_files": 31, "sections": 66, "numeric_presence_flags": [], "review_regression_checks": "guide line references and bounded unverified mappings passed", "fact_review": "pending; mechanical verification is not approval", "online_eligible": false}
```

逐个原件哈希、全部精确定位重读及抽取哈希检查（命令只输出数量和结果，不输出资料内容或原件路径）：

```sh
python3 - <<'PY'
import hashlib,json,subprocess
from pathlib import Path
root=Path('/Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a2-20261010')
archive=Path('/Users/neo/Developer/work/AI-FAE-Agent-local-archive/daq-candidate-20260920/original-files-v2/files')
audit=json.loads((root/'source-extraction-audit.json').read_text())
for item in audit:
 ref=item['source_ref']; p=archive/ref['path']
 assert hashlib.sha256(p.read_bytes()).hexdigest()==ref['sha256']
 loc=ref['locator']
 if loc['kind']=='page':
  n=str(loc['page']); raw=subprocess.check_output(['pdftotext','-layout','-f',n,'-l',n,str(p),'-']).decode()
 else:
  raw='\n'.join(p.read_text().splitlines()[loc['start']-1:loc['end']])
 assert raw==item['raw_text']
 assert hashlib.sha256(raw.encode()).hexdigest()==item['fresh_extraction_sha256']
print(json.dumps({'source_locations':len(audit),'distinct_sources':len({x['source_ref']['sha256'] for x in audit}),'original_sha256_checks':'passed','locator_reread_text_checks':'passed','fresh_extraction_sha256_checks':'passed'},sort_keys=True))
PY
```

实际输出：

```json
{"distinct_sources": 15, "fresh_extraction_sha256_checks": "passed", "locator_reread_text_checks": "passed", "original_sha256_checks": "passed", "source_locations": 35}
```

## 二次复核：跨来源摘要依赖闭合

独立复核发现两个产品的入口/边界摘要缺少其跨语言冲突的英文来源。已补齐四节引用，并检查同类摘要：HUB 入口/边界的跨来源冲突以及另四个提及冲突的规格/软件段落均显式关联对应冲突章节并继承完整来源。各依赖保持相同实体；没有凭间接 K7 字段依赖替代原件引用。私有正文、候选状态、事实/权限签认及角色限制未改变。

私有生成器现建立摘要到冲突章节的依赖及来源闭包。validator 的指南断言绑定完整 path/hash/locator，措辞断言绑定目标章节正文；另固定检查本次两个英文原件位置，防止错误文件同一行号或其他章节的正确措辞导致误通过。

确切重新制备和验证命令：

```sh
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a2-20261010/build_private.py
python3 /Users/neo/Developer/work/AI-DAQ-FAE-Agent/data/knowledge/curated/a2-20261010/validate_private.py
```

两条命令退出码均为 `0`，实际输出依次为：

```json
{"products": 8, "files": 31, "sections": 66, "conflict_sections": 7, "fresh_source_locations": 35, "index_sha256": "d86b13baee11d735392dfd4f69398df30dde9a745664031602e2cccedc513cc1"}
{"checks": "section identity/body hashes/source locations/dependencies/permissions/relative links/no URL or local-path or Agent prose", "products": 8, "markdown_files": 31, "sections": 66, "numeric_presence_flags": [], "review_regression_checks": "exact guide and English source refs, target-section wording, and conflict-summary dependency closure passed", "fact_review": "pending; mechanical verification is not approval", "online_eligible": false}
```

随后再次逐字执行上一节完整的 `python3 - <<'PY'` 原件定位重读命令（脚本未变），退出码 `0`，实际输出：

```json
{"distinct_sources": 15, "fresh_extraction_sha256_checks": "passed", "locator_reread_text_checks": "passed", "original_sha256_checks": "passed", "source_locations": 35}
```

本次未重跑 pytest；后端代码未修改。原件抽取审计哈希未变；章节索引与验证报告的最新哈希已在上文产物指纹更新。

# D1 原件持久归档工程准备

状态：工程准备已验证；真实原件已按用户指示复制到本项目的 Git 忽略目录并逐件核验；D1 独立持久归档与重取验收仍阻塞。

只读核查：本机 v2 归档的 manifest SHA-256 为
`e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`。
逐件核验得到 276 文件、2,038,212,224 字节，大小与 SHA-256 全部一致。
本机 v1 的不同 manifest 被预期锚点拒绝；没有用 v1 替代 v2。

挂载清单中独立 SMB 卷为只读；另一外部挂载是只读安装镜像。
可写 APFS 属本机。用户随后授权在本项目目录内存放原件；已建立 `data/knowledge/local_archive/candidate-originals-v2-local-copy-20261010/` 私有本机副本，目录和文件分别为 `0500`、`0400`，来源、副本与 manifest 的 276 个大小和 SHA-256 均一致。收据位于同级 Git 忽略目录，明确标为同机复制、无独立重取。这个副本可供本机清洗复核，不能证明灾备独立性或通过 D1。尚未发现获确认、可写、独立且受控的目标；后续 D1 仍需保管人提供目标和控制证据。

## 执行合同

`scripts/durable_candidate_archive.py` 复用原归档校验，保存 manifest 原始字节，
验证源和目标的全部文件、大小、哈希及只读模式；目标必须不存在。
命令只能在已确认目标下、独占写入权限的目录执行。运行身份和 ACL 审核由保管人提供，
本工具的 POSIX mode 检查不能证明远程 ACL、持久性或存储独立性。
文件系统必须支持原归档的 0500 目录和 0400 文件合同，否则拒绝，不能放宽校验。
失败 staging 目录留在目标父目录，由获授权操作人私下调查及清理；原件始终保留。

证据 JSON 和详细控制记录存放仓外。证据仅包含无路径的受控记录 ID：
`format_version=1`、`target_id`、`version_id`、`storage_independence_record`、
`authorization_record`、`permission_record`、`read_only_record`、`custodian`、
非空 `authorized_readers`。重取还须 `retrieval_record` 和 `retrieval_environment_id`。
各 ID 应指向实际记录：保管位置映射、授权人/时间、访问控制及读取角色、
只读/不可变策略及版本、存储独立性、独立身份/主机或重新挂载读取的时间与步骤。
工具只检验这些引用存在且无路径；独立审查必须核对其真实性。

保管人确认目标后，在私有环境设置 `ARCHIVE_SOURCE`、`CONTROLLED_DESTINATION`、
`CUSTODY_EVIDENCE`，用 Python 3.11 或以上执行：

```sh
python3.11 scripts/durable_candidate_archive.py copy "$ARCHIVE_SOURCE" \
  --destination "$CONTROLLED_DESTINATION" --evidence "$CUSTODY_EVIDENCE" \
  --expected-manifest-sha256 e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56
```

关闭原读取会话，通过独立位置重新取得归档，登记重取证据后执行：

```sh
python3.11 scripts/durable_candidate_archive.py retrieve "$RETRIEVED_ARCHIVE" \
  --evidence "$RETRIEVAL_EVIDENCE" \
  --expected-manifest-sha256 e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56
```

输出仅为 `copy_verified_pending_independent_retrieval` 或
`retrieved_bytes_verified_pending_review`，从不自动标记 D1 pass。
独立 Codex/人工核对全部 276 文件和上述控制记录后，才更新 D1 验收。
同机源文件及本项目副本继续保留；候选材料仍不是授权知识发布输入。
详细原件名、manifest、证据位置和真实命令参数不得进入 Git。

## 验证

Python 3.11：`python3.11 -m pytest tests/test_durable_candidate_archive.py tests/test_archive_candidate_snapshot.py -q`：15 passed。
新增测试首先因工作流缺失得到 5 failed，再实现得到 5 passed；追加路径泄漏及软链接哨兵后共 15 passed。
系统 Python 3.9 不支持既有脚本的 `Path.stat(follow_symlinks=False)`，须使用项目支持的 Python。

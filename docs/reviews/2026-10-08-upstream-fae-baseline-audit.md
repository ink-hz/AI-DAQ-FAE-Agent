# M0-1：现有 FAE 发布与设计基线只读核查

**核查时间**：2026-10-08 06:39 UTC。

**状态**：生产发布身份已核实；设计分支远端可达，分支保护设置尚未核实。M0-1 未全部通过。

## 生产发布身份

| 独立证据 | 只读核查结果 |
| --- | --- |
| 生产 `/health` | HTTP 200，`environment=production`；`build.available=true`，`build.source=artifact`，`build.git_sha=a6234f6be546efebb230ffc74bd9a00bebdc2814`，`build.release_name=ai-fae-agent-20261008040853` |
| 当前发布包 `build-info.json` | 通过生产服务器只读读取 `/opt/ai-fae-agent/current/build-info.json`，`git_sha` 与 `release_name` 均与 `/health` 一致 |
| 本地发布 manifest | 现有 FAE 的 `feat/gemini-materials-20261008` 工作树中，`dist/release/manifests/ai-fae-agent-20261008040853.json` 记录同一 Git SHA/发布名，`status=succeeded`、`deployable=true`、`remote_match=true`；包 SHA-256 为 `59847312aed60310e8ee0354c1fc7f950d15d2b7d33a5f8423b647529cdb2a08` |

三项相互一致，因此 `a6234f6` 已部署到当前生产，而“未在 `master` 或 tag 上”只说明它缺少适合作为数采依赖的持久 Git 锚点。本次没有调用生产 `/chat`、评测或负载测试。

## 设计提交的远端可达性

FAE 设计基线 `881c1491c2e47fd2aa0452f66381966ea661170a` 已推送到 `origin/docs/data-acquisition-fae-design-20261008`。后续盘点口径修订 `972852ad6cdc93dc7a5cea439d66ca35d847b684` 和生产基线说明修订 `c261501001f98e3f0c7bba773412a7ba96df9db4` 也已推送；`git merge-base --is-ancestor` 确认 `881c149` 是这些后继提交的祖先，`git ls-remote` 确认远端引用。推送和可达性不能证明该分支受保护。

## 尚待完成的门

1. GitHub 仓库管理员在 `docs/data-acquisition-fae-design-20261008` 上启用或确认禁止强推、禁止删除的分支保护/规则集，并留下设置或 API 核查记录。本机没有 `gh` 或可用的 GitHub HTTPS API 凭据，不能独立核实该设置。
2. 数采仓库目前只有本地 `main`、没有远端；实施前须建立可复取的远端和保护策略。
3. `a6234f6` 不是最终数采依赖 SHA。完成 `EvidencePolicy` 与身份参数化、旧域回归和双仓合同后，M0-5 将包含这些变更的最终上游提交固定到持久受保护 ref。

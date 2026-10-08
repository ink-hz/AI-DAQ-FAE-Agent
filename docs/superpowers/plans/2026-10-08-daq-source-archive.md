# DAQ Candidate Source Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the original 2026-09-20 DAQ candidate snapshot with a per-file hash manifest and a verified, restricted off-repository copy, while making the camera repository ignore `tmp/` through a versioned rule.

**Architecture:** An offline archive utility copies only regular files from an explicitly named snapshot into a new destination. It records relative path, byte count, original modification time and SHA-256, then verifies every copied byte and rejects missing, extra or linked files. The archive is only a candidate source; no DAQ knowledge release or online retrieval points at it.

**Tech Stack:** Python 3.11 standard library, Git, pytest.

## Global Constraints

- Keep the original `tmp/` tree untouched and untracked.
- Keep the archive and file manifest outside both Git repositories, owner readable only; never print file contents.
- Do not promote candidate facts, downloads or links to `empty-dev-v0`.
- Camera `master` has unrelated uncommitted documents; edit its isolated worktree only.
- A local restricted archive is a recoverable staging copy; controlled durable storage and branch protection must be verified separately before declaring M0-4 passed.

---

### Task 1: Version the camera `tmp/` ignore rule

**Files:** Modify `../AI-FAE-Agent/.worktrees/daq-tmp-ignore/.gitignore`.

**Interfaces:** `git check-ignore -v tmp/` must name `.gitignore`, not `.git/info/exclude`.

- [x] **Step 1:** Add `/tmp/` to the repository root `.gitignore` in the isolated camera worktree.
- [x] **Step 2:** Run `git check-ignore -v tmp/` in that worktree and inspect the rule; run `git diff --check`.
- [x] **Step 3:** Commit the single file on `feat/daq-tmp-ignore-20261008` and push the branch for review. Do not merge it into `master` during this task.

### Task 2: Archive and verify the original candidate bytes

**Files:** Create `scripts/archive_candidate_snapshot.py`; test `tests/test_archive_candidate_snapshot.py`; write the archive outside both repositories.

**Interfaces:** `create_archive(source: Path, destination: Path, source_date: str) -> dict` and `verify_archive(destination: Path, expected_manifest_sha256: str | None = None) -> dict`. CLI: `create SOURCE DESTINATION --source-date YYYY-MM-DD` and `verify DESTINATION --expected-manifest-sha256 DIGEST`.

- [x] **Step 1:** Write tests with two small synthetic files asserting relative paths, sizes, SHA-256 values, source dates, and byte-identical re-read. Add a tamper test, an extra-file test, a source symlink rejection test, and a destination-exists rejection test.
- [x] **Step 2:** Run `/Users/neo/Developer/work/AI-FAE-Agent/.venv/bin/python -m pytest -q tests/test_archive_candidate_snapshot.py` and confirm failures because the utility is absent.
- [x] **Step 3:** Implement `create_archive`: enumerate the tree without following links, reject any symlink or non-regular entry, copy into a new private staging directory, hash streamed bytes, write canonical UTF-8 JSON manifest, verify it, atomically rename to the requested destination, and make the archive read-only for its owner. Include original file mtimes and snapshot label date. Reject an existing destination.
- [x] **Step 4:** Implement `verify_archive`: reject links, missing and extra files, check every listed file's size and SHA-256, and return a file/byte summary. CLI must emit only summary counts and manifest SHA-256.
- [x] **Step 5:** Run focused tests, then create a restricted local archive under `../AI-FAE-Agent-local-archive/daq-candidate-20260920/` from `../AI-FAE-Agent/tmp/数据采集设备资料汇总-20260920(1)/`. Verify the archive again in a separate process and compare all source file hashes to the manifest; record count, byte total and manifest hash, not the list of filenames, in the review note.
- [x] **Step 6:** Run the full DAQ backend suite, source snapshot check, WebUI tests and build. Commit the utility, tests, plan and review note on `feat/m0-archive-20261008`. No shared runtime or production behavior changes.

### Task 3: Correct repository status documentation

**Files:** Modify `README.md` and `docs/reviews/2026-10-08-upstream-fae-baseline-audit.md` only where historical text is currently presented as live status; create `docs/reviews/2026-10-08-daq-takeover-gate-audit.md`.

**Interfaces:** Distinguish pushed Git refs, GitHub protection and CI evidence, local tests, Dev replay, source archive, knowledge, Platform and pilot gates.

- [x] **Step 1:** Replace README claims that the DAQ repository has no remote. State the observed `origin/master` SHA and the verification date, plus the remaining protected-ref/CI proof gap.
- [x] **Step 2:** Record a task-book gate matrix with evidence and remaining work. Label local archive as staging until storage controls and restore evidence are accepted; label the camera branch ignore change as pending merge.
- [x] **Step 3:** Run `git diff --check`, verify both repository statuses, and commit the documentation with Task 2. Do not call a Git push a production deployment.

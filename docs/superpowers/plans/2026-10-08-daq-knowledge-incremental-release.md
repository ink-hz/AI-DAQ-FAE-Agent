# DAQ Knowledge Incremental Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic offline K-1 importer and local immutable publisher that supports frequent source updates without silently changing reviewed DAQ answers.

**Architecture:** Read only a verified off-repo archive. Emit a private candidate snapshot with exact source locations and deterministic change report. Publish separate reviewed records to immutable local release directories after validating provenance, conflict state, and role visibility. Atomically switch an active pointer and permit rollback.

**Tech Stack:** Python 3.11, PyMuPDF, standard library, pytest.

## Global Constraints

- Raw `tmp/` files and user attachments are never authoritative knowledge.
- The importer never promotes extracted text or a package name to an answer or URL.
- All model evaluation remains in Dev; this batch changes no shared FAE runtime.
- A new source version never mutates an existing knowledge release.
- Current same-machine archive is for Dev only until controlled durable storage is verified.

---

### Task 1: Deterministic candidate import

**Files:** Create `daq_fae/knowledge/source_import.py`, `daq_fae/knowledge/__init__.py`, `tests/test_knowledge_source_import.py`.

**Interfaces:** `import_archive(archive: Path, manifest_sha256: str) -> dict` consumes the verified archive and returns JSON-serializable `sources`, `chunks`, and archive identity. `compare_snapshots(previous: dict, current: dict) -> dict` returns added, changed, removed paths.

- [ ] Write tests for same-input determinism, PDF page and Markdown line provenance, binary metadata exclusion, and changed/added/removed file diff.
- [ ] Run the new tests; confirm failure because the interface does not exist.
- [ ] Implement import by calling the archive verifier, using PyMuPDF for PDF pages and bounded heading/line chunks for Markdown/TXT; hash every chunk and sort output.
- [ ] Run new tests and the DAQ suite.
- [ ] Commit the isolated change.

### Task 2: Reviewed record schema and update impact

**Files:** Create `daq_fae/knowledge/records.py`, `tests/test_knowledge_records.py`.

**Interfaces:** `validate_records(records: list[dict], snapshot: dict) -> tuple[list[dict], list[dict]]` returns normalized records and findings; `impact_report(records: list[dict], change: dict) -> dict` identifies records tied to changed or removed source paths.

- [ ] Write failing tests for six record types, exact source hash/location, duplicate IDs, conflict candidates, independent review and visibility, unverified links, and impacted records.
- [ ] Implement strict schema validation with no automatic status promotion. Only reviewed `verified` or reviewed explicit `unsupported` records with roles can be answerable; `conflict` and `unknown` remain visible in audit but not answerable.
- [ ] Run tests and commit.

### Task 3: Immutable local releases and rollback

**Files:** Create `daq_fae/knowledge/releases.py`, `tests/test_knowledge_releases.py`.

**Interfaces:** `publish_release(root: Path, snapshot: dict, records: list[dict], previous_release: str | None, review: dict) -> str` writes a content-addressed read-only release after validation; `activate_release(root: Path, release_id: str) -> None` atomically changes the active pointer; `read_active_release(root: Path) -> dict | None` reads it.

- [ ] Write failing tests for deterministic IDs, conflict/non-answerable preservation, stale source rejection, failed publish leaving active pointer intact, explicit activation, and rollback.
- [ ] Implement immutable manifest, atomic temporary directory rename and pointer replacement, source/review/role counts, previous release ID, and validation before writes.
- [ ] Run tests and commit.

### Task 4: CLI, real candidate rehearsal, and evidence

**Files:** Create `scripts/daq_knowledge.py`, `tests/test_knowledge_cli.py`, `docs/reviews/2026-10-08-daq-k1-candidate-rehearsal.md`; update `README.md` to document offline commands and the new location of the ignored raw package.

- [ ] Write CLI tests for import/diff/publish/activate/rollback with temporary synthetic archives; verify no live knowledge changes during import.
- [ ] Implement CLI with private output modes, explicit expected manifest SHA, and JSON reports; import the real 276-file archive to an ignored local output directory and inspect counts and extraction errors.
- [ ] Run the full DAQ suite, upstream snapshot check, and WebUI tests/build. Record exact outcomes and pending K-2/K-4 gates.
- [ ] Request code review and fix material issues, then commit and push the feature branch. Do not equate push with deployment.

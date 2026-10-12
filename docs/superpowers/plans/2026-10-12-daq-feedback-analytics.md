# DAQ Feedback Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a private, read-only DAQ feedback and quality summary from the DAQ PostgreSQL schema without reading camera rows.

**Architecture:** A DAQ-owned query layer validates the exact schema search path and installation identity, runs a read-only repeatable-read transaction, and returns aggregate counts only. A CLI writes an atomic private JSON artifact; detailed feedback and review decisions stay in the existing authenticated Review Center.

**Tech Stack:** Python 3, psycopg 3, pytest, DAQ PostgreSQL migrations.

## Global Constraints

- Use only `DAQ_DATABASE_URL` with `options=-csearch_path=daq_fae` and a matching `daq_installation_identity` row.
- Never read `public` or camera FAE tables; never write production rows from analysis.
- Do not include questions, answers, comments, user IDs or source paths in aggregate output.
- Do not run model evaluations in production. This batch does not change answering behavior.

---

### Task 1: Query contract and isolation

**Files:** Create `daq_fae/feedback_analytics.py`; create `tests/test_daq_feedback_analytics.py`.

**Interfaces:** `summarize_feedback(dsn: str, start: datetime, end: datetime) -> dict` consumes an exact-schema DAQ DSN and produces counts for turns, outcomes, fallback, ratings, negative reason codes and review status.

- [x] **Step 1: Write failing tests.** Build DAQ and public schemas in the existing isolated PostgreSQL fixture, insert one camera sentinel into `public` and two DAQ turns into `daq_fae`, with one bad feedback and one review. Assert DAQ-only counts. Assert a DSN without exact `search_path=daq_fae` is rejected before connecting.
- [x] **Step 2: Run `python -m pytest -q tests/test_daq_feedback_analytics.py` and confirm RED.**
- [x] **Step 3: Implement `summarize_feedback`.** Validate URL via `_validate_database`, require the exact options parameter, check `current_schema()` and `daq_installation_identity`, set `repeatable read read only`, then run bounded `count(*) ... group by` queries filtered by `chat_turns.created_at >= start and < end`. Return only normalized aggregate maps and the UTC window.
- [x] **Step 4: Run the focused tests and confirm GREEN.**
- [x] **Step 5: Commit the query layer and tests.**

### Task 2: Private CLI artifact

**Files:** Create `scripts/daq_feedback_analytics.py`; extend `tests/test_daq_feedback_analytics.py`.

**Interfaces:** `python -m scripts.daq_feedback_analytics --start <ISO UTC> --end <ISO UTC> --output <ignored private JSON>` reads `DAQ_DATABASE_URL`, calls `summarize_feedback`, and atomically writes mode `0600` JSON under `analysis/results/daq/`.

- [x] **Step 1: Write failing tests.** Assert output outside the ignored private root and symlinks fail; a valid output contains only aggregate keys and has mode `0600`.
- [x] **Step 2: Run the focused tests and confirm RED.**
- [x] **Step 3: Implement argument parsing, private-path validation, atomic write and concise status output.**
- [x] **Step 4: Run focused tests and confirm GREEN.**
- [x] **Step 5: Commit the CLI and tests.**

### Task 3: End-to-end verification

**Files:** Create `docs/reviews/2026-10-12-daq-feedback-analytics-dev.md`.

- [x] **Step 1: Run an isolated DAQ-schema PostgreSQL integration test with camera public-table sentinels.**
- [x] **Step 2: Run relevant Python tests and static import checks.**
- [x] **Step 3: Record exact command results and limitations, then commit.**

This plan covers aggregate feedback analysis. Task authorization, attachment archive handoff, knowledge release expansion and English are separate plans under the parent design.

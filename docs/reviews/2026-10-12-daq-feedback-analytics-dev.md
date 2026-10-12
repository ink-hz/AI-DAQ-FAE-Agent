# DAQ feedback analytics Dev verification

Date: 2026-10-12. The DAQ-owned `summarize_feedback` query requires an exact `daq_fae` search path and a matching installation identity. It uses a read-only repeatable-read transaction and emits aggregate counts only. The CLI writes an immutable private JSON artifact below Git-ignored `analysis/results/` with mode `0600`. It does not answer questions or call a model.

The isolated PostgreSQL contract test applied all DAQ migrations into `daq_fae` while retaining a `public` camera sentinel. It inserted two DAQ turns, one bad feedback and one pending review. The resulting summary counted exactly the two DAQ turns, one fallback, one negative reason and one pending review; neither answer/comment nor the public sentinel appeared in output. Invalid search-path and window requests failed before database access. The private output test rejected paths outside the private root, symlinks and overwriting an existing artifact.

Verification at the implementation revision:

```text
.venv/bin/python -m pytest -q tests/test_daq_feedback_analytics.py tests/test_shared_database_schema.py tests/test_authenticated_persistence.py
32 passed in 5.82s
git diff --check
passed
.venv/bin/python -m compileall -q daq_fae/feedback_analytics.py scripts/daq_feedback_analytics.py
passed
```

No production database was queried. No production feedback volume or reviewed quality rate is claimed. The existing authenticated Review Center remains the place for detailed human review; this first DAQ analytics batch provides a private aggregate view, not an approved Platform report.

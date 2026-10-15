# DAQ SDK knowledge

This is a source-checked **Dev usage catalog** built from the local 2026-10-15 archive. It follows the camera FAE `Knowledge_SDK` split between API/example facts and tested device support. `sdk_evidence` can return these usage records during isolated Dev replay. Exact device, firmware, connection and platform support remains under `check_software_support`; these 15 records do not certify it.

## Scope

| Layer | Source build | Records | Role |
| --- | --- | ---: | --- |
| Host C++ EGO examples | OrbbecSDK 2.9.0, 2026-09-21/23/29 Linux x86_64 packages | 12 | internal FAE |
| Device C API | libobkit 2026-09-09, RK3576/AArch64 | 3 | internal FAE |

The catalog has 42 source references to exact archive members and line spans. The source archive is ignored by Git and hash verified. The committed JSONL has facts and provenance only, not binary packages. `curated.json` is the human-readable source review input. The manifest binds the generated JSONL and original archive manifest.

Each generated record also carries code identifiers found within its selected source spans. The answer policy permits those identifiers in SDK usage prose while still rejecting internal record IDs and source paths. These identifiers are not a `code_verified` complete snippet and do not authorize a generated copyable program.

For manual inspection, use `package_index.md`, `topic_index.md`, `symbol_index.md`, `usage_task_index.md` and `source_member_index.md`. These are regenerated from the same records; they are not separate sources of truth.

The archived packages outside this first slice are listed in [`audit/coverage_gaps.md`](audit/coverage_gaps.md). An absent record means this catalog has not reviewed that package or API; it does not mean the SDK lacks the capability.

## Rebuild and check

```bash
.venv/bin/python -m scripts.build_daq_sdk_knowledge \
  --archive data/knowledge/local_archive/sdk-originals-1015-local-20261015

.venv/bin/python -m pytest -q tests/test_daq_sdk_catalog.py tests/test_software_evidence.py
```

For a new fast-moving SDK drop: archive originals, diff members and changelog against the preceding build, update `curated.json` and source spans, regenerate JSONL, then run the SDK question replay and independent answer review. An absent member or changed hash must fail validation; old records must not silently inherit new build claims.

`updates.md` summarizes the current build history. Regenerate the mechanical source diff with:

```bash
.venv/bin/python -m scripts.audit_daq_sdk_updates \
  --archive data/knowledge/local_archive/sdk-originals-1015-local-20261015 \
  --output Knowledge_SDK/update_audit.json
```

The 2026-09-09 `libobkit` README says its library is strongly bound to the matching whole-device image. Its own build instructions contain an old package filename, so use the actual package held locally. No firmware/image pair has been device tested by this catalog.

## Independent publication

After a frozen Dev replay and independent answer review, `scripts.publish_daq_sdk_release` can stage this catalog as a content-addressed SDK release and atomically activate it. Production reads only the active SDK release from `DAQ_SDK_RELEASE_ROOT`; code deploy and knowledge activation have separate identities. Re-activating the previous release ID rolls back the SDK knowledge without changing the main DAQ product release.

# DAQ SDK knowledge

This is a source-checked SDK usage catalog built from the local 2026-10-15 archive. The previously reviewed 23 usage records and 17 unique change-note records were staged as immutable release `ee6b77ad30fc8f449cfe59f10d823987347a860f8521308784bfc4f3aadf0805`. The current workspace has a 24th usage record, `daq-sdk-ble-linux-link-024`, bound to the EgoLowBle Linux guide and scripts; it is a Dev candidate until a new source review, model replay and independent answer review pass. Check production `/health` for its actual active SDK release ID. The catalog follows the camera FAE `Knowledge_SDK` split between API/example facts and tested device support. Exact device, firmware, connection and platform support remains under `check_software_support`; these records do not certify it.

## Scope

| Layer | Source build | Records | Role |
| --- | --- | ---: | --- |
| Host C++ EGO examples | OrbbecSDK 2.9.0, 2026-09-21/23/29 Linux x86_64 packages | 14 | internal FAE |
| Device C API | libobkit 2026-09-09, RK3576/AArch64 | 3 | internal FAE |
| Android Java API | OrbbecSDK Android Wrapper beta2.2.5, 2026-09-29 | 3 | internal FAE |
| BLE C++ integration | EgoLowBle 1.1.8, 2026-09-29 Windows x64 and Linux x64 | 3 | internal FAE |
| iPhoneOS framework integration | OrbbecSDK 2.9.0, 2026-09-29 iOS ARM64 | 1 | internal FAE |

The current catalog has 61 source references to exact archive members and line spans. The source archive is ignored by Git and hash verified. The committed JSONL has facts and provenance only, not binary packages. `curated.json` is the human-readable source review input. The manifest binds the generated JSONL and original archive manifest. The previously published records and their frozen Dev replay are described in the [2026-10-17 release review](../docs/reviews/2026-10-17-daq-knowledge-dev-release.md); the Linux link and runtime-path addition is reviewed in the 2026-10-19 complex evaluation.

The `answer` field in `curated.json` and `release_notes_curated.json` contains only source-supported statements from the cited members or log lines. Reviewer inferences about what a source cannot prove belong in the independent review and the typed `device_support` / `evidence_scope` boundary, not in that field; otherwise an answer can misattribute reviewer reasoning to the original document. Keep source paths and line numbers in `source_refs`, never in answer prose. A changed statement gets a new record hash and must be independently reviewed and replayed before activation.

Each generated record also carries code identifiers found within its selected source spans. The answer policy permits those identifiers in SDK usage prose while still rejecting internal record IDs and source paths. These identifiers are not a `code_verified` complete snippet and do not authorize a generated copyable program.

An example may declare `example_subjects` only when its verified archive member names that subject. This field helps match a question to the example, such as EGO Depth's network preset sample. A search hint in `question_patterns` cannot establish a device subject, and an example subject never certifies device or firmware support.

An API record may declare `callable_symbols` only for names verified as calls in its cited source spans. The recording callback record uses this to distinguish its two callback methods from `SAVING` and `RECORD_SAVED` state constants. When a question names both callbacks and asks an unsupported extra action, the agent can explain the callback methods while leaving the extra action unverified.

For manual inspection, use `package_index.md`, `topic_index.md`, `symbol_index.md`, `usage_task_index.md` and `source_member_index.md`. These are regenerated from the same records; they are not separate sources of truth.

The archived packages outside this first slice are listed in [`audit/coverage_gaps.md`](audit/coverage_gaps.md). The [complete 143-file intake ledger](../docs/reviews/2026-10-16-daq-sdk-material-coverage.md) records hashes, duplicates and held candidates. An absent record means this catalog has not reviewed that package or API; it does not mean the SDK lacks the capability.

`material_inventory.jsonl` is a separate offline asset index for all 143 originals. It records path, hash, size, platform/date hints, byte duplicates and intake scope; every row has `device_support=unknown` and `download_authority=unverified`. A local FAE reviewer can query artifact identity with `scripts.build_daq_sdk_material_inventory --query EgoLowBle` after verifying the original archive. This index is not loaded into SDK usage coverage or software compatibility checks.

`Release_Notes.jsonl` is a separate catalog of what the archived SDK, EgoViewer and firmware change logs **state**. The 22 log files yield 17 distinct file hashes: five Linux/Windows Viewer log pairs have identical bytes and count as one statement each. Each record binds an exact file hash and line span, build, platform and `internal_fae` role. Search results have `evidence_scope=release_note_statement_only` and `device_support=unknown`; they cannot satisfy an SDK API goal or `check_software_support`. The two `.url` shortcuts are absent from this catalog. These notes passed frozen Dev replay and independent answer review in the release named above.

## Rebuild and check

```bash
.venv/bin/python -m scripts.build_daq_sdk_knowledge \
  --archive data/knowledge/local_archive/sdk-originals-1015-local-20261015

.venv/bin/python -m scripts.build_daq_sdk_material_inventory \
  --archive data/knowledge/local_archive/sdk-originals-1015-local-20261015

.venv/bin/python -m scripts.build_daq_release_notes \
  --archive data/knowledge/local_archive/sdk-originals-1015-local-20261015

.venv/bin/python -m pytest -q tests/test_daq_sdk_catalog.py tests/test_daq_release_notes.py tests/test_daq_sdk_material_inventory.py tests/test_software_evidence.py
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

After a frozen Dev replay and independent answer review, `scripts.publish_daq_sdk_release` can stage this catalog as a content-addressed SDK release and atomically activate it. A release containing notes also requires a source-span review bound to every SDK usage and note record SHA, plus a reviewed Dev answer that actually cites a note. The replay and report must agree on the frozen DAQ code SHA and main-knowledge release. The loader rechecks those two identities against the running DAQ code and loaded main knowledge for note-bearing releases. Production reads only the active SDK release from `DAQ_SDK_RELEASE_ROOT`; code deploy and knowledge activation have separate identities. Re-activating the previous release ID rolls back the SDK knowledge when its runtime identity also matches.

# DAQ SDK source evidence and Dev replay

## Scope

Build a separate SDK usage layer following the camera FAE's `Knowledge_SDK` pattern: source located records for APIs, examples, build and troubleshooting, with device compatibility governed separately by exact software evidence. Use the archived 2026-10-15 local packages. The first release covers the EGO C++ examples and the distinct device side `libobkit` API; the import process must support rapid package revisions and surface additions and removals between builds.

## Work

1. Verify and archive original bytes and produce package/member/line based source locators. Never treat package presence as tested device support.
2. Add an SDK record schema, validator, manifest and indexes. Records carry package SHA, member SHA, version/build, platform, layer, answer, query patterns, confidence and source line spans. Only reviewed records enter the tool view.
3. Give `sdk_evidence` a usage/documentation route distinct from `check_software_support`. Return source metadata and an explicit unknown device support state; preserve existing exact software checks.
4. Add deterministic tests for source tamper, line validity, role visibility, relevance, version conflict and compatibility non-escalation.
5. Replay real SDK questions through the Dev model path and independently review answers against source and negative controls. Record exact release, model and test artifacts. No production model evaluation.

## Acceptance

- API and sample answers contain source backed facts and version/build scope.
- An example in a package never becomes a claim that a specified device/firmware combination works.
- Unsupported or conflicting version questions produce a visible uncertainty.
- SDK change ingestion identifies additions, removals and altered members for the next update.
- Existing DAQ and camera runtime contract tests pass if shared runtime is touched.

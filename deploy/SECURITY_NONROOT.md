# DAQ non-root deployment preparation

This is a deployment candidate, not authorization or evidence to cut over. Use the exact existing image ID `sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7`; do not build or pull. The independent native gates V01–V11 and the production observation V12 in `2026-10-29-daq-nonroot-design.md` remain mandatory. The controller owns container, image, UID collision, mount, trace reader/rotation, quiescence, Docker/Compose compatibility, rollback and actual release checks. Never run this tooling against a live writable source and describe the result as a cold capture.

`security_assets.py` requires effective root. Output parents and every ancestor must be root-owned and not group/other writable; targets must not exist. Supply absolute paths with no symbolic-link components. It never changes a source file or its ownership. It creates a private target (0700) and, on successful asset staging, exposes its root as root:21081 0750. Secret and content key copies are 21081:21081 0600; entitlement and approval key copies are root:21081 0640; RO knowledge files are root:21081 0640 with directories 0750. New attachment and trace data are 21081:21081, directories 0700 and files 0600. Docker must mount the RO leaves read-only. Inspect actual native owner and permission behavior separately; host-side mocked tests do not prove it.

Every command reads a root:root 0600 JSON spec. The spec carries paths, SHA-256/size identities, and metadata, never key contents. Collect the expected inventory independently and privately; it is a required controller input, not a hash of an unverified copy. Inventories must cover every regular file under each selected knowledge root and the complete attachment tree; list exactly the four referenced secrets and the single trace. The tool does not copy other secrets or historical knowledge roots. Inventory identity must bind the published version and be rechecked for drift; a missing or extra file aborts. Files over 10 GiB, totals over 1 TiB, more than one million files/directories, more than 128 directory levels, links (including hard links) and special files are refused. Sources are opened component by component with no-follow descriptors, fstat checked, copied with exclusive targets, checked by hash and re-read after copying, and the new tree is checked again. Fsync is used for files and directories. The tool reports only stage/matched on stdout or stderr; detailed `manifest.json` is root-private and contains sensitive paths, hashes, identities and attachment names. Never put it in a general report or log.

Example schema (replace all placeholder values; SHA values are 64 lowercase hex characters):

```json
{
  "uid": 21081, "gid": 21081,
  "paths": {
    "main": "/absolute/original/main-root",
    "sdk": "/absolute/original/sdk-root",
    "secrets": "/absolute/original/secrets",
    "attachments": "/absolute/original/attachments",
    "trace": "/absolute/original/daq_traces.jsonl"
  },
  "expected": {
    "main": {"active.json": {"size": 123, "sha256": "<sha256>"}},
    "sdk": {"active.json": {"size": 123, "sha256": "<sha256>"}},
    "secrets": {
      "session-keyring.json": {"size": 123, "sha256": "<sha256>"},
      "content-keyring.json": {"size": 123, "sha256": "<sha256>"},
      "entitlements.json": {"size": 123, "sha256": "<sha256>"},
      "feedback-approval-keys-20261023.json": {"size": 123, "sha256": "<sha256>"}
    },
    "attachments": {"relative/path": {"size": 123, "sha256": "<sha256>"}},
    "trace": {"daq_traces.jsonl": {"size": 123, "sha256": "<sha256>"}}
  },
  "runtime": {
    "ro_dir": "/absolute/new-ro-batch",
    "rw_dir": "/absolute/new-rw-batch",
    "env_file": "/absolute/existing-env-file",
    "env_sha256": "<sha256>",
    "old_secrets_dir": "/absolute/original/secrets",
    "project": "deploy",
    "labels_file": "/absolute/private-controller-labels.json",
    "config_files": {"/absolute/original-compose.yml": "<sha256>"},
    "config_identity": "sha256:<labels-file-sha256>"
  }
}
```

The abbreviated inventories above are examples, not complete inventories. Empty attachment trees use `{}`. Keys in each `config_files` object must appear in the same order as the comma-separated `com.docker.compose.project.config_files` label. `labels_file` is a controller-captured JSON object containing at least the actual container's `com.docker.compose.project`, `com.docker.compose.service`, and `com.docker.compose.project.config_files` labels. It is root:root 0600. The tool validates the evidence bytes and those files against the caller's expected hashes, but cannot establish which running container supplied the evidence. The controller must independently validate that provenance and identity. The existing env file is read privately and must match `env_sha256`, production authentication and exact selected internal paths; its approval key pin must match the expected public-key file hash. No expanded environment is printed.

1. During live service, stage only the RO material with `python3 deploy/security_assets.py stage-ro --spec /absolute/private-spec.json --target /absolute/new-ro-batch`. It does not copy attachments or trace. Recheck published active identity and source inventory before proceeding.
2. The controller stops only DAQ and establishes that uploads, workers and leases are quiescent. Capture the controller's evidence in a separate root:root 0600 JSON file: `{"container_id":"<64 lowercase hex>","image_id":"sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7","stopped_at":"2026-10-29T00:00:00Z","quiescence_evidence":"sha256:<controller-evidence-sha256>"}`. The tool records this as `controller_asserted_not_verified`; it cannot verify Docker state or writer quiescence. Independently capture the current complete attachment/trace inventory after stopping. Run `python3 deploy/security_assets.py capture-rw --spec /absolute/private-spec.json --target /absolute/new-rw-batch --stopped-evidence /absolute/private-evidence.json` while still quiescent.
3. Run `python3 deploy/security_assets.py render --spec /absolute/private-spec.json --target /absolute/new-config-batch`, then `python3 deploy/security_assets.py verify --spec /absolute/private-spec.json --target /absolute/new-config-batch` immediately before a controller-approved start. Rendered `runtime.json` and `rollback.json` are root-private Compose JSON files. Both use the exact old image, `pull_policy: never`, same loopback port/networks/address/restart/health, RO knowledge and leaf RW paths. The rollback omits the non-root security overrides, uses the original secrets and keeps **current new** attachments/trace and the unchanged external PG. No broad `/app/data` mount is generated. Verify checks identities, hashes, permissions and exact generated config, not Docker's effective configuration or running container. Validate those separately on the isolated native host without exposing expanded env.
4. After the service has written data, the captured RW manifest is deliberately stale: do not present `verify` against that old inventory as a post-write or rollback acceptance. Use the previously verified rollback config after stopping writers; do not overwrite new data from the old snapshot or restore PG. A later re-forward after root recovery needs a fresh stopped-source copy into another exclusive batch because root may have created root-owned files. No online recursive chown.

Any failure after a new target is created leaves a private `failure.json` with a sanitized reason/category and partial target; it is not published as a successful batch. Failures before an exclusive target can be safely created (including bad inputs, unsafe parents or collisions) cannot leave a receipt there. Preserve the controller's sanitized command result and inspect private evidence; never overwrite or reuse a failed target. `verify` is read-only and leaves no receipt. An existing valid batch is never overwritten.

# Isolated native non-root probe

This is Task2b tooling, not deployment approval. `probe_nonroot_runtime.py` accepts
only the explicitly prepared main/SDK roots, the pinned **public** approval key,
exact local image IDs and a new private workroot. It has no production env-file,
DSN, private-keyring, network, endpoint, Docker-context or container input. The
source image is hard-pinned; `--pull never` is always used. No image is built.

Run only after parent review, as root on the authorized native Linux amd64 host
with the exact application image and an explicitly reviewed official PostgreSQL
image already local. The PostgreSQL image must support UID/GID 999 and the normal
`docker-entrypoint.sh`/`POSTGRES_*` interface. It receives an independent tmpfs
cluster; production PostgreSQL is never contacted or restored.

```sh
python3 deploy/probe_nonroot_runtime.py run \
  --main-root /PRIVATE/PREPARED/main \
  --sdk-root /PRIVATE/PREPARED/sdk \
  --public-approval-key /PRIVATE/PREPARED/feedback-approval-keys-20261023.json \
  --expected-public-sha d5e2f0c91431171aba2061c14734c1747eb66886f1a2aa26b99577121617fa38 \
  --source-image sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7 \
  --pg-image sha256:REPLACE_WITH_REVIEWED_LOCAL_PG_IMAGE_ID \
  --workroot /var/tmp/daq-native-UNIQUE-ATTEMPT \
  --runtime-release daq-062f0a7ae744 \
  --main-internal /app/data/knowledge/reviewed-thinking-prefix-20261028 \
  --sdk-internal /app/data/knowledge/sdk-thinking-prefix-20261028
```

Input paths must be real absolute paths without symlink components. The supplied
RO inputs are copied into the exclusive workroot, checked for links/special files,
and source contents/metadata checked again. All write/delete/rename negatives
operate on those copies or the isolated image filesystem, never source assets.
Synthetic session/content keys are independently generated; old root copies and
new 21081 copies have identical bytes. Elevated owner setup is limited to new
isolated fixture files and is separate from the APP runtime policy.

The private PostgreSQL container has `network=none`, no published ports, no logs,
no writable host data and non-root UID 999. Owned APP/worker containers join its
exact full-ID network namespace; only private loopback exists. APP uses UID/GID
21081, read-only rootfs, cap-drop ALL, NNP, no added groups and a 256 MiB
noexec/nosuid/nodev private tmpfs. Only synthetic attachment/trace leaves are RW.
Workers run in independently owned containers, never `docker exec`; their timeout
causes cleanup of the owned containers. The root seed/rollback stages deliberately
use the old root/default-capability baseline and are labelled separately.

`journal.json` is fsynced before each Docker create. A lost create result is
reconciled only by exact unique name, owner/role labels and image ID. Cleanup
requires actual stopped state and subsequent full-ID and name absence. A failure
leaves a blocked journal and unreleased `/run/daq-native-probe/active.json` lease;
starting another workroot is refused. Retain all files and use only:

```sh
python3 deploy/probe_nonroot_runtime.py cleanup --workroot /var/tmp/daq-native-UNIQUE-ATTEMPT
```

Do not remove the global lease to bypass a blocked cleanup. Do not use Docker
prune, broad rm, restart source containers, production Nginx commands or production
secrets. If the control directory was lost after a host reboot, reconcile the
exact retained journal under parent review; daemon residue blocks fresh runs.

`report.json` has fixed safe result categories. Private `output/` retains synthetic
fixtures, input hashes, issued synthetic session credentials, intermediate evidence
and task receipts. Keep the workroot private; do not attach the whole directory to
a public issue. Runtime app failures/warning counts are copied before cleanup;
ordinary application logs are disabled. The full synthetic old trace prefix,
old root attachment snapshot, current data and re-forward copy remain available.
No production data is preserved there because none is admitted.

Select affected gates with repeated `--gate Vxx`. V01 is always a prerequisite;
V02/V07/V08 share the actual upload/spawn observation; V10/V11 require those same
uploads. V03/V04 share filesystem tests and restart. No gate retries. A new batch
needs a new workroot after confirmed cleanup. Readiness polling is bounded and
never repeats a model call or an already-failed gate.

| Gate | Implemented native assertion path | Qualification |
|---|---|---|
| V01 | exact image/platform, default signed main+SDK startup, actual migrations/PG marker, production health, no SQLite, warning counter | First real run still required |
| V02 | shared-PID-namespace `/proc` main, spawn worker and Tesseract UID/GID/caps/NNP; RSS/tmpfs sampling | Missing any observed process fails; sampled peaks are lower bounds |
| V03 | write/delete/rename denials for code, prompt, both active roots, public key and both keyrings; daemon mount policy | Isolated copies only |
| V04 | attachment/tmp create/fsync/rename/chmod, rootfs denial, attachment/trace persistence across restart | Real upload path also contributes V07 evidence |
| V05 | real strict key/entitlement loaders; root0600 and group0640 negatives; group-write entitlement negative; root ciphertext/new UID decrypt and digest match | Synthetic keys only |
| V06 | real HTTP frozen identity exchange/validation, issued cookie/CSRF/origin, anonymous rejection, full static byte comparison | Synthetic external identity; manual issued Secure cookie across the private HTTP proxy-equivalent hop; not browser/real SSO |
| V07 | real multipart TXT/CSV/PDF/DOCX/XLSX/uncompressed PNG >1 MiB; original/hash/manifest/chunks/normalization; exact clear OCR text | Never calls parser directly for acceptance; OCR warning fails |
| V08 | nonempty old-root copy, real startup expired/unexpired GC and partial/batch cleanup, owner/other HTTP read/delete | **PARTIAL: archive-protected GC is NOT_EXECUTED** |
| V09 | real JsonlTraceSink, preserved prefix across restart, expected RO negative with captured warning and failed write gate | Health alone never passes this gate |
| V10 | true HTTP minimal complete task through deterministic provider boundary, real PG terminal/context/history, restart same-key replay, next-turn attachment read | Provider class method substituted before app import; app module bytes/config/signatures/repositories unchanged; no model/quality claim |
| V11 | same-image old-root baseline, current old+new attachments/trace, root-created owner, stopped fresh-copy re-forward; PG never restored | Root history decrypt requires V10 in selected gates; otherwise explicit PARTIAL |

V08's missing protected-GC case needs an authentic archived relation produced
through an enabled archive flow in a separately labelled candidate. Production
archive remains false here; the harness does not fabricate successful sessions or
claim an empty relation is archival protection. Any PARTIAL makes the overall CLI
return nonzero; this tooling alone cannot close the full release matrix.

Local engineering verification:

```sh
python -m pytest tests/test_native_security_probe.py -q
python -m py_compile deploy/probe_nonroot_runtime.py
```

Tests substitute the Docker daemon and root chown where explicitly stated, use
real temporary files and crypto/store modules, include an actual loopback identity
HTTP fixture, and include a separate **development ASGI** scripted-provider loop
check. None of those are production-mode native, real-PG, OCR-process, rollback,
browser or production acceptance. Native V01–V11 remain NOT_EXECUTED until run on
the reviewed native host. Real model calls and input/output model tokens are zero.

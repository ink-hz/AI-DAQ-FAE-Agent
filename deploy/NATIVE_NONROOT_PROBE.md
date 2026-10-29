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
  --workroot /opt/daq-native-probes/UNIQUE-ATTEMPT \
  --runtime-release daq-062f0a7ae744 \
  --main-internal /app/data/knowledge/reviewed-thinking-prefix-20261028 \
  --sdk-internal /app/data/knowledge/sdk-thinking-prefix-20261028
```

Deploy this script together with `security_assets.py` from the **same checkout**.
The reviewed dependency is commit `07b2fc354fc3e5e8609c2a03f15b3450d3b884ce`;
its required SHA-256 is
`451dafbd88b4f3b07836940ad4500b34af63ae9796f0f676888435a06117061b`.
The native branch merges that full approved branch and checks the helper bytes
before using its descriptor-based copy/identity primitives. It neither imports a
sibling worktree nor keeps a second copytree implementation.

Create the root-owned, mode-0700 `/opt/daq-native-probes` parent beforehand.
Input/workroot paths must be canonical absolute paths: dot/dotdot, duplicate
separators, links, special files, hardlinks and untrusted writable ancestors are
rejected. `/var/tmp` and `/tmp` are unsuitable controller parents. The workroot
itself must not exist. Held descriptors and device/inode ancestry checks reject
source/destination overlap and aliases before destination creation. Prepared RO
roots and all their members must be root-owned and not group/other writable;
synthetic RW trees instead require a trusted parent and stopped writers.

The stable native handoff v1 accepts only `active.json` and
`releases/<manifest-sha256>/manifest.json`, with exact directories and active
pointer membership. Main is exactly 2 files/9,412,265 bytes; SDK is exactly
15 files/1,329,624 bytes (design section 9). Runtime still performs the actual
signed loaders. Bounds are 4,096 nodes, depth 32, 16 MiB/file, 32 MiB/tree and
32 MiB combined input; free-space preflight reserves input bytes plus a 256 MiB
synthetic-data allowance and 1 GiB margin. The approved copy primitive also
retains its own 64 MiB/file and 256 MiB total drift limits. Membership, metadata,
identity and hashes are checked before/after copying, with inventory evidence.
The supplied RO inputs are copied into the exclusive workroot. All write/delete/rename negatives
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

`journal.json` is fsynced before each Docker create. A lost create result (including nonzero CLI exit) is
reconciled only by exact unique name, owner/role labels and image ID. A current
not-found response cannot prove that an in-flight create will never materialize:
the lease remains blocked. SIGTERM/SIGHUP kill and reap the separately sessioned
Docker client group, then reconcile owned objects; SIGKILL cannot run cleanup. Cleanup
requires actual stopped state and subsequent full-ID and name absence. A failure
leaves a blocked journal and unreleased `/run/daq-native-probe/active.json` lease;
starting another workroot is refused. Retain all files and use only:

```sh
python3 deploy/probe_nonroot_runtime.py cleanup --workroot /opt/daq-native-probes/UNIQUE-ATTEMPT
```

Do not remove the global lease to bypass a blocked cleanup. Do not use Docker
prune, broad rm, restart source containers, production Nginx commands or production
secrets. Recovery procedure for unknown create:

1. Retain the workroot/journal and lease. Use the ordinary cleanup command above
   to inspect the exact durable name. If the delayed object has materialized,
   matching labels/image/full identity permit stop, remove and absence checks.
   Another not-found keeps the attempt blocked; do not retry the workload.
2. If it never materializes, the tool has no API proof that the old request is
   cancelled. A daemon restart alone is not accepted as evidence. Arrange any
   **host reboot separately under the host owner's existing authorization**;
   this CLI never reboots a host. Preserve the private workroot on durable disk.
3. After that actual reboot, use
   `python3 deploy/probe_nonroot_runtime.py cleanup --after-host-reboot --workroot /opt/daq-native-probes/UNIQUE-ATTEMPT`.
   The journal's recorded kernel boot ID must differ from the current kernel boot
   ID. That proves the pre-reboot client/daemon request cannot execute later;
   any retained exact owned objects still undergo normal stop/removal/absence
   checks. Missing `/run` lease is allowed only in this verified reboot path.
   The report retains both boot IDs. Missing/unchanged boot evidence refuses
   recovery. Existing labelled daemon residue blocks fresh attempts.

Never delete or hand-edit a lease/journal to bypass this procedure.

`report.json` has fixed safe result categories. Private `output/` retains synthetic
fixtures, input hashes, issued synthetic session credentials, intermediate evidence
and task receipts. Keep the workroot private; do not attach the whole directory to
a public issue. Provider/identity substitutions are recorded before each app starts. Unique phase
files preserve worker evidence and each app's warnings, including shutdown
warnings copied before and after every stop. Warnings block success; ordinary
application logs are disabled. Bounded process evidence records PID/starttime,
parent, UID/GID/capabilities/NNP, namespaces and entry identity. Journal audits
retain actual APP/worker/PostgreSQL policy, tmpfs and mount-source identities. The full synthetic old trace prefix,
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
| V08 | nonempty old-root copy, real startup expired/unexpired GC and partial/batch cleanup, owner/other HTTP read/delete | Mandatory normal GC/owner/migration can PASS; archive protection is separately NOT_EXECUTED |
| V09 | real JsonlTraceSink, preserved prefix across restart, expected RO negative with captured warning and failed write gate | Health alone never passes this gate |
| V10 | true HTTP minimal complete task through deterministic provider boundary, real PG normal persistence/restored context/revision/attachments/history, exact-event replay without provider calls, actual read receipts on both turns | Provider class method substituted before app import; app module bytes/config/signatures/repositories unchanged; no model/quality claim |
| V11 | same-image old-root baseline, current old+new attachments/trace, root-created owner, stopped fresh-copy re-forward; PG never restored | Root history decrypt requires V10 in selected gates; otherwise explicit PARTIAL |

Archive protection is separately `NOT_EXECUTED` with
`disabled_by_source_configuration`: the pinned source has archive=false. It is
not counted as a pass and does not make mandatory V08 PARTIAL. An enabled archive
scenario needs a separately reviewed candidate and authentic archived relation.
V10 verifies normal successful persistence; transaction-failure atomicity is
separately `NOT_EXECUTED` (`normal_success_scenario_only`). No PG fault-injection
claim is made. An actual failed or PARTIAL required gate still returns nonzero.

The second authenticated native turn asks “再读取上一轮日志中的status=ready。”
without resending attachment IDs, exercising the source's existing prior-attachment
reference contract. The first turn and both read receipts require the exact
unique marker/source/locator. The engineering development-route test supplies
explicit IDs on both turns because that different route requires them; it does
not establish native restored-attachment behavior.

Local engineering verification:

```sh
python -m pytest tests/test_native_security_probe.py -q
python -m py_compile deploy/probe_nonroot_runtime.py
```

Tests substitute the Docker daemon and root ownership/trusted-parent checks where
explicitly stated. Real subprocess SIGTERM/SIGHUP tests cover create/wait client
group reaping and delayed daemon materialization; no real Docker daemon is used.
Tests use
real temporary files and crypto/store modules, include an actual loopback identity
HTTP fixture, and include a separate **development ASGI** scripted-provider loop
check. None of those are production-mode native, real-PG, OCR-process, rollback,
browser or production acceptance. Native V01–V11 remain NOT_EXECUTED until run on
the reviewed native host. Real model calls and input/output model tokens are zero.

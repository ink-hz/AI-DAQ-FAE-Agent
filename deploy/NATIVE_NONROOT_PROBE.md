# Isolated native non-root probe

This is Task2b tooling, not deployment approval. `probe_nonroot_runtime.py` accepts
only the explicitly prepared main/SDK roots, the pinned **public** approval key,
exact local image IDs and a new private workroot. It has no production env-file,
DSN, private-keyring, network, endpoint, Docker-context or container input. The
source and candidate images are hard-pinned; `--pull never` is always used. No image is built.

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
  --candidate-image sha256:7360ead746ecef1303f27cc85b560ee13807af4a2245c8963f3eef94468586c7 \
  --pg-image sha256:REPLACE_WITH_REVIEWED_LOCAL_PG_IMAGE_ID \
  --workroot /opt/daq-native-probes/UNIQUE-ATTEMPT \
  --runtime-release daq-062f0a7ae744 \
  --main-internal /app/data/knowledge/reviewed-thinking-prefix-20261028 \
  --sdk-internal /app/data/knowledge/sdk-thinking-prefix-20261028
```

Deploy this script together with `security_assets.py` from the **same checkout**.
The reviewed dependency is commit `cfc1510e5a4a9b49d870195909cd560cbf32010c`;
its required SHA-256 is
`3d94c1ac3b395cfb551ec31005df8cdae2b42312ba0533f8de9fef7057553eea`.
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
use the original source image and old root/default-capability baseline. Non-root
APP and workers use the candidate image. Both phases use the current prepared
RW attachments and trace where applicable. Before creating any controller
resources, read-only image inspection checks both pinned IDs, amd64 Linux,
candidate base-layer prefix plus two layers, default user, the three exact
policy/base/recipe labels and all other application Config fields against the
source. Reports retain only identities, digests and booleans, not Config.Env.

`journal.json` is fsynced before each Docker create. A lost create result (including nonzero CLI exit) is
reconciled only by exact unique name, owner/role labels and image ID. A current
not-found response cannot prove that an in-flight create will never materialize:
the lease remains blocked. SIGTERM/SIGHUP kill and reap the separately sessioned
Docker client group, then reconcile owned objects; SIGKILL cannot run cleanup. Inspect absence accepts only return code 1, empty stdout or `[]` (surrounding
whitespace permitted), and exactly one supported full-target error line:
`Error: No such container: <target>` or
`Error response from daemon: No such container: <target>` (optional final newline).
Other codes, nonempty JSON, wrong targets or extra text remain unknown. This
protocol adaptation does not release an unknown create merely because it is
currently absent. Cleanup
requires actual stopped state and subsequent full-ID and name absence. A failure
leaves a blocked journal and unreleased `/run/daq-native-probe/active.json` lease;
starting another workroot is refused. Cleanup walks all journaled objects in
reverse dependency order even when one is unresolved or fails inspection,
ownership, stop or removal. Each independently confirmed owned object is still
stopped/removed and absence checked; fixed per-object errors are persisted and
aggregated only after traversal. Journal writes have their own failure boundary,
including initial reboot bookkeeping: persistent IO failure cannot skip an
independently confirmed owned object's stop/absence checks. Operation errors and
journal-persistence errors retain separate categories. Any failed journal write
keeps the attempt blocked and returns nonzero even if all workloads were removed;
when storage cannot accept evidence, the existing unreleased lease is retained.
Any unresolved object likewise keeps the lease blocked.
Retain all files and use only:

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
a public issue. Runtime failure receipts may include `missing_module` only for a
`ModuleNotFoundError.name` matching ASCII dotted identifiers of at most 160
characters. This stays in private evidence; exception messages and raw traceback
are never copied. Provider/identity substitutions are recorded before each app starts. Unique phase
files preserve worker evidence and each app's warnings, including shutdown
warnings copied before and after every stop. Warnings block success; ordinary
application logs are disabled. Bounded process evidence records PID/starttime,
parent, UID/GID/capabilities/NNP, namespaces and entry identity. Journal audits
retain actual APP/worker/PostgreSQL policy, tmpfs and mount-source identities. The full synthetic old trace prefix,
old root attachment snapshot, current data and re-forward copy remain available.
No production data is preserved there because none is admitted.

Initial APP readiness is followed immediately by a host-side, bounded,
read-only filesystem snapshot of interrupted directories and expired/retained
legacy fixtures, persisted as `startup-maintenance.json`. It precedes all probe
attachment consumers. The readiness worker only checks health, PG identity and
proc/filesystem evidence; it never constructs `AttachmentStore`. The actual
store/tools may perform maintenance later, but cannot retroactively change the
recorded startup result. A failed startup prerequisite is V08 FAIL even when a
later consumer could clear the leftovers.

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
| V11 | original-image old-root baseline, current old+new attachments/trace, root-created owner, stopped fresh-copy re-forward; PG never restored | Root history decrypt requires V10 in selected gates; otherwise explicit PARTIAL |

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
browser or production acceptance. The parent's first native batch at `b40a6e8` failed V01 before migration because
immutable image directories were unreadable by nonroot; V02–V11 were not executed.
That failure remains preserved. The inspect-protocol fix is local engineering
only; original-batch cleanup and any later native run are separate parent actions.
Real model calls and input/output model tokens are zero.


## Assembly correction after the first derived-image batch

The first derived-image batch at `e0aa155` failed V01 after 14.615 seconds;
actual migration and root seed passed, APP recorded ValueError, cleanup released.
No frames were recorded then, so its throwing function is unknown. Those original
receipts remain unchanged. A separate pure-source reproduction confirms that the
old probe Origin `https://native-probe.invalid` is rejected by the real product
allowlist. The next positive assembly uses the already-approved string
`https://agent.orbbec.com.cn`; actual HTTP stays on private 127.0.0.1 ports. There
is no public-domain request/DNS dependency, allowlist change or real credential.
The old string stays an explicit configuration refusal and HTTP wrong-Origin403
negative. This corrects an agent-authored test configuration, not a business
input or gate criterion; a later corrected batch cannot be called the old pass.

Runtime failures now retain bounded private frame metadata: inspect at most 32
traceback nodes and emit the last at most 8 admitted frames within that inspected
prefix, with an explicit truncation flag. Allowed filenames are `/probe.py` and
Python files below `/app/daq_fae/`, `/app/src/`, `/app/scripts/`, ASCII identifier
components only, no dotdot/aliases, at most 240 characters. Function names are
ASCII identifiers up to 80 characters or the fixed Python module/lambda/comprehension
names; line numbers are integers 1..1,000,000. Unknown paths/functions are omitted.
Only filename/function/line and safe counts are retained: no formatted traceback,
source lines, message/args, locals, environment, request or knowledge bodies.
Existing FAIL/category/type and bounded missing_module fields remain. Engineering
tests compile fixed static filenames to simulate those paths; this is not native
image execution, and no metadata is backfilled into prior failures. Image/helper
pins, upload/task fixtures, provider substitution and all native gates are unchanged.


## One-owner HTTP scope and per-gate evidence

`http_client` is now a standard context manager: one real httpx Client is entered
once, session exchange (or saved-cookie restoration) occurs inside that scope,
and the same client is yielded and closed on authentication, consumer or normal
exit. All authenticated consumers—auth, upload, migrated, task, root rollback and
re-forward—enter this factory scope; helpers receiving a client only borrow it.
There is no second client swap, repeated exchange, swallowed RuntimeError or
client re-entry. Cookie/CSRF/Origin assertions and private HTTP endpoints remain.

The actual prior `01a1f8b` batch remains V01/V03/V04/V05 PASS, V06 FAIL, six gates
NOT_EXECUTED (27.398 seconds, cleanup released). Its V06 failure was the former
factory returning an already-used client followed by consumer re-entry. Local
real-httpx tests use only an owned random-port HTTP/session fixture and explicitly
do not establish native authenticated PG behavior.

The next parent-owned native selection is V02/V06/V07/V08/V09/V10/V11 plus required
V01. V03/V04/V05 evidence may be inherited only after checking unchanged relevant
functions/controller policy/context and the same actual image, kernel and config.
The report supplies source/AST hashes; it cannot prove host state remotely. Keep a
per-gate batch/version table, preserve the previous V06 FAIL, and never describe
this later eight-gate batch as eleven same-batch passes. The complete
upload→ownership→two-turn task→rollback story V07/V08/V10/V11 must close in the same
new batch with the original fixtures; unknown inheritance conditions require the
affected check instead of silently skipping it.

# Single-shot deployment action receipts

`security_action.py` is a Linux-root operator for one parent-reviewed literal argv.
It is not a phase controller. Parent holds the existing global deployment lease,
authorizes every operation, and owns phase A/B, exact daemon ownership, source
cold/material/FD/writer/PG proof, listener policy and physical inspect evidence.
It does not generate quiescence, material, PG or start-policy proof.

Use a **new**, existing root:root 0700 run directory on a filesystem with working
file/directory fsync. Every ancestor must be root-controlled, without symlinks or
group/other write access. Place a complete root:root 0600 regular, one-link intent
there (or in another equally controlled directory). Never use source/user data
as the run directory. Parent assigns one unique 32-hex attempt per new directory
and unique operation names. Cross-directory/global uniqueness remains parent-owned.
The operator never cleans, overwrites, retries, recovers or reuses an allocation.

Intent example (replace the CID, attempt and trusted real executable path):

```json
{"version":1,"run_attempt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","operation":"source-stop","role":"source","kind":"stop","argv":["/usr/bin/docker","stop","--time","-1","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],"expected":{"target_cid":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

The executable must be an absolute regular one-link trusted path, not a symlink;
resolve/review the actual installed path first. Roles are `source`, `candidate`,
`rollback`, `observer`, `listener`. Categories are `stop`, `rm`, `create`, `start`,
`listener_fence`, `listener_rearm`. Stop/rm/start require exactly `target_cid`
(64 lowercase hex). Create requires exactly `pending_name` (same name grammar as
operation), `image_id` and `config_identity` (both `sha256:` plus 64 lowercase hex).
Listener operations require exactly `config_identity`. Operation names match
`[a-z][a-z0-9-]{0,63}`; protocol-reserved names are rejected.

Metadata binds the reviewed action; the runner does not derive or authorize argv
from it. Parent must privately compare the **whole literal argv** to the expected
CID/name/image/config and current ownership before submission. No shell or secret
values may be supplied. In particular the observer DSN goes only in the parent's
private daemon `--env-file` path, never in argv. The runner uses `shell=False`,
empty environment and the run directory as cwd; it does not recognize arbitrary
credential formats or expand variables. These are root-operator obligations,
not an untrusted-user command authorization API.

```sh
python3 /reviewed/security_action.py submit --run-dir /root/private-run --attempt aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --operation source-stop --intent /root/private-run/stop-input.json --wait 2
python3 /reviewed/security_action.py status --run-dir /root/private-run --attempt aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --operation source-stop
```

Submission exclusively allocates the operation directory. Intent+SHA and an
`ack_pending.json` latch, then `may_have_occurred.json`, are persisted and file,
operation directory and run directory fsynced before fork. The detached supervisor
persists its PID/nonce and Linux boot ID/process start ticks in one exclusive claim,
then fsyncs the run directory before launching exactly one real child. All records
bind attempt, operation, role, kind, expected metadata, intent hash and argv hash.
JSON is bounded to 64 KiB; argv has at most 256 strings / 32 KiB total. Duplicate
keys, unknown fields, booleans as integer fields, links and unsafe paths fail closed.

The supervisor continuously drains stdout/stderr in 64 KiB chunks and retains only
byte counts and SHA-256, never output bodies in receipts. It has no child timeout
and never kills a source child on reporting failure. It waits for actual terminal
CLI status, durably saves the ACK and fsyncs both directories **before** removing
the pending latch. Latch removal need not be durable: a crash can conservatively
restore UNKNOWN, while the ACK itself was already durable before removal. Keep
all evidence unchanged, including partial temp files. A partial temp or extra
operation file makes status UNKNOWN.

`ACK` records the actual CLI return code, including nonzero or signal termination;
only return code 0 is CLI success. Successful create/start followed by immediate
application exit is still an ACK; there is no `Running=true` requirement. Parent
must separately inspect actual daemon state and content. Exit status is 0 only
for ACK/returncode 0; UNKNOWN, ABSENT, rejection or nonzero CLI exit returns 2.
Status is read-only and checks the complete intent/marker/claim/ACK binding.
ABSENT only means no operation allocation currently exists in this parent-owned
run directory. Empty, partial, delayed, interrupted and mismatched records are
UNKNOWN. A bounded `--wait` (0–60 seconds) bounds only the submitting CLI's wait;
its expiry or SSH disconnect does not cancel the supervisor or daemon operation.
SIGTERM/HUP/INT to the supervisor suppress ACK but continue draining/waiting for
a known child. SIGKILL cannot provide a receipt or cancel a daemon operation.
Do not replay, delete the latch, promote a record, infer cancellation or introduce
a second writer. Parent preserves UNKNOWN and independently reconciles the exact
object; this tool deliberately has no generic recovery/cleanup path.

Immediately before candidate start, parent reruns the reviewed assets helper
`verify` and retains the physical proofs. Phase B rollback uses original image
5ed plus **CURRENT NEW** attachments/trace and original root keys, without PG
restore or old-data resurrection. Source stop is `--time -1`; source removal
requires stopped/no-force/no-volumes plus parent material proof. This runner does
not enforce these phase policies or validate Docker state.

Local engineering check: `python3 -m unittest discover -s tests/security -p test_security_action.py -v`.
Tests substitute only the required root UID/owner boundary (ancestors may still be
root-owned); real subprocess/file persistence run locally. Explicit persistence
fault injection is separate from that boundary substitution. No Docker, SSH,
production DB, model, cloud, browser or release acceptance is performed.

# Controller's DAQ metadata observer

This is a production metadata actor, **not an isolated native acceptance candidate**. `OBSERVED` / `query_ok: true` means a bound read-only query succeeded. Even `running: 0` does not establish whole-service quiescence: the controller separately proves graceful drain, source exit, upload/partial writers, PIDs/cgroups, connections and sockets. Nonzero counts are valid observations. Missing, failed or interrupted results are never zero counts.

## Controller preparation and ownership

The parent controller owns every Docker operation, credentials, exact name/CID/labels, durable lifecycle intent/acknowledgement, network identity and cleanup proof under the approved cutover design. This script neither performs nor authorizes daemon actions. Use the exact already-local reviewed candidate image, UID/GID `21081:21081`, cap-drop ALL, no-new-privileges, read-only root, private tmpfs, no published ports and the single exact deployed DB bridge. Do not join the source container's network namespace or use the platform-edge network. Its network must survive source shutdown. Production role/superuser residual privileges remain unchanged; this tool does not claim role isolation.

Supply **only** the original normal `DAQ_DATABASE_URL` in a private controller-owned envfile passed to the daemon; no credentials in arguments, command text, logs, reports or expected JSON. Preserve original DSN options/search_path/SSL/target. Mount the observer script read-only, control at `/observer-control` read-only, output at `/observer-output` read-write. Root mount ancestors must be controlled and not aliases. Control directory: `root:21081 0750`; input files: `root:21081 0640`, regular, one link. Fresh output directory: `21081:21081 0700`, empty. No source mounts or complete source environment are needed.

Before creating the observer, the parent independently obtains the baseline through the **original source's normal DSN**, not through the new observer. Use the fixed metadata queries in `read_identity()` inside one read-only transaction with local statement timeout 3s, lock timeout 500ms and access-share locks on both relations. Collect no business body or subject identifiers. Check the installation marker and preserve exact database, role, `current_schema`, search_path, both resolved relation schemas/OIDs/kinds. They need not share `current_schema`. Do not invent a schema, install a marker, alter privileges or use the observer's first query as its own baseline.

Atomically publish `expected.json` before startup (maximum 8192 bytes):

```json
{"version":1,"attempt":"<32 lowercase hex>","identity":{"database":"<source>","user":"<source>","schema":"<source current_schema>","search_path":"<source exact setting>","relations":{"daq_request_ledger":{"schema":"<source resolved schema>","oid":123,"kind":"r"},"daq_installation_identity":{"schema":"<source resolved schema>","oid":456,"kind":"r"}},"marker":{"agent_id":"ai-daq-fae-agent","database_name":"<same source database>"}}}
```

OID numbers above are placeholders, not production values. The marker must be exactly one singleton row, agent `ai-daq-fae-agent`, bound to the actual database. Expected JSON is immutable for the whole attempt. Unknown fields, duplicate JSON keys, incorrect types, permissions, links and size limits fail closed. Production baseline remains a parent obligation.

## One request, one observation

Only public invocation: `python /mounted/observe_daq_quiescence.py --attempt <32hex> --lifetime 300`. Lifetime is 1–300 seconds; each phase has a 15-second total budget, connection timeout 3s, statement timeout 3s, lock timeout 500ms. No directory, SQL or host CLI override exists. `ready.json` means only IPC is ready; it makes no database claim.

The controller publishes one outstanding request at a time by creating a complete file **outside** the control directory and atomically renaming it into that directory. Keep all prior requests unchanged and present until the process exits. Names are exactly `request-01.json` through `request-16.json`; id is the matching JSON integer (not boolean), attempt matches, phase is `pre`, `post`, `final` or `stop`. Maximum request size 1024 bytes. No other filenames may enter the control directory.

```json
{"attempt":"<same 32hex>","id":1,"phase":"pre"}
```

The observer persists `claim-01.json` before running a query, then exclusively and atomically publishes `result-01.json`; each output is at most 8192 bytes, mode 0600. Same unchanged request does not query again; modified/deleted duplicates or out-of-order/multiple new requests fail. All metadata and aggregate checks occur on one connection, in a repeatable-read read-only transaction with access-share locks, then identity/settings are rechecked and the transaction commits/closes before publishing the result. Thus no idle transaction remains between phases. It reads only fixed metadata and counts for the DAQ agent: running, live lease, expired running, completed, interrupted, last update and one transaction as-of timestamp. It never clears leases or mutates shared PostgreSQL.

`OBSERVED` includes actual identity, `transaction_read_only: true`, observer backend PID, `observed_at`, nullable `last_update`, and honest integer `counts`. Live means lease strictly greater than the as-of timestamp; expired means less than or equal. Running equals live plus expired. The parent compares attempt/id/phase and source baseline before using the observation. Role sharing does not make other processes belong exclusively to DAQ.

A `stop` request emits `STOPPED` without querying and exits 0. Timeout, SIGTERM/HUP/INT, IPC or database failure exits nonzero and emits only fixed safe category and optional SQLSTATE to private `error.json`/stderr, never exception text/DSN/frames/content. If a result or error cannot be persisted, exit/loss is missing proof. A claim without result is indeterminate. Do not restart into the same output directory; existing evidence is refused. SIGKILL has no magic receipt or cleanup. Parent must reconcile the exact observer lifecycle, preserve evidence, stop/remove only its owned exact object and confirm absence before releasing ownership. Only unlink its own private envfile after inode/ownership checks; never delete the source envfile or source data.

## Local engineering evidence

Run only `python -m pytest tests/security/test_quiescence_observer.py -q --tb=short`. Tests use already-installed PostgreSQL 17 binaries and an owned temporary cluster, TCP SCRAM ordinary roles, actual DAQ migration schemas, and real subprocess/file IPC. No Docker, cloud, production DB, model or business messages are exercised. Local tests substitute macOS UID/GID and temporary mount paths for root/21081 fixed mounts; all file-descriptor/type/link/mode checks still run. Only synthetic marker schema creation is extracted from the real marker migration, excluding its unrelated shared-table guards. Query tests separately prove read-only write rejection and valid nonzero observations. Whole-service cutover and container packaging require independent controller validation.

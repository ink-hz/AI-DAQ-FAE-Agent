# Production D3 approval verifier v1

The authenticated DAQ app can supply the existing D3 publisher/loader with a
server-owned Ed25519 verifier. This adds no publisher, owner release mode,
permission grant, archive waiver, or local Dev opt-in. An authentic signature
must still pass the existing content, source, scope, archive, independent review,
Dev batch, and runtime identity checks.

## Trusted configuration

Configure both settings in the DAQ server environment:

```text
DAQ_KNOWLEDGE_APPROVAL_KEYS_FILE=/absolute/trusted/approval-keys.json
DAQ_KNOWLEDGE_APPROVAL_KEYS_SHA256=<64 lowercase hex SHA-256 of exact file bytes>
```

The file is a regular file at an absolute path. Symlinks in the file path are
rejected. Provision it in an operator-controlled directory with the application
having read access, and pin its exact raw bytes in the separately controlled
environment. Whitespace changes require a new pin. Relative paths, absent or
invalid files, wrong hashes, incomplete settings, and invalid schemas abort
startup with a bounded configuration error. File content, signatures, keys, and
paths are not included in that error.

The JSON object has exactly `format_version` and a nonempty `keys` array. Each
entry has exactly `reviewer` and `public_key_b64`:

```json
{
  "format_version": "daq-knowledge-approval-keys/v1",
  "keys": [
    {
      "reviewer": "independent-reviewer-identity",
      "public_key_b64": "<canonical base64 of the raw 32-byte Ed25519 public key>"
    }
  ]
}
```

The angle bracket value is a template placeholder. Reviewer names are exact,
unique UTF-8 strings, must contain non-whitespace text, and cannot contain NUL.
Duplicate JSON fields, duplicate reviewers, malformed base64, and keys of the
wrong size are rejected. Private keys never belong in this file, application
configuration, repository, or runtime.

`load_approval_verifier(path: Path, expected_sha256: str)` returns an immutable
callable over the loaded keys. It never rereads the file during verification.
Restart/reload with a newly reviewed pin to rotate trust; keep public keys needed
to verify the active release and rollback releases. Removing a reviewer key
invalidates releases signed by that reviewer when next loaded.

## Approval bytes and signature

The reviewer signs the canonical D3 payload digest already used by
`release_readiness.audit_readiness`: the digest of the full bundle except its
`approval` member. The digest must be 64 lowercase hexadecimal characters.

```python
from daq_fae.knowledge.adjudication import digest
from daq_fae.knowledge.approval_verifier import approval_message
import base64

payload_sha256 = digest({k: v for k, v in bundle.items() if k != "approval"})
message = approval_message(reviewer, payload_sha256)
# private_key is held by the independent reviewer outside the application.
signature = "ed25519:" + base64.b64encode(private_key.sign(message)).decode("ascii")
bundle["approval"] = {
    "reviewer": reviewer,
    "payload_sha256": payload_sha256,
    "signature": signature,
}
```

The signed bytes are exactly:

```text
b'daq-knowledge-release-approval/v1\0' + reviewer.encode('utf-8') + b'\0' + payload_sha256.encode('ascii')
```

Signatures use the exact `ed25519:` prefix and canonical base64 of a raw 64-byte
signature. The verifier returns `False` for unknown reviewers, malformed inputs,
wrong signatures/keys, different reviewer names, and changed payloads. Reviewers
sharing a key still cannot reuse one another's signatures because the identity
is part of the signed message.

Use the callback with the existing public `publish_release`, `activate_release`,
and `ReviewedKnowledge.load_active` APIs. The application factory passes it to
the default active loader and retains the trusted process runtime/upstream
identity checks. The existing observation adapter and independent release review
remain necessary; a signature does not establish that an attested archive or
answer-quality report is truthful.

## Factory compatibility and verification

When neither setting is present, factory behavior is unchanged, including the
existing supported owner releases and rejection of D3 releases without trusted
verification. If a server caller explicitly injects
`knowledge_approval_verifier`, that dependency takes precedence over environment
configuration, including malformed/incomplete settings. HTTP clients cannot
select this dependency. No environment setting enables `allow_local_dev`.

Focused tests in `tests/test_knowledge_approval_verifier.py` use generated,
synthetic keys and real Ed25519 signatures. They exercise public D3 publication,
activation, actual active loading with runtime identity, authenticated factory
loading and identity routes, trust-file mutation, and local Dev rejection. No
model request or production evaluation is involved.

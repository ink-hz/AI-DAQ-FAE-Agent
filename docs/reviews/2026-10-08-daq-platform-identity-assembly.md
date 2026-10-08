# DAQ Platform identity assembly

This batch adds `daq_fae.platform_identity.configure_platform_identity(app)`;
it does not enable identity in `daq_fae/app.py` or register Platform resources.
Call it before ASGI startup, after attachment lifespan assembly. Enabled mode
wraps the existing lifespan and closes the owned Platform client on shutdown.

## Trusted configuration

Identity is off by default for the existing local Dev service. To enable it,
set `DAQ_PLATFORM_IDENTITY_ENABLED=true` and supply all of:

- `DAQ_DATABASE_URL`: dedicated DAQ database/user; no FAE fallback.
- `DAQ_PLATFORM_IDENTITY_BASE_URL`: shared client's allowed private backchannel.
- `DAQ_PLATFORM_PUBLIC_ORIGIN`: allowed HTTPS browser origin, normally
  `https://agent.orbbec.com.cn` for the internal workspace.
- `DAQ_PLATFORM_SESSION_KEYRING_FILE`: absolute path to a DAQ-only session HMAC
  keyring with the shared keyring file format and restrictive permissions.
- `DAQ_PLATFORM_ALLOWED_SUBJECT_IDS`: comma-separated trusted enterprise-member
  subject UUIDs authorized for the pilot. Partner operators are refused.

Missing/invalid enabled configuration fails assembly. The trusted Agent ID is
fixed to `ai-daq-fae-agent`; it is never selected by browser input. Session
cookie `__Host-daq_enterprise_session` and CSRF header `X-DAQ-Enterprise-CSRF`
are separate from the old FAE browser scope. Shared route parameters retain
old FAE defaults. Origin, CSRF, cached session and foreign Agent checks remain
mandatory. API roots for chat/history/attachments/feedback/conversations/review
require an authorized DAQ identity when enabled.

The pilot entry allowlist is not a per-document entitlement system. Retrieval
must enforce document visibility independently. Identity assembly alone does
not provide durable conversation ownership, review-specific authorization,
Platform registration, DB migrations, external serving, or production approval.
Application integration must finish those contracts before enabling a pilot.

## Dependencies and verification

Requires upstream revision `46c00c6003d01bf90398daa5b16e7597d58e194f`, whose
identity route change adds `cookie_name` and `csrf_header` parameters. Root integration must sync that exact file and record
its upstream revision in the temporary source snapshot manifest before testing
against the DAQ-local `src/` tree. This branch changes no copied runtime files.

DAQ contract tests use an injected in-memory repository and fake Platform
backchannel: launch/read/revoke, secure dedicated cookie, CSRF, missing identity,
missing config, allowlist/partner rejection, cached foreign record rejection,
trusted client Agent ID, and lifespan cleanup. No production requests or model
replays are needed for this assembly batch. Platform registration remains a
separate integration requirement.

Verification in the isolated batch worktree: 43 DAQ Python tests passed with
`src` explicitly selected from upstream `46c00c6` (18 identity contracts);
235 UI tests and the TypeScript/Vite build passed. Ruff and diff whitespace
checks passed. Direct DAQ-local identity tests require the upstream route sync
first; success against the override does not claim that snapshot integration
is already complete. Upstream identity/capability regression: 103 passed.
The broader upstream run had one existing health model-profile expectation
mismatch, reproduced with the unmodified `3d0b06d` route baseline.

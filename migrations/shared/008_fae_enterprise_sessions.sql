create extension if not exists pgcrypto;

create table if not exists fae_enterprise_sessions (
    session_id uuid primary key default gen_random_uuid(),
    session_token_hash bytea not null,
    session_token_key_version integer not null check (session_token_key_version > 0),
    csrf_token_hash bytea not null,
    csrf_token_key_version integer not null check (csrf_token_key_version > 0),
    internal_user_id uuid not null,
    identity_binding_id uuid not null,
    agent_id text not null check (agent_id = 'ai-fae-agent'),
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    last_validated_at timestamptz not null default now(),
    idle_expires_at timestamptz not null,
    absolute_expires_at timestamptz not null,
    revoked_at timestamptz null,
    unique (session_token_hash),
    check (idle_expires_at <= absolute_expires_at)
);

create index if not exists idx_fae_enterprise_sessions_binding
    on fae_enterprise_sessions(identity_binding_id)
    where revoked_at is null;

create index if not exists idx_fae_enterprise_sessions_expiry
    on fae_enterprise_sessions(idle_expires_at, absolute_expires_at)
    where revoked_at is null;

comment on table fae_enterprise_sessions is
    'FAE-owned browser sessions bound to Platform identity; plaintext browser secrets and DingTalk identifiers are forbidden.';

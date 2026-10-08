-- Explicit DAQ-only migration. Application startup does not run DDL.
create table if not exists daq_state_sessions (
    agent_id text not null check (agent_id = 'ai-daq-fae-agent'),
    external_session_id text not null check (external_session_id like 'daq:%'),
    owner_subject_id uuid not null,
    primary key (agent_id, external_session_id),
    unique (agent_id, external_session_id, owner_subject_id)
);
create table if not exists daq_request_ledger (
    agent_id text not null check (agent_id = 'ai-daq-fae-agent'),
    owner_subject_id uuid not null,
    client_request_id text not null check (length(client_request_id) between 1 and 128),
    external_session_id text not null,
    payload_sha256 bytea not null,
    execution_token uuid not null,
    state text not null check (state in ('running', 'completed', 'interrupted')),
    lease_expires_at timestamptz not null,
    terminal_ciphertext bytea,
    terminal_key_version integer,
    terminal_sha256 bytea,
    interruption_reason text,
    updated_at timestamptz not null default clock_timestamp(),
    primary key (agent_id, owner_subject_id, client_request_id),
    foreign key (agent_id, external_session_id, owner_subject_id)
        references daq_state_sessions (agent_id, external_session_id, owner_subject_id),
    check ((state = 'completed') = (terminal_ciphertext is not null)),
    check ((terminal_ciphertext is null) = (terminal_key_version is null)),
    check ((terminal_ciphertext is null) = (terminal_sha256 is null))
);
create unique index if not exists daq_request_one_running_session
    on daq_request_ledger (agent_id, external_session_id) where state = 'running';
create table if not exists daq_context_checkpoints (
    agent_id text not null check (agent_id = 'ai-daq-fae-agent'),
    owner_subject_id uuid not null,
    external_session_id text not null,
    revision bigint not null check (revision > 0),
    state_ciphertext bytea not null,
    state_key_version integer not null,
    state_sha256 bytea not null,
    updated_at timestamptz not null default clock_timestamp(),
    primary key (agent_id, external_session_id),
    foreign key (agent_id, external_session_id, owner_subject_id)
        references daq_state_sessions (agent_id, external_session_id, owner_subject_id)
);

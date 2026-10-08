-- Attachment relationship metadata only. No attachment bytes or local filesystem paths.
create table if not exists chat_turn_attachments (
    id uuid primary key default gen_random_uuid(),
    turn_id uuid not null references chat_turns(id) on delete restrict,
    external_session_id text not null,
    trace_id text not null,
    attachment_id text not null,
    source_id text not null,
    direction text not null check (direction in ('user_input', 'agent_output')),
    ordinal integer not null check (ordinal >= 0),
    association_kind text not null check (
        association_kind in ('explicit_current_turn', 'session_context')
    ),
    display_name text not null,
    kind text not null check (
        kind in ('image', 'pdf', 'document', 'spreadsheet', 'text', 'code')
    ),
    media_type text not null,
    size_bytes bigint not null check (size_bytes >= 0),
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null,
    processing_expires_at timestamptz not null,
    handoff_deadline_at timestamptz not null,
    archive_status text not null default 'pending' check (
        archive_status in (
            'pending', 'failed', 'archived', 'deletion_pending',
            'expired_unarchived', 'deleted'
        )
    ),
    archive_attempt_count integer not null default 0 check (archive_attempt_count >= 0),
    last_archive_error text not null default '',
    platform_attachment_id uuid null,
    archived_at timestamptz null,
    thumbnail_status text not null check (
        thumbnail_status in ('not_applicable', 'pending', 'ready', 'unavailable')
    ),
    thumbnail_media_type text null,
    thumbnail_size_bytes bigint null check (
        thumbnail_size_bytes is null or thumbnail_size_bytes >= 0
    ),
    thumbnail_sha256 text null check (
        thumbnail_sha256 is null or thumbnail_sha256 ~ '^[0-9a-f]{64}$'
    ),
    updated_at timestamptz not null default now(),
    unique (turn_id, direction, ordinal),
    unique (turn_id, attachment_id),
    check (handoff_deadline_at >= processing_expires_at),
    check (
        archive_status <> 'archived'
        or (platform_attachment_id is not null and archived_at is not null)
    ),
    check (
        thumbnail_status <> 'ready'
        or (
            thumbnail_media_type = 'image/webp'
            and thumbnail_size_bytes is not null
            and thumbnail_sha256 is not null
        )
    )
);

create index if not exists idx_chat_turn_attachments_archive_queue
    on chat_turn_attachments (archive_status, handoff_deadline_at, id);
create index if not exists idx_chat_turn_attachments_attachment_status
    on chat_turn_attachments (attachment_id, archive_status);
create index if not exists idx_chat_turn_attachments_session_turn
    on chat_turn_attachments (external_session_id, turn_id, ordinal);

comment on table chat_turn_attachments is
    'Safe FAE turn-to-attachment metadata; no attachment bytes or local filesystem paths.';

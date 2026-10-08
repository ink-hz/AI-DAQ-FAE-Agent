-- Durable private Platform task queue. No Platform cookie, DingTalk identifier,
-- object key, or plaintext task content is stored in these tables.
create table if not exists platform_tasks (
    task_id uuid primary key default gen_random_uuid(),
    platform_task_id uuid not null,
    idempotency_key text not null,
    request_sha256 bytea not null check (octet_length(request_sha256) = 32),
    request_ciphertext bytea not null,
    request_key_version integer not null check (request_key_version > 0),
    requester_internal_user_id uuid not null,
    capability_version integer not null check (capability_version > 0),
    authorized_scopes text[] not null check (cardinality(authorized_scopes) > 0),
    status text not null default 'queued' check (
        status in (
            'queued', 'running', 'waiting_input', 'waiting_confirmation',
            'completed', 'failed', 'cancelled', 'timed_out'
        )
    ),
    cancel_requested boolean not null default false,
    next_event_seq integer not null default 1 check (next_event_seq > 0),
    next_message_seq integer not null default 1 check (next_message_seq > 0),
    deadline_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz null,
    terminal_at timestamptz null,
    unique (platform_task_id),
    unique (idempotency_key),
    check (
        (status in ('completed', 'failed', 'cancelled', 'timed_out'))
        = (terminal_at is not null)
    )
);

create table if not exists platform_task_events (
    event_id uuid primary key default gen_random_uuid(),
    task_id uuid not null references platform_tasks(task_id) on delete restrict,
    seq integer not null check (seq > 0),
    kind text not null check (
        kind in (
            'thinking_summary', 'message', 'work_update', 'artifact',
            'input_required', 'action_required', 'finding', 'result',
            'failed', 'timeout', 'cancelled'
        )
    ),
    payload_sha256 bytea not null check (octet_length(payload_sha256) = 32),
    payload_ciphertext bytea not null,
    payload_key_version integer not null check (payload_key_version > 0),
    created_at timestamptz not null,
    unique (task_id, seq)
);

create table if not exists platform_task_messages (
    message_id uuid primary key default gen_random_uuid(),
    task_id uuid not null references platform_tasks(task_id) on delete restrict,
    message_seq integer not null check (message_seq > 0),
    idempotency_key text not null,
    content_sha256 bytea not null check (octet_length(content_sha256) = 32),
    content_ciphertext bytea not null,
    content_key_version integer not null check (content_key_version > 0),
    created_at timestamptz not null,
    unique (task_id, message_seq),
    unique (task_id, idempotency_key)
);

create table if not exists platform_task_cancel_requests (
    cancel_request_id uuid primary key default gen_random_uuid(),
    task_id uuid not null references platform_tasks(task_id) on delete restrict,
    idempotency_key text not null,
    created_at timestamptz not null,
    unique (task_id, idempotency_key)
);

create index if not exists idx_platform_tasks_claim
    on platform_tasks (status, created_at, task_id)
    where status = 'queued';
create index if not exists idx_platform_tasks_deadline
    on platform_tasks (deadline_at, task_id)
    where status not in ('completed', 'failed', 'cancelled', 'timed_out');
create index if not exists idx_platform_task_events_page
    on platform_task_events (task_id, seq);

create or replace function prevent_platform_task_terminal_transition()
returns trigger
language plpgsql
as $$
begin
    if old.status in ('completed', 'failed', 'cancelled', 'timed_out')
       and new is distinct from old then
        raise check_violation using message = 'terminal task status is immutable';
    end if;
    return new;
end;
$$;

drop trigger if exists platform_task_terminal_guard on platform_tasks;
create trigger platform_task_terminal_guard
before update on platform_tasks
for each row execute function prevent_platform_task_terminal_transition();

create or replace function append_platform_task_event(
    selected_task_id uuid,
    selected_kind text,
    selected_payload_sha256 bytea,
    selected_payload_ciphertext bytea,
    selected_payload_key_version integer,
    selected_created_at timestamptz
)
returns table(event_seq integer, task_status text)
language plpgsql
as $$
declare
    current_status text;
    allocated_seq integer;
    target_status text;
begin
    select status, next_event_seq
      into current_status, allocated_seq
      from platform_tasks
     where task_id = selected_task_id
     for update;
    if not found then
        raise no_data_found using message = 'platform task not found';
    end if;
    if current_status in ('completed', 'failed', 'cancelled', 'timed_out') then
        raise check_violation using message = 'terminal task status is immutable';
    end if;

    target_status := case selected_kind
        when 'work_update' then 'running'
        when 'input_required' then 'waiting_input'
        when 'action_required' then 'waiting_confirmation'
        when 'result' then 'completed'
        when 'failed' then 'failed'
        when 'timeout' then 'timed_out'
        when 'cancelled' then 'cancelled'
        else current_status
    end;

    insert into platform_task_events (
        task_id, seq, kind, payload_sha256, payload_ciphertext,
        payload_key_version, created_at
    ) values (
        selected_task_id, allocated_seq, selected_kind, selected_payload_sha256,
        selected_payload_ciphertext, selected_payload_key_version, selected_created_at
    );

    update platform_tasks
       set status = target_status,
           next_event_seq = allocated_seq + 1,
           started_at = case
               when target_status = 'running' then coalesce(started_at, selected_created_at)
               else started_at
           end,
           terminal_at = case
               when target_status in ('completed', 'failed', 'cancelled', 'timed_out')
               then selected_created_at
               else null
           end,
           updated_at = selected_created_at
     where task_id = selected_task_id;

    return query select allocated_seq, target_status;
end;
$$;

comment on table platform_tasks is
    'Encrypted Platform-owned task envelope; no Platform cookie, DingTalk identifier, object key, or plaintext task content.';

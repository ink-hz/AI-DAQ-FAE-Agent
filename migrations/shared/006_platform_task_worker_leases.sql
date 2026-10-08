-- Recoverable worker ownership for queued/running Platform tasks.
alter table platform_tasks
    add column if not exists lease_owner text null,
    add column if not exists lease_expires_at timestamptz null,
    add column if not exists attempt_count integer not null default 0
        check (attempt_count >= 0);

alter table platform_tasks
    drop constraint if exists platform_task_lease_pair;
alter table platform_tasks
    add constraint platform_task_lease_pair check (
        (lease_owner is null) = (lease_expires_at is null)
    );

create index if not exists idx_platform_tasks_reclaim
    on platform_tasks (lease_expires_at, created_at, task_id)
    where status in ('queued', 'running');

create index if not exists idx_platform_tasks_expire
    on platform_tasks (deadline_at, created_at, task_id)
    where status not in ('completed', 'failed', 'cancelled', 'timed_out');

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
           lease_owner = case
               when target_status in ('completed', 'failed', 'cancelled', 'timed_out')
               then null else lease_owner
           end,
           lease_expires_at = case
               when target_status in ('completed', 'failed', 'cancelled', 'timed_out')
               then null else lease_expires_at
           end,
           updated_at = selected_created_at
     where task_id = selected_task_id;

    return query select allocated_seq, target_status;
end;
$$;

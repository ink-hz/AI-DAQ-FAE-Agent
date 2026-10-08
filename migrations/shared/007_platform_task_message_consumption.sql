-- Durable follow-up consumption state. Message content remains encrypted.
alter table platform_task_messages
    add column if not exists consumed_at timestamptz null;

create index if not exists idx_platform_task_messages_pending
    on platform_task_messages (task_id, message_seq)
    where consumed_at is null;

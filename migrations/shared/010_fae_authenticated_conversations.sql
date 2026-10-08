begin;

-- Durable generic ownership for authenticated FAE conversations.
-- FAE deploys replay every migration and keep no ledger, so this file must be
-- additive and re-runnable. deploy/scripts/migrate_pg.sh pipes each file to
-- psql without --single-transaction, so the file opens its own transaction:
-- owner columns, guards, backfill, shape constraint, checkpoint table and index
-- must become visible together or not at all, on first deploy and every replay.
-- Public/anonymous conversations keep working exactly as before: they stay
-- ownerless and are excluded from every authenticated read path.

-- Read-only preflight. An authenticated enterprise conversation whose user_id
-- is missing or not a UUID cannot be attributed to a Platform subject. Stop the
-- release for manual classification instead of coercing ownership. This runs
-- before any schema change, so a malformed database is left untouched.
do $preflight$
declare
    malformed bigint;
begin
    select count(*) into malformed
    from public.chat_sessions
    where coalesce(metadata ->> 'authentication_mode', 'public_customer')
              = 'platform_enterprise'
      and (
          user_id is null
          or user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      );
    if malformed > 0 then
        raise exception
            'fae_authenticated_conversation_owner_preflight_failed: % authenticated chat_sessions row(s) have a missing or non-uuid user_id; classify them manually before release',
            malformed;
    end if;
end
$preflight$;

alter table chat_sessions
    add column if not exists owner_subject_id uuid,
    add column if not exists owner_subject_type text;

-- Rolling deploy and rollback compatibility: pre-v10 enterprise code inserts
-- chat_sessions without owner columns. Derive the enterprise owner from the
-- row's own trusted user_id so those conversations stay visible to their owner.
-- Public rows carry no authenticated mode, so they are never given an owner.
create or replace function chat_sessions_derive_owner()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if new.owner_subject_id is null
       and new.owner_subject_type is null
       and new.metadata ->> 'authentication_mode' = 'platform_enterprise'
       and new.user_id is not null then
        new.owner_subject_id := new.user_id::uuid;
        new.owner_subject_type := 'enterprise_member';
    end if;
    return new;
end;
$$;

drop trigger if exists chat_sessions_derive_owner on chat_sessions;

create trigger chat_sessions_derive_owner
    before insert on chat_sessions
    for each row
    execute function chat_sessions_derive_owner();

-- Session identifiers and owner subject identifiers are immutable. The only
-- legal null-to-owner transition is this file's deterministic enterprise
-- backfill from the row's own trusted user_id, which a public conversation can
-- never satisfy, so a public conversation can never be adopted by a subject.
create or replace function chat_sessions_guard_owner()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if new.id is distinct from old.id
       or new.external_session_id is distinct from old.external_session_id then
        raise exception 'chat_session_external_id_immutable';
    end if;
    if old.owner_subject_id is null and old.owner_subject_type is null then
        if new.owner_subject_id is null and new.owner_subject_type is null then
            return new;
        end if;
        if new.owner_subject_type = 'enterprise_member'
           and old.metadata ->> 'authentication_mode' = 'platform_enterprise'
           and old.user_id is not null
           and new.owner_subject_id = old.user_id::uuid then
            return new;
        end if;
        raise exception 'chat_session_owner_immutable';
    end if;
    if new.owner_subject_id is distinct from old.owner_subject_id
       or new.owner_subject_type is distinct from old.owner_subject_type then
        raise exception 'chat_session_owner_immutable';
    end if;
    return new;
end;
$$;

drop trigger if exists chat_sessions_guard_owner on chat_sessions;

create trigger chat_sessions_guard_owner
    before update on chat_sessions
    for each row
    execute function chat_sessions_guard_owner();

-- Backfill only unowned authenticated enterprise rows. Replays match nothing
-- because owned rows are already excluded, and partner rows are never rewritten.
update chat_sessions
set owner_subject_id = user_id::uuid,
    owner_subject_type = 'enterprise_member'
where owner_subject_id is null
  and owner_subject_type is null
  and metadata ->> 'authentication_mode' = 'platform_enterprise'
  and user_id is not null;

alter table chat_sessions
    drop constraint if exists chat_sessions_owner_shape;

-- Owner columns stay nullable: a null owner is exactly a public/anonymous
-- conversation, which must keep working unchanged.
alter table chat_sessions
    add constraint chat_sessions_owner_shape check (
        (owner_subject_id is null and owner_subject_type is null)
        or (
            owner_subject_id is not null
            and owner_subject_type in ('enterprise_member', 'partner_operator')
        )
    );

create index if not exists idx_chat_sessions_authenticated_owner
    on chat_sessions (owner_subject_id, last_active_at desc, id desc)
    where owner_subject_id is not null;

-- Sealed session state for restoring an authenticated conversation after the
-- in-memory cache is gone. Turn content remains in its existing table; this is
-- a derived, encrypted projection, not a second conversation source of truth.
create table if not exists chat_session_checkpoints (
  external_session_id text primary key
    references chat_sessions(external_session_id) on delete cascade,
  owner_subject_id uuid not null,
  state_ciphertext bytea not null,
  state_key_version integer not null check (state_key_version > 0),
  state_sha256 bytea not null check (octet_length(state_sha256)=32),
  message_count integer not null check (message_count >= 0),
  updated_at timestamptz not null default clock_timestamp()
);

create or replace function chat_session_checkpoints_guard_owner()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
    parent_owner uuid;
begin
    if tg_op = 'UPDATE'
       and (
           new.external_session_id is distinct from old.external_session_id
           or new.owner_subject_id is distinct from old.owner_subject_id
       ) then
        raise exception 'chat_session_checkpoint_owner_immutable';
    end if;
    select owner_subject_id into parent_owner
    from public.chat_sessions
    where external_session_id = new.external_session_id;
    if parent_owner is null or parent_owner <> new.owner_subject_id then
        raise exception 'chat_session_checkpoint_owner_mismatch';
    end if;
    return new;
end;
$$;

drop trigger if exists chat_session_checkpoints_guard_owner
    on chat_session_checkpoints;

create trigger chat_session_checkpoints_guard_owner
    before insert or update on chat_session_checkpoints
    for each row
    execute function chat_session_checkpoints_guard_owner();

comment on table chat_session_checkpoints is
    'Sealed FAE session state for authenticated conversation restoration; plaintext conversation content and browser secrets are forbidden.';

commit;

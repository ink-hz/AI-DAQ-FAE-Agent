begin;

-- Generic Platform subjects on the existing FAE session table.
-- FAE deploys replay every migration and keep no ledger, so this file must be
-- additive and re-runnable: the physical table and its indexes keep their v8
-- names so migration 008 can never recreate a second, empty session table.
-- deploy/scripts/migrate_pg.sh pipes each file to psql without
-- --single-transaction, so this file opens its own transaction: owner columns,
-- the compatibility trigger, the backfill and the shape constraint must become
-- visible together or not at all, on first deploy and on every replay.
alter table fae_enterprise_sessions
    add column if not exists owner_subject_id uuid,
    add column if not exists owner_subject_type text;

-- Rolling deploy and rollback compatibility: pre-v9 enterprise code inserts
-- without owner columns. Derive the enterprise owner for those rows only, so
-- partner rows still have to satisfy the shape constraint on their own. The
-- trigger is installed before the backfill and before the owner columns become
-- NOT NULL so that no ordering inside this transaction can reject a legacy
-- insert.
create or replace function fae_enterprise_sessions_derive_owner()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    if new.owner_subject_id is null and new.owner_subject_type is null then
        new.owner_subject_id := new.internal_user_id;
        new.owner_subject_type := 'enterprise_member';
    end if;
    return new;
end;
$$;

drop trigger if exists fae_enterprise_sessions_derive_owner
    on fae_enterprise_sessions;

create trigger fae_enterprise_sessions_derive_owner
    before insert or update on fae_enterprise_sessions
    for each row
    execute function fae_enterprise_sessions_derive_owner();

-- Backfill only unowned rows. Re-runs must never rewrite a partner row.
update fae_enterprise_sessions
set owner_subject_id = internal_user_id,
    owner_subject_type = 'enterprise_member'
where owner_subject_id is null
   or owner_subject_type is null;

alter table fae_enterprise_sessions
    alter column owner_subject_id set not null,
    alter column owner_subject_type set not null,
    alter column internal_user_id drop not null;

alter table fae_enterprise_sessions
    drop constraint if exists fae_enterprise_sessions_owner_shape;

alter table fae_enterprise_sessions
    add constraint fae_enterprise_sessions_owner_shape check (
        (owner_subject_type = 'enterprise_member'
            and internal_user_id is not null
            and owner_subject_id = internal_user_id)
        or
        (owner_subject_type = 'partner_operator'
            and internal_user_id is null)
    );

comment on table fae_enterprise_sessions is
    'FAE-owned browser sessions bound to generic Platform subjects; plaintext browser secrets and provider identifiers are forbidden.';

commit;

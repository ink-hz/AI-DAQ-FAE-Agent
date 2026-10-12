-- DAQ-only immutable role/release binding for Platform task outputs.
-- Insert before task queue insertion; an orphan binding is inert and safe.
begin;
do $guard$
begin
    if not exists (
        select 1 from daq_installation_identity
        where singleton and agent_id = 'ai-daq-fae-agent'
          and database_name = current_database()
    ) then
        raise exception 'daq_task_binding_identity_mismatch';
    end if;
end
$guard$;

create table if not exists daq_task_knowledge_bindings (
    platform_task_id uuid primary key,
    requester_internal_user_id uuid not null,
    knowledge_release text not null,
    knowledge_role text not null check (
        knowledge_role in ('internal_fae', 'tmall_support', 'channel')
    ),
    bound_at timestamptz not null default now()
);

create or replace function prevent_daq_task_binding_change()
returns trigger language plpgsql as $$
begin
    raise check_violation using message = 'DAQ task knowledge binding is immutable';
end;
$$;
drop trigger if exists daq_task_knowledge_binding_immutable
    on daq_task_knowledge_bindings;
create trigger daq_task_knowledge_binding_immutable
before update or delete on daq_task_knowledge_bindings
for each row execute function prevent_daq_task_binding_change();
commit;

-- Fail closed if a camera database is accidentally used for the DAQ task queue.
begin;
do $guard$
begin
    if exists (
        select 1 from fae_enterprise_sessions
        where agent_id <> 'ai-daq-fae-agent'
    ) then
        raise exception 'daq_installation_contains_foreign_identity_sessions';
    end if;
    if exists (select 1 from chat_sessions where external_session_id not like 'daq:%') then
        raise exception 'daq_installation_contains_foreign_sessions';
    end if;
    if to_regclass('public.daq_installation_identity') is null
       and exists (select 1 from platform_tasks) then
        raise exception 'daq_installation_contains_preexisting_tasks';
    end if;
end
$guard$;

create table if not exists daq_installation_identity (
    singleton boolean primary key default true check (singleton),
    agent_id text not null check (agent_id = 'ai-daq-fae-agent'),
    database_name text not null
);
insert into daq_installation_identity (singleton, agent_id, database_name)
values (true, 'ai-daq-fae-agent', current_database())
on conflict (singleton) do nothing;
do $guard$
begin
    if not exists (
        select 1 from daq_installation_identity
        where singleton and agent_id = 'ai-daq-fae-agent'
          and database_name = current_database()
    ) then
        raise exception 'daq_installation_identity_mismatch';
    end if;
end
$guard$;
commit;

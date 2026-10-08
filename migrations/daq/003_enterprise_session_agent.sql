-- The shared v8 schema carries the camera-only agent constraint. Replace it
-- only in the isolated DAQ database, after its installation marker is checked.
begin;
do $guard$
begin
    if not exists (
        select 1 from daq_installation_identity
        where singleton and agent_id = 'ai-daq-fae-agent'
          and database_name = current_database()
    ) or exists (
        select 1 from fae_enterprise_sessions
        where agent_id <> 'ai-daq-fae-agent'
    ) then
        raise exception 'daq_enterprise_session_agent_mismatch';
    end if;
end
$guard$;

alter table fae_enterprise_sessions
    drop constraint if exists fae_enterprise_sessions_agent_id_check;
alter table fae_enterprise_sessions
    drop constraint if exists daq_enterprise_sessions_agent_id_check;
alter table fae_enterprise_sessions
    add constraint daq_enterprise_sessions_agent_id_check
    check (agent_id = 'ai-daq-fae-agent');
commit;

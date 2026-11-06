"""Every provider request of one DAQ conversation carries the same metadata.user_id."""
from src.provider.session import current_provider_metadata, metadata_user_id_for


def test_task_orchestrator_scopes_provider_requests_to_the_session():
    from daq_fae.platform_tasks import DaqTaskOrchestrator

    seen = []
    orchestrator = DaqTaskOrchestrator.__new__(DaqTaskOrchestrator)
    orchestrator._handle_stream = lambda **kwargs: iter([seen.append(current_provider_metadata())])
    list(orchestrator.handle_stream(session_id="session-a", user_message="q"))
    assert seen == [{"user_id": metadata_user_id_for("session-a")}]
    assert current_provider_metadata() is None

"""Private archive CLI must bind to DAQ ownership and files."""
import pytest

from daq_fae.archive_cli import _service_from_daq_config


def test_daq_archive_service_uses_dedicated_database_agent_and_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_ATTACHMENT_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://daq_user@localhost/daq_database')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fae_user@localhost/fae_database')
    monkeypatch.setenv('DAQ_ATTACHMENT_STORAGE_DIR', str(tmp_path / 'daq-attachments'))
    service = _service_from_daq_config()
    assert service.enabled
    assert service._repository._agent_id == 'ai-daq-fae-agent'
    assert service._repository._database_url.endswith('/daq_database')
    assert service._store.root == tmp_path / 'daq-attachments'


def test_daq_archive_service_refuses_camera_database(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_ATTACHMENT_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://fae_user@localhost/fae_database')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fae_user@localhost/fae_database')
    monkeypatch.setenv('DAQ_ATTACHMENT_STORAGE_DIR', str(tmp_path / 'daq-attachments'))
    with pytest.raises(ValueError, match='daq_database_identity_invalid'):
        _service_from_daq_config()


def test_archive_worker_keeps_deletion_authority_when_new_handoffs_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_ATTACHMENT_ARCHIVE_ENABLED', 'false')
    monkeypatch.setenv('DAQ_DATABASE_URL', 'postgresql://daq_user@localhost/daq_database')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fae_user@localhost/fae_database')
    monkeypatch.setenv('DAQ_ATTACHMENT_STORAGE_DIR', str(tmp_path / 'daq-attachments'))
    service = _service_from_daq_config()
    assert service.enabled is False
    assert service.has_deletion_authority is True

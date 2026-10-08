"""Authenticated DAQ assembly must fail before exposing anonymous Dev routes."""

import pytest

from daq_fae.app import create_app


def test_enabled_platform_identity_requires_dedicated_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.delenv("DAQ_DATABASE_URL", raising=False)
    monkeypatch.delenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", raising=False)
    with pytest.raises(ValueError, match="daq_authenticated_persistence_configuration_missing"):
        create_app(provider_mode="offline", state_db_path=tmp_path / "dev.sqlite3")


from fastapi.testclient import TestClient
import pytest

from daq_fae.app import create_app
from daq_fae.offline_adapter import OfflineAdapter
from src.attachments.vision import AnthropicVisionAdapter


def test_dev_vision_inherits_daq_anthropic_gateway_with_auto_tool_choice(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_VISION_ENABLED', 'true')
    monkeypatch.setenv('DAQ_ANTHROPIC_AUTH_TOKEN', 'synthetic-token')
    monkeypatch.setenv('DAQ_ANTHROPIC_BASE_URL', 'https://gateway.example')
    monkeypatch.setenv('DAQ_ANTHROPIC_MODEL', 'claude-opus-5-5')
    app = create_app(
        provider_mode='anthropic', adapter=OfflineAdapter(),
        knowledge_release_root=tmp_path / 'knowledge', attachment_dir=tmp_path / 'attachments',
    )

    vision = app.state.vision_adapter
    assert isinstance(vision, AnthropicVisionAdapter)
    assert vision.model == 'claude-opus-5-5'
    assert vision.api_url == 'https://gateway.example/v1/messages'
    assert vision.tool_choice_strategy == 'submit_only_auto'
    client = TestClient(app)
    assert client.get('/attachments/capabilities').json() == {
        'vision_enabled': True, 'vision_reason': 'ready',
    }


def test_vision_enabled_requires_daq_gateway_not_implicit_public_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_VISION_ENABLED', 'true')
    monkeypatch.setenv('DAQ_ANTHROPIC_AUTH_TOKEN', 'synthetic-token')
    monkeypatch.delenv('DAQ_ANTHROPIC_BASE_URL', raising=False)
    with pytest.raises(ValueError, match='daq_vision_gateway_missing'):
        create_app(
            provider_mode='anthropic', adapter=OfflineAdapter(),
            knowledge_release_root=tmp_path / 'knowledge', attachment_dir=tmp_path / 'attachments',
        )

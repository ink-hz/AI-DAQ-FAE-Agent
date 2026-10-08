from fastapi.testclient import TestClient

from daq_fae.app import create_app


def test_built_daq_ui_mounts_local_workspace_without_camera_workspace(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><head></head><body>AI DAQ FAE Agent</body></html>', encoding="utf-8",
    )
    (tmp_path / "assets").mkdir()
    client = TestClient(create_app(provider_mode="offline", webui_dist=tmp_path))
    response = client.get("/app/")
    assert response.status_code == 200
    assert "AI DAQ FAE Agent" in response.text
    assert 'content="/app"' in response.text
    assert 'content=""' in response.text
    assert client.get("/app/conversations/test-session").status_code == 200
    assert client.get("/app/assets/missing.js").status_code == 404
    assert client.get("/fae/").status_code == 404

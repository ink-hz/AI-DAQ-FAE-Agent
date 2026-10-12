import base64
import json
import subprocess
from pathlib import Path


def test_production_env_reuses_camera_database_and_gateway_but_separates_daq_keys(tmp_path):
    camera = tmp_path / 'camera.env'
    camera.write_text('\n'.join([
        'POSTGRES_DB=ai_fae', 'POSTGRES_USER=ai_fae', 'POSTGRES_PASSWORD=test%password',
        'ANTHROPIC_AUTH_TOKEN=synthetic-secret', 'ANTHROPIC_BASE_URL=https://gateway.example',
        'ANTHROPIC_MODEL_MAIN=claude-opus-5-5', 'NO_PROXY=localhost,127.0.0.1',
    ]) + '\n')
    output = tmp_path / 'daq'
    subject = 'b78b3205-2c86-424a-a217-775382c208bd'
    result = subprocess.run([
        str(Path(__file__).resolve().parents[1] / '.venv/bin/python'),
        'deploy/provision_env.py', '--camera-env', str(camera),
        '--output-root', str(output), '--subject-id', subject,
        '--runtime-release', 'daq-release-1',
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    from dotenv import dotenv_values
    env = dotenv_values(output / 'env.production')
    assert env['DAQ_DATABASE_URL'] == (
        'postgresql://ai_fae:test%25password@ai-fae-postgres:5432/ai_fae'
        '?options=-csearch_path%3Ddaq_fae')
    assert env['DAQ_ANTHROPIC_AUTH_TOKEN'] == 'synthetic-secret'
    assert env['DAQ_ANTHROPIC_BASE_URL'] == 'https://gateway.example'
    assert env['DAQ_RUNTIME_RELEASE'] == 'daq-release-1'
    assert env['DAQ_PLATFORM_ALLOWED_SUBJECT_IDS'] == subject
    assert (output / 'env.production').stat().st_mode & 0o077 == 0
    session = json.loads((output / 'secrets/session-keyring.json').read_text())
    content = json.loads((output / 'secrets/content-keyring.json').read_text())
    assert base64.b64decode(session['keys']['1']) != base64.b64decode(content['keys']['1'])
    assert json.loads((output / 'secrets/entitlements.json').read_text())['subjects'] == {
        subject: 'internal_fae'}
    assert 'synthetic-secret' not in result.stdout

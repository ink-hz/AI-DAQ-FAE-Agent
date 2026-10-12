#!/usr/bin/env python3
"""Provision DAQ-only credentials while reusing the camera database and gateway."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
from urllib.parse import quote
from uuid import UUID

from dotenv import dotenv_values


def _private_write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(content)


def _keyring() -> str:
    return json.dumps({
        'active_version': 1,
        'keys': {'1': base64.b64encode(secrets.token_bytes(32)).decode('ascii')},
    }, separators=(',', ':')) + '\n'


def _env_value(value: str) -> str:
    if any(char in value for char in '\r\n'):
        raise ValueError('multiline environment value')
    # Compose env_file accepts single-quoted literal values, including dollar
    # signs and hash characters in provider credentials.
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def provision(camera_env: Path, output_root: Path, subject_ids: list[str], runtime_release: str) -> None:
    camera = dotenv_values(camera_env, interpolate=False)
    required = ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')
    if any(not camera.get(key) for key in required):
        raise ValueError('camera database configuration missing')
    token = camera.get('ANTHROPIC_AUTH_TOKEN') or ''
    api_key = camera.get('ANTHROPIC_API_KEY') or ''
    if not (token or api_key) or not camera.get('ANTHROPIC_BASE_URL'):
        raise ValueError('camera gateway configuration missing')
    if not runtime_release or runtime_release.endswith('-dev'):
        raise ValueError('production runtime release missing')
    subjects = [str(UUID(item)) for item in subject_ids]
    if not subjects or len(set(subjects)) != len(subjects):
        raise ValueError('pilot subject list invalid')
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True, mode=0o700)
    secret_dir = output_root / 'secrets'
    secret_dir.mkdir(mode=0o700)
    _private_write(secret_dir / 'session-keyring.json', _keyring())
    _private_write(secret_dir / 'content-keyring.json', _keyring())
    _private_write(secret_dir / 'entitlements.json', json.dumps({
        'agent_id': 'ai-daq-fae-agent',
        'subjects': {subject: 'internal_fae' for subject in subjects},
    }, separators=(',', ':')) + '\n')
    user = quote(str(camera['POSTGRES_USER']), safe='')
    password = quote(str(camera['POSTGRES_PASSWORD']), safe='')
    db = quote(str(camera['POSTGRES_DB']), safe='')
    database_url = (f'postgresql://{user}:{password}@ai-fae-postgres:5432/{db}'
                    '?options=-csearch_path%3Ddaq_fae')
    values = {
        'DAQ_ENVIRONMENT': 'production',
        'DAQ_RUNTIME_RELEASE': runtime_release,
        'DAQ_PROVIDER_MODE': 'anthropic',
        'DAQ_ANTHROPIC_AUTH_TOKEN': token,
        'DAQ_ANTHROPIC_API_KEY': api_key,
        'DAQ_ANTHROPIC_BASE_URL': str(camera['ANTHROPIC_BASE_URL']),
        'DAQ_ANTHROPIC_MODEL': 'claude-opus-5-5',
        'DAQ_VISION_ENABLED': 'true',
        'DAQ_DATABASE_URL': database_url,
        'DAQ_PLATFORM_IDENTITY_ENABLED': 'true',
        'DAQ_PLATFORM_IDENTITY_BASE_URL': 'http://172.31.0.3:8080',
        'DAQ_PLATFORM_PUBLIC_ORIGIN': 'https://agent.orbbec.com.cn',
        'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS': ','.join(subjects),
        'DAQ_PLATFORM_SESSION_KEYRING_FILE': '/run/daq-secrets/session-keyring.json',
        'DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE': '/run/daq-secrets/content-keyring.json',
        'DAQ_KNOWLEDGE_ENTITLEMENTS_FILE': '/run/daq-secrets/entitlements.json',
        'DAQ_KNOWLEDGE_RELEASE_ROOT': '/app/data/knowledge/published',
        'DAQ_TRACE_LOG_PATH': '/app/data/daq_traces.jsonl',
        'DAQ_ATTACHMENT_STORAGE_DIR': '/app/data/attachments',
        'DAQ_PLATFORM_TASK_ENABLED': 'false',
        'DAQ_ATTACHMENT_ARCHIVE_ENABLED': 'false',
    }
    content = ''.join(f'{key}={_env_value(value)}\n' for key, value in values.items())
    _private_write(output_root / 'env.production', content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-env', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--subject-id', action='append', required=True)
    parser.add_argument('--runtime-release', required=True)
    args = parser.parse_args()
    provision(args.camera_env, args.output_root, args.subject_id, args.runtime_release)
    print('DAQ production environment and independent keyrings provisioned')


if __name__ == '__main__':
    main()

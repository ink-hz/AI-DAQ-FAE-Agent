"""Private, authenticated DAQ Dev replay with the active immutable release.

Starts an isolated Unix-socket Postgres and a fake enterprise identity binding.
Every answer still traverses the real /chat endpoint, DAQ schema, Loop, evidence
policy, transaction, SSE and history projection. Never points at production.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Barrier
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
NEW_SUITE = ROOT / 'evals/daq_workflow_20261012.jsonl'
OLD_SUITES = (
    ROOT / 'data/knowledge/curated/operator-dev-20261012/final-v3/suite.json',
    ROOT / 'data/knowledge/curated/operator-dev-20261012/claim-v2/suite.json',
)
RELEASE_ROOT = ROOT / 'data/knowledge/published'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cases(suite: str) -> list[dict]:
    rows = []
    if suite in {'new', 'all'}:
        rows.extend(json.loads(line) for line in NEW_SUITE.read_text().splitlines() if line)
    if suite in {'old24', 'all'}:
        for path in OLD_SUITES:
            rows.extend({**case, 'id': 'OLD_' + case['id']}
                        for case in json.loads(path.read_text())['cases'])
    return rows


def _events(response) -> list[dict]:
    events = []
    for block in response.text.split('\n\n'):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith('event: ')), None)
        payload = next((json.loads(line[6:]) for line in lines if line.startswith('data: ')), None)
        if name is not None:
            events.append({'type': name, 'data': payload})
    return events


def _start_postgres(directory: Path) -> str:
    data, socket = directory / 'pgdata', directory / 'socket'
    socket.mkdir()
    subprocess.run(['initdb', '-D', str(data), '-U', 'daq_dev_admin',
                    '--auth-local=trust', '--auth-host=reject', '--no-locale',
                    '--encoding=UTF8'], check=True, capture_output=True)
    subprocess.run(['pg_ctl', '-D', str(data), '-l', str(directory / 'postgres.log'),
                    '-o', f"-F -k {socket} -h ''", '-w', 'start'],
                   check=True, capture_output=True)
    admin = f'host={socket} dbname=postgres user=daq_dev_admin'
    import psycopg
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute('create role daq_dev_user login')
        connection.execute('create database daq_dev owner daq_dev_user')
    dsn = f'host={socket} dbname=daq_dev user=daq_dev_user'
    with psycopg.connect(dsn, autocommit=True) as connection:
        for path in sorted((ROOT / 'migrations/shared').glob('0*.sql')):
            connection.execute(path.read_text())
        connection.execute((ROOT / 'migrations/daq/001_durable_request_context.sql').read_text())
    return dsn


def _stop_postgres(directory: Path) -> None:
    data = directory / 'pgdata'
    if data.exists():
        subprocess.run(['pg_ctl', '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                       check=False, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=('new', 'old24', 'all'), default='new')
    parser.add_argument('--case-ids', default='')
    parser.add_argument('--response-language', choices=('zh-CN', 'en-US'),
                        help='Explicit browser language selection for every replayed turn')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--concurrency-probe', action='store_true',
                        help='Run exactly two independent one-turn cases concurrently')
    args = parser.parse_args()
    selected = set(filter(None, args.case_ids.split(',')))
    cases = [case for case in _cases(args.suite) if not selected or case['id'] in selected]
    if selected - {case['id'] for case in cases}:
        raise SystemExit('unknown case id: ' + ','.join(sorted(selected - {case['id'] for case in cases})))
    if not cases:
        raise SystemExit('no selected cases')
    if args.concurrency_probe and (len(cases) != 2 or any(
            case.get('turns') or case.get('fixture') or case.get('operation')
            for case in cases)):
        raise SystemExit('concurrency probe needs exactly two independent one-turn cases')
    output = args.output_dir or ROOT / 'data/knowledge/curated/workflow-dev-20261012' / time.strftime('%Y%m%d-%H%M%S')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output.chmod(0o700)

    # Importing app.py constructs an app at module load. Keep that bootstrap on
    # the empty test root; the explicit Dev app below binds the active release.
    with tempfile.TemporaryDirectory(prefix='daq-workflow-dev-', dir='/tmp') as temp:
        workspace = Path(temp)
        isolated_release = workspace / 'empty-release'
        isolated_release.mkdir()
        os.environ['DAQ_KNOWLEDGE_RELEASE_ROOT'] = str(isolated_release)
        from dotenv import dotenv_values
        from daq_fae.knowledge.releases import read_active_release
        active = read_active_release(RELEASE_ROOT)
        if active is None:
            raise SystemExit('active DAQ release missing')
        runtime_release = active['manifest']['review']['operator_release']['runtime']['release']
        os.environ['DAQ_RUNTIME_RELEASE'] = runtime_release
        settings = dotenv_values(ROOT.parent / 'AI-FAE-Agent/.env')
        for target, source in (
            ('DAQ_ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_AUTH_TOKEN'),
            ('DAQ_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'),
            ('DAQ_ANTHROPIC_BASE_URL', 'ANTHROPIC_BASE_URL'),
            ('DAQ_ANTHROPIC_MODEL', 'ANTHROPIC_MODEL_MAIN'),
        ):
            os.environ[target] = settings.get(source) or ''
        if not (os.environ['DAQ_ANTHROPIC_AUTH_TOKEN'] or os.environ['DAQ_ANTHROPIC_API_KEY']):
            raise SystemExit('Dev model credential unavailable')
        from daq_fae.app import _anthropic_dev_adapter, _daq_vision_adapter, create_app
        from daq_fae.durable_state import DaqDurableState
        from daq_fae.authenticated_persistence import _FailClosedFeedbackWriter
        from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
        from src.storage.authenticated_conversations import AuthenticatedConversationRepository, ConversationContentCodec
        from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
        from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment
        from tests.test_authenticated_persistence import environment as persistence_environment
        from fastapi.testclient import TestClient

        dsn = _start_postgres(workspace)
        try:
            env = {**identity_environment(workspace), **persistence_environment(workspace)}
            for key, value in env.items():
                os.environ[key] = value
            os.environ['DAQ_ENVIRONMENT'] = 'development'
            os.environ['DAQ_TRACE_LOG_PATH'] = str(output / 'traces.jsonl')
            role_path = workspace / 'entitlements.json'
            role_path.write_text(json.dumps({'agent_id': 'ai-daq-fae-agent', 'subjects': {
                str(FakePlatform().subject.subject_id): 'internal_fae',
            }}))
            role_path.chmod(0o600)
            os.environ['DAQ_KNOWLEDGE_ENTITLEMENTS_FILE'] = str(role_path)
            codec = ConversationContentCodec.from_file(env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE'])
            image_cases = any(case.get('fixture') == 'current_turn_image' for case in cases)
            app = create_app(
                provider_mode='anthropic', adapter=_anthropic_dev_adapter(),
                vision_adapter=_daq_vision_adapter() if image_cases else None,
                attachment_dir=workspace / 'attachments',
                knowledge_release_root=RELEASE_ROOT,
                platform_client=FakePlatform(),
                identity_repository=InMemoryAuthenticatedSessionRepository(),
                conversation_repository=AuthenticatedConversationRepository(dsn, codec=codec),
                feedback_store=PostgresDataFlywheelStore(dsn, _FailClosedFeedbackWriter()),
                review_store=object(),
                durable_state=DaqDurableState(dsn, codec=codec),
            )
            identity = {
                'suite': args.suite, 'new_suite_sha256': _sha(NEW_SUITE),
                'old_suite_sha256': [_sha(path) for path in OLD_SUITES],
                'knowledge_release': active['release_id'],
                'runtime_release': runtime_release,
                'upstream_revision': json.loads((ROOT / 'upstream-source.json').read_text())['revision'],
                'prompt_sha256': _sha(ROOT / 'prompts/reviewed_knowledge_system.md'),
                'schema_prompt_sha256': _sha(ROOT / 'prompts/daq_schema_extraction.md'),
                'model': app.state.chat_adapter.model if hasattr(app.state, 'chat_adapter') else os.environ['DAQ_ANTHROPIC_MODEL'],
                'concurrency': 2 if args.concurrency_probe else 1,
                'selected_response_language': args.response_language,
            }
            (output / 'identity.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2))
            (output / 'identity.json').chmod(0o600)
            result_path = output / 'results.jsonl'
            previous_session = None
            with TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN']) as client, \
                    result_path.open('w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), 0o600)
                launch = client.post('/enterprise/session', json={'code': CODE},
                                     headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
                if launch.status_code != 201:
                    raise RuntimeError(f'Dev identity launch failed: {launch.status_code}')
                headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
                           'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
                if args.concurrency_probe:
                    barrier = Barrier(2)

                    def concurrent_turn(case):
                        body = {'message': case['question'],
                                'client_request_id': f"{case['id']}-probe-{uuid4()}"}
                        if args.response_language:
                            body['response_language'] = args.response_language
                        barrier.wait(timeout=15)
                        started = time.monotonic()
                        response = client.post('/chat', json=body, headers=headers)
                        ended = time.monotonic()
                        events = _events(response)
                        done = next((event['data'] for event in events
                                     if event['type'] == 'done'), None)
                        if done is None:
                            raise RuntimeError(f"{case['id']} missing concurrent done")
                        detail = client.get(
                            f"/authenticated/conversations/{done['session_id']}").json()
                        saved = detail.get('turns', [])[-1]
                        stages = [event['data'] for event in events
                                  if event['type'] == 'stage']
                        running = {event['stage_id'] for event in stages
                                   if event.get('status') == 'running'}
                        closed = {event['stage_id'] for event in stages
                                  if event.get('status') in {'completed', 'error'}}
                        integrity = {
                            'stage_lifecycle_closed': running <= closed,
                            'stream_history_stage_equal': stages == saved.get('stages'),
                            'trace_id_equal': done.get('trace_id') == saved.get('trace_id'),
                            'outcome_equal': done.get('outcome') == saved.get('outcome'),
                            'source_ids_equal': {
                                item.get('source_id') for item in done.get('sources', [])
                            } == {item.get('source_id') for item in saved.get('sources', [])},
                        }
                        turn = {'question': case['question'],
                                'http_status': response.status_code,
                                'events': events, 'done': done,
                                'history_turns': detail.get('turns', []),
                                'integrity': integrity,
                                'duration_ms': int((ended - started) * 1000),
                                'started_monotonic': started,
                                'ended_monotonic': ended}
                        return {'case': case, 'identity': identity, 'turns': [turn]}

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        concurrent_rows = list(pool.map(concurrent_turn, cases))
                    overlap = (max(row['turns'][0]['started_monotonic']
                                   for row in concurrent_rows) <
                               min(row['turns'][0]['ended_monotonic']
                                   for row in concurrent_rows))
                    for row in concurrent_rows:
                        row['concurrent_overlap'] = overlap
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                        print(row['case']['id'], row['turns'][0]['done']['outcome'],
                              row['turns'][0]['duration_ms'], 'overlap', overlap,
                              flush=True)
                    stream.flush()
                    print('output', output, 'sha256', _sha(result_path), flush=True)
                    return 0
                for case in cases:
                    if case.get('operation'):
                        from daq_fae.domain_tools import DaqToolBox
                        tool = DaqToolBox(knowledge=app.state.daq_knowledge,
                                          role='internal_fae').dispatch(
                                              case['operation'], case.get('arguments', {}))
                        row = {'case': case, 'identity': identity,
                               'operation': {'status': tool.status,
                                             'content': tool.content,
                                             'sources': tool.sources}}
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                        stream.flush()
                        print(case['id'], tool.status, flush=True)
                        continue
                    if case.get('fixture') and case['fixture'] != 'current_turn_image':
                        row = {'case': case, 'status': 'deterministic_fixture_not_model_replay',
                               'identity': identity}
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                        stream.flush()
                        print(case['id'], row['status'], flush=True)
                        continue
                    questions = case.get('turns') or [case['question']]
                    if case['id'] == 'OLD_multiturn_followup' and previous_session:
                        session_id = previous_session
                    else:
                        session_id = None
                    outputs = []
                    for index, question in enumerate(questions):
                        started = time.monotonic()
                        body = {'message': question, 'client_request_id': f"{case['id']}-{index}-{uuid4()}"}
                        if args.response_language:
                            body['response_language'] = args.response_language
                        if case.get('fixture') == 'current_turn_image':
                            from PIL import Image, ImageDraw
                            picture = Image.new('RGB', (320, 180), 'white')
                            drawing = ImageDraw.Draw(picture)
                            drawing.ellipse((40, 30, 140, 130), fill='blue')
                            drawing.text((165, 80), 'DAQ TEST', fill='black')
                            payload = BytesIO()
                            picture.save(payload, format='PNG')
                            uploaded = client.post('/attachments', files=[
                                ('files', ('visual-fixture.png', payload.getvalue(), 'image/png'))],
                                headers=headers)
                            if uploaded.status_code != 201:
                                raise RuntimeError(f'image upload failed: {uploaded.status_code}')
                            body['attachment_ids'] = [
                                uploaded.json()['results'][0]['attachment']['attachment_id']]
                        if session_id:
                            body['session_id'] = session_id
                        response = client.post('/chat', json=body, headers=headers)
                        events = _events(response)
                        done = next((event['data'] for event in events if event['type'] == 'done'), None)
                        duration_ms = int((time.monotonic() - started) * 1000)
                        if done is None:
                            raise RuntimeError(f"{case['id']} missing done; status={response.status_code}")
                        session_id = done['session_id']
                        detail = client.get(f'/authenticated/conversations/{session_id}').json()
                        turn_history = detail.get('turns', [])
                        saved = turn_history[-1] if turn_history else {}
                        emitted_stages = [event['data'] for event in events
                                          if event['type'] == 'stage']
                        running = {event['stage_id'] for event in emitted_stages
                                   if event.get('status') == 'running'}
                        closed = {event['stage_id'] for event in emitted_stages
                                  if event.get('status') in {'completed', 'error'}}
                        integrity = {
                            'stage_lifecycle_closed': running <= closed,
                            'stream_history_stage_equal': emitted_stages == saved.get('stages'),
                            'trace_id_equal': done.get('trace_id') == saved.get('trace_id'),
                            'outcome_equal': done.get('outcome') == saved.get('outcome'),
                            'source_ids_equal': {
                                item.get('source_id') for item in done.get('sources', [])
                            } == {item.get('source_id') for item in saved.get('sources', [])},
                        }
                        outputs.append({'question': question, 'http_status': response.status_code,
                                        'events': events, 'done': done,
                                        'history_turns': turn_history, 'integrity': integrity,
                                        'duration_ms': duration_ms})
                    if case['id'] == 'OLD_multiturn_setup':
                        previous_session = session_id
                    row = {'case': case, 'identity': identity, 'turns': outputs}
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                    stream.flush()
                    last = outputs[-1]
                    print(case['id'], last['done']['outcome'], last['duration_ms'],
                          len(last['done']['answer']), last['done']['trace_id'], flush=True)
            print('output', output, 'sha256', _sha(result_path), flush=True)
        finally:
            _stop_postgres(workspace)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

import pytest
import json
from pathlib import Path

from daq_fae.request_schema import (DaqRequestSchema, SchemaExtractionError,
                                    extract_request_schema, refers_to_prior_attachment)
from daq_fae.knowledge.sdk_catalog import SdkCatalog
from daq_fae.domain_tools import DaqToolBox


def test_dated_sdk_sample_does_not_silently_skip_feature_change_history():
    archive = Path('data/knowledge/local_archive/sdk-originals-1015-local-20261015')
    if not archive.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(Path('Knowledge_SDK'), archive)
    question = ('2026-09-29 SDK 包里还带有 magnetometer.cpp，'
                '就能确定 UMI Finger 磁力计仍可用吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk_release_date': '2026-09-29',
                            'source_files': ['magnetometer.cpp']}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['UMI Finger'],
             'conditions': {'feature': 'magnetometer'}},
        ],
    }, sdk_catalog=catalog)
    history = [goal for goal in schema.goals
               if goal.capability == 'search_knowledge']
    assert len(history) == 1
    assert history[0].entities == ('UMI Finger',)
    assert history[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'feature': 'magnetometer'}
    assert history[0].critical is True


def test_dated_sdk_sample_followup_keeps_prior_source_topic_for_history_goal():
    archive = Path('data/knowledge/local_archive/sdk-originals-1015-local-20261015')
    if not archive.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(Path('Knowledge_SDK'), archive)
    question = '那能据这个示例说 UMI Finger 磁力计仍然可用吗？'
    prior = ('OrbbecSDK 2.9.0 的 2026-09-29 构建改动了什么？\n'
             '同一构建的包里还有 magnetometer.cpp 示例吗？')
    candidate = {'confidence': 'high', 'goals': [
        {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
         'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                        'source_files': ['magnetometer.cpp']}},
        {'kind': 'product', 'capability': 'check_software_support',
         'entities': ['UMI Finger'],
         'conditions': {'feature': 'magnetometer', 'sdk': 'OrbbecSDK',
                        'sdk_version': '2.9.0'}},
    ]}
    schema = DaqRequestSchema.from_candidate(
        question, candidate, prior_user_context=prior, sdk_catalog=catalog)
    history = [goal for goal in schema.goals
               if goal.capability == 'search_knowledge']
    assert len(history) == 1
    assert history[0].entities == ('UMI Finger',)
    assert history[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'feature': 'magnetometer',
        'sdk_version': '2.9.0'}

    unrelated = DaqRequestSchema.from_candidate(
        '换个话题：UMI Finger 磁力计仍然可用吗？', candidate,
        prior_user_context=prior, sdk_catalog=catalog)
    assert not any(goal.capability == 'search_knowledge'
                   for goal in unrelated.goals)


def test_release_note_document_type_is_redundant_to_search_goal():
    question = '四目 EGO Pro 固件 v1.0.7 的变更日志写了什么？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'search_knowledge',
            'entities': ['四目 EGO Pro'],
            'conditions': {'firmware_version': 'v1.0.7',
                           'document_type': 'changelog'},
        }],
    })
    assert schema.goals[0].condition_map() == {'firmware_version': 'v1.0.7'}
    assert schema.goals[0].unmapped_condition_keys == ()
    wrong = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'search_knowledge',
            'entities': ['四目 EGO Pro'],
            'conditions': {'firmware_version': 'v1.0.7',
                           'document_type': 'datasheet'},
        }],
    })
    assert wrong.goals[0].unmapped_condition_keys == ('document_type',)


def _reviewed_sdk_row(kb_id: str) -> SdkCatalog:
    row = next(json.loads(line) for line in Path('Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()
               if json.loads(line)['kb_id'] == kb_id)
    return SdkCatalog([row], {'records_sha256': 'fixture-records'})


def test_exact_package_example_presence_keeps_file_evidence_when_model_invents_inspect_task():
    catalog = _reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023')
    question = '同一构建的包里还有 magnetometer.cpp 示例吗？'
    prior = 'OrbbecSDK 2.9.0 的 2026-09-29 构建改动了什么？'
    base = {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
            'sdk_release_date': '2026-09-29',
            'source_files': ['magnetometer.cpp'], 'task': 'inspect_package'}

    def goal(asked, conditions):
        return DaqRequestSchema.from_candidate(asked, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': conditions,
            }],
        }, prior_user_context=prior, sdk_catalog=catalog).goals[0]

    presence = goal(question, base)
    assert presence.condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
        'sdk_release_date': '2026-09-29',
        'source_files': ['magnetometer.cpp']}
    assert presence.unmapped_condition_keys == ()

    # File presence does not prove execution, and a different build or file
    # cannot borrow this reviewed example row.
    for asked, conditions in (
        ('同一构建的包里 magnetometer.cpp 示例能运行吗？', base),
        (question, {**base, 'sdk_release_date': '2026-10-01'}),
        ('同一构建的包里还有 missing.cpp 示例吗？',
         {**base, 'source_files': ['missing.cpp']}),
    ):
        scoped = goal(asked, conditions)
        assert scoped.condition_map().get('task') == 'inspect_package'
        assert 'task' in scoped.unmapped_condition_keys

    for asked, prior_turn, files, build in (
        ('同一构建的包里还有 missing.cpp 示例吗？',
         'OrbbecSDK 2.9.0 的 2026-09-29 构建有 magnetometer.cpp 吗？',
         ['missing.cpp'], '2026-09-29'),
        ('OrbbecSDK 2.9.0 的 2026-09-29 构建包里还有 magnetometer.cpp 和 missing.cpp 两个示例吗？',
         '', ['magnetometer.cpp'], '2026-09-29'),
        ('OrbbecSDK 2.9.0 的 2026-09-23 构建包里有 magnetometer.cpp 示例吗？',
         'OrbbecSDK 2.9.0 的 2026-09-29 构建改动了什么？',
         ['magnetometer.cpp'], '2026-09-23'),
    ):
        scoped = DaqRequestSchema.from_candidate(asked, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': [], 'conditions': base,
            }],
        }, prior_user_context=prior_turn, sdk_catalog=catalog).goals[0]
        assert scoped.condition_map()['source_files'] == files
        assert scoped.condition_map()['sdk_release_date'] == build
        if files != ['magnetometer.cpp'] or build != '2026-09-29':
            assert scoped.condition_map().get('task') == 'inspect_package'
            assert 'task' in scoped.unmapped_condition_keys
        from daq_fae.domain_tools import DaqToolBox
        catalog.manifest['records_sha256'] = '0' * 64

        class Knowledge:
            release_id = 'dev-sdk'
            sdk_catalog = catalog

            def records_for(self, role):
                return []

            def sections_for(self, role):
                return []

        result = DaqToolBox(
            question=asked, knowledge=Knowledge(), role='internal_fae',
            requirements=[{
                'id': 'usage', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': scoped.condition_map(),
            }]).dispatch('sdk_evidence', {'query': asked})
        if files != ['magnetometer.cpp'] or build != '2026-09-29':
            assert result.status == 'not_found'
            assert result.content['matched_requirement_ids'] == []


def test_two_named_source_files_keep_separate_reviewed_sdk_goals():
    catalog = SdkCatalog([
        _reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023').records[0],
        _reviewed_sdk_row('daq-sdk-host-state-006').records[0],
    ], {'records_sha256': 'fixture-records'})
    question = ('OrbbecSDK 2.9.0 的 2026-09-29 构建包里，magnetometer.cpp '
                '和 ego_state.cpp 这两个示例分别存在吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'sdk_release_date': '2026-09-29',
                            'source_files': ['magnetometer.cpp'],
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'sdk_release_date': '2026-09-29',
                            'source_files': ['ego_state.cpp'],
                            'task': 'inspect_package'}},
        ],
    }, sdk_catalog=catalog)
    assert {tuple(goal.condition_map()['source_files']) for goal in schema.goals} == {
        ('magnetometer.cpp',), ('ego_state.cpp',)}
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)
    combined = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'sdk_release_date': '2026-09-29',
                           'source_files': ['magnetometer.cpp', 'ego_state.cpp'],
                           'task': 'inspect_package'},
        }],
    }, sdk_catalog=catalog)
    assert {tuple(goal.condition_map()['source_files']) for goal in combined.goals} == {
        ('magnetometer.cpp',), ('ego_state.cpp',)}
    assert all(not goal.unmapped_condition_keys for goal in combined.goals)
    from daq_fae.domain_tools import DaqToolBox
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirements = [
        {'id': goal.id, 'capability': 'sdk_evidence', 'entities': [],
         'conditions': goal.condition_map()}
        for goal in combined.goals]
    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert {row['kb_id'] for row in result.content['matches']} == {
        'daq-sdk-host-magnetometer-sample-023', 'daq-sdk-host-state-006'}
    assert set(result.content['matched_requirement_ids']) == {
        goal.id for goal in combined.goals}


def test_package_file_and_build_obligations_survive_partial_model_schema():
    catalog = _reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023')
    base = {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
            'sdk_release_date': '2026-09-29',
            'source_files': ['magnetometer.cpp'], 'task': 'inspect_package'}
    for question, expected in (
        ('OrbbecSDK 2.9.0 的 2026-09-29 构建包里，magnetometer.cpp 和 '
         'missing.cpp 这两个示例分别存在吗？',
         {'source_files': {('magnetometer.cpp',), ('missing.cpp',)}}),
        ('OrbbecSDK 2.9.0 的 2026-09-23 和 2026-09-29 两个构建包里都存在 '
         'magnetometer.cpp 示例吗？',
         {'sdk_release_date': {'2026-09-23', '2026-09-29'}}),
        ('OrbbecSDK 2.8.0 和 OrbbecSDK 2.9.0 的 2026-09-29 包里都存在 '
         'magnetometer.cpp 示例吗？',
         {'sdk_version': {'2.8.0', '2.9.0'}}),
    ):
        schema = DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': [], 'conditions': base,
            }],
        }, sdk_catalog=catalog)
        assert len(schema.goals) == 2
        for key, values in expected.items():
            actual = {tuple(goal.condition_map()[key])
                      if key == 'source_files' else goal.condition_map()[key]
                      for goal in schema.goals}
            assert actual == values

    version_question = ('OrbbecSDK 2.8.0 的 2026-09-29 构建包里有 '
                        'magnetometer.cpp 示例吗？')
    version_goal = DaqRequestSchema.from_candidate(version_question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': base,
        }],
    }, prior_user_context='OrbbecSDK 2.9.0 的示例呢？',
        sdk_catalog=catalog).goals[0]
    assert version_goal.condition_map()['sdk_version'] == '2.8.0'
    assert version_goal.condition_map()['task'] == 'inspect_package'
    assert 'task' in version_goal.unmapped_condition_keys
    underscored = version_question.replace('OrbbecSDK 2.8.0',
                                             'OrbbecSDK_v2.8.0')
    underscored_goal = DaqRequestSchema.from_candidate(underscored, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': base,
        }],
    }, prior_user_context='OrbbecSDK_v2.9.0 的示例呢？',
        sdk_catalog=catalog).goals[0]
    assert underscored_goal.condition_map()['sdk_version'] == '2.8.0'
    assert underscored_goal.condition_map()['task'] == 'inspect_package'


def test_libobkit_readme_image_binding_uses_reviewed_scope_and_keeps_device_support_separate():
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    first = 'libobkit.so 是 RK3576 设备端 SDK 吗？包内 README 对整机镜像怎么说？'
    schema = DaqRequestSchema.from_candidate(first, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit', 'platform': 'RK3576',
                           'task': 'inspect_package'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map()['task'] == 'sdk_scope'
    assert goal.unmapped_condition_keys == ()

    followup = '所以可以把 libobkit.so 拷到任意 EGO Pro 固件上用吗？'
    schema = DaqRequestSchema.from_candidate(followup, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO Pro'],
             'conditions': {'sdk': 'libobkit', 'platform': 'rk3576'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'libobkit', 'platform': 'rk3576',
                            'task': 'firmware_image_requirement'}},
        ],
    }, prior_user_context=first, sdk_catalog=catalog)
    usage = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    assert usage.condition_map()['task'] == 'sdk_scope'
    assert usage.unmapped_condition_keys == ()
    assert any(goal.capability == 'check_software_support' for goal in schema.goals)
    from daq_fae.domain_tools import DaqToolBox
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=followup, knowledge=Knowledge(), role='internal_fae',
        requirements=[{'id': 'usage', 'capability': 'sdk_evidence',
                       'entities': [], 'conditions': usage.condition_map()}],
    ).dispatch('sdk_evidence', {'query': followup})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']
    effective = (followup + '\n此前用户原话（仅作上下文线索）：'
                 'README 对固件镜像和摄像头成像分别有什么要求？')
    multistage = DaqToolBox(
        current_question=followup, knowledge=Knowledge(), role='internal_fae',
        requirements=[{'id': 'usage', 'capability': 'sdk_evidence',
                       'entities': [], 'conditions': usage.condition_map()}],
    ).with_request_context(effective).dispatch('sdk_evidence', {'query': followup})
    assert multistage.status == 'ok'
    assert multistage.content['matched_requirement_ids'] == ['usage']

    omitted_support = DaqRequestSchema.from_candidate(followup, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit',
                           'task': 'firmware_image_requirement'},
        }],
    }, prior_user_context=first, sdk_catalog=catalog)
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('EGO Pro',)
               for goal in omitted_support.goals)
    other_firmware = 'libobkit.so 能在 EGO Pro 的其他固件上用吗？'
    other_schema = DaqRequestSchema.from_candidate(other_firmware, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit',
                           'task': 'firmware_image_requirement'},
        }],
    }, sdk_catalog=catalog)
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('EGO Pro',)
               for goal in other_schema.goals)

    # Camera imaging and other SDKs must not borrow this firmware-image row.
    for asked, condition in (
        ('libobkit.so 的摄像头成像要求是什么？',
         {'sdk': 'libobkit', 'task': 'firmware_image_requirement'}),
        ('OrbbecSDK 包内 README 对整机镜像怎么说？',
         {'sdk': 'OrbbecSDK', 'task': 'inspect_package'}),
    ):
        scoped = DaqRequestSchema.from_candidate(asked, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': [], 'conditions': condition,
            }],
        }, sdk_catalog=catalog).goals[0]
        assert scoped.condition_map().get('task') != 'sdk_scope'


def test_libobkit_firmware_image_requirements_plural_keeps_readme_partial_evidence():
    question = '所以可以把 libobkit.so 拷到任意 EGO Pro 固件上用吗？'
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'platform': 'rk3576', 'sdk': 'libobkit',
                            'task': 'firmware_image_requirements'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO Pro'], 'conditions': {
                 'sdk': 'libobkit', 'task': 'copy_library_to_firmware'}},
        ],
    }, prior_user_context=('libobkit.so 是 RK3576 设备端 SDK 吗？'
                           '包内 README 对整机镜像怎么说？\n'
                           'README 对固件镜像和摄像头成像分别有什么要求？'),
        sdk_catalog=catalog)
    usage = next(goal for goal in schema.goals
                 if goal.capability == 'sdk_evidence')
    assert usage.condition_map()['task'] == 'sdk_scope'
    assert usage.unmapped_condition_keys == ()
    assert any(goal.capability == 'check_software_support'
               for goal in schema.goals)


def test_audio_playback_feature_is_grounded_by_unique_reviewed_host_example():
    catalog = _reviewed_sdk_row('daq-sdk-host-audio-007')
    question = 'OrbbecSDK 2.9.0 的音频播放怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK',
                           'sdk_version': '2.9.0'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.unmapped_condition_keys == ()

    # A different SDK identity or target platform cannot borrow this source.
    for asked, conditions in (
        ('libobkit 音频播放怎么做？',
         {'feature': 'audio_playback', 'sdk': 'libobkit'}),
        ('OrbbecSDK 2.9.0 Windows 音频播放怎么做？',
         {'feature': 'audio_playback', 'sdk': 'OrbbecSDK',
          'sdk_version': '2.9.0', 'platform': 'Windows'}),
        ('OrbbecSDK 2.9.0 Win11 音频播放怎么做？',
         {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'}),
        ('OrbbecSDK 2.9.0 在 Linux ARM64 怎么播放音频？',
         {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'}),
        ('OrbbecSDK 2.8.0 的音频播放怎么做？',
         {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'}),
    ):
        scoped = DaqRequestSchema.from_candidate(asked, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': [], 'conditions': conditions,
            }],
        }, sdk_catalog=catalog).goals[0]
        assert 'feature' in scoped.unmapped_condition_keys


def test_explicit_audio_sdk_version_survives_model_omission():
    catalog = _reviewed_sdk_row('daq-sdk-host-audio-007')
    for version in ('2.9.0', '2.8.0'):
        schema = DaqRequestSchema.from_candidate(
            f'OrbbecSDK {version} 的音频播放怎么做？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                    'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
                }],
            }, sdk_catalog=catalog)
        assert schema.goals[0].condition_map()['sdk_version'] == version
        assert ('feature' in schema.goals[0].unmapped_condition_keys) == (version != '2.9.0')


def test_explicit_audio_cpu_architecture_survives_model_omission():
    for question in (
        'OrbbecSDK 2.9.0 在 Linux ARM64 怎么播放音频？',
        'OrbbecSDK 2.9.0 在 Ubuntu ARM64 怎么播放音频？',
        'OrbbecSDK 2.9.0 在 ARM64 Linux 怎么播放音频？',
        'OrbbecSDK 2.9.0 在 Linux ARMv8 怎么播放音频？',
    ):
        schema = DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
            }],
        }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
        assert schema.goals[0].condition_map()['platform'] == 'Linux arm64'
        assert 'feature' in schema.goals[0].unmapped_condition_keys

    other = DaqRequestSchema.from_candidate(
        'OrbbecSDK 2.9.0 在 Linux riscv64 怎么播放音频？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
            }],
        }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert other.goals[0].condition_map()['platform'] == 'Linux riscv64'
    assert 'feature' in other.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('device', ('EGO Pro', 'WristCam'))
def test_audio_package_example_does_not_erase_device_support_goal(device):
    catalog = _reviewed_sdk_row('daq-sdk-host-audio-007')
    schema = DaqRequestSchema.from_candidate(
        f'{device} 是否支持 OrbbecSDK 2.9.0 音频播放？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
            }],
        }, sdk_catalog=catalog)
    assert len(schema.goals) == 2
    support = next(goal for goal in schema.goals
                   if goal.capability == 'check_software_support')
    assert support.entities == (device,)
    assert support.condition_map() == {'sdk': 'OrbbecSDK',
                                       'sdk_version': '2.9.0',
                                       'feature': 'audio_playback'}


def test_audio_usage_and_device_applicability_remain_separate_across_clauses():
    question = 'OrbbecSDK 2.9.0 的音频播放怎么做？EGO Pro 是否支持？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert {goal.capability for goal in schema.goals} == {
        'sdk_evidence', 'check_software_support'}
    assert next(goal for goal in schema.goals if
                goal.capability == 'check_software_support').entities == ('EGO Pro',)


def test_model_generic_sdk_goal_cannot_erase_audio_topic_or_device_support():
    question = 'OrbbecSDK 2.9.0 的音频播放示例适用于 EGO Pro 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    usage = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    assert usage.condition_map()['feature'] == 'audio_playback'
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('EGO Pro',) for goal in schema.goals)


def test_model_empty_sdk_selectors_keep_user_named_audio_sdk_and_device():
    question = 'OrbbecSDK 2.9.0 的音频播放示例适用于 EGO Pro 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    usage = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    assert usage.condition_map()['sdk'] == 'OrbbecSDK'
    assert usage.condition_map()['sdk_version'] == '2.9.0'
    assert usage.condition_map()['feature'] == 'audio_playback'
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('EGO Pro',) for goal in schema.goals)


def test_audio_comparison_retains_unreviewed_second_library():
    question = 'OrbbecSDK 2.9.0 和 SDL2 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert scoped['OrbbecSDK'].condition_map()['feature'] == 'audio_playback'
    assert scoped['SDL2'].condition_map()['feature'] == 'audio_playback'
    assert 'sdk' in scoped['SDL2'].unmapped_condition_keys


def test_audio_in_another_clause_does_not_relabel_orbbecsdk_package_goal():
    question = 'OrbbecSDK 2.9.0 的包内容有哪些？OpenAL 的音频播放怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert 'feature' not in scoped['OrbbecSDK'].condition_map()
    assert scoped['OpenAL'].unmapped_condition_keys


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2.9.0 和 SDL2 音频播放分别怎么做？',
    'OrbbecSDK 2.9.0 音频播放怎么做？SDL2 呢？',
    'OrbbecSDK 2.9.0 与 SDL2 分别怎么播放音频？',
))
def test_audio_second_library_survives_natural_comparison_forms(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert scoped['OrbbecSDK'].condition_map()['feature'] == 'audio_playback'
    assert scoped['SDL2'].condition_map()['feature'] == 'audio_playback'
    assert scoped['SDL2'].unmapped_condition_keys


def test_same_clause_other_library_audio_does_not_relabel_package_goal():
    question = 'OrbbecSDK 2.9.0 的包内容与 OpenAL 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert 'feature' not in scoped['OrbbecSDK'].condition_map()
    assert scoped['OpenAL'].unmapped_condition_keys


@pytest.mark.parametrize('audio_key', ('task', 'feature'))
def test_separate_package_and_openal_does_not_create_sdk_integration_goal(audio_key):
    question = 'OrbbecSDK 2.9.0 的包内容与 OpenAL 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'api': 'OpenAL', audio_key: 'audio_playback'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert not any(goal.condition_map().get('sdk') == 'OrbbecSDK'
                   and goal.condition_map().get('api') == 'OpenAL'
                   for goal in schema.goals)
    assert any(goal.condition_map().get('sdk') == 'OpenAL'
               and goal.unmapped_condition_keys for goal in schema.goals)


def test_explicit_orbbec_openal_integration_keeps_user_requested_goal():
    question = 'OrbbecSDK 2.9.0 的音频播放如何对接 OpenAL？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'api': 'OpenAL', 'feature': 'audio_playback'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert any(goal.condition_map().get('api') == 'OpenAL'
               for goal in schema.goals)


def test_magnetometer_presence_recovers_source_bound_feature_when_model_omits_it():
    question = ('OrbbecSDK 2026-09-29 的 Linux x86_64 包里有磁力计示例吗？'
                'Android 包怎么编译？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'platform': 'linux_x86_64',
                            'sdk_release_date': '2026-09-29',
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'platform': 'android',
                            'sdk_release_date': '2026-09-29', 'task': 'build'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023'))
    scoped = {goal.condition_map()['platform']: goal for goal in schema.goals}
    assert scoped['linux_x86_64'].condition_map() == {
        'sdk': 'OrbbecSDK', 'platform': 'linux_x86_64',
        'sdk_release_date': '2026-09-29', 'feature': 'magnetometer'}
    assert scoped['linux_x86_64'].unmapped_condition_keys == ()
    assert scoped['android'].condition_map()['task'] == 'build'
    assert scoped['android'].unmapped_condition_keys


def test_cmake_request_keeps_unknown_method_and_exposes_reviewed_makefile_build():
    question = ('OrbbecSDK 2.9.0 Linux x86_64 的磁力计示例和 '
                'RK3576 libobkit 的 CMake 编译安装分别怎么做？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'platform': 'linux_x86_64'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'libobkit', 'platform': 'rk3576',
                            'task': 'cmake_build_install'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-build-012'))
    libobkit_goals = [goal for goal in schema.goals
                     if goal.condition_map().get('sdk') == 'libobkit']
    assert len(libobkit_goals) == 2
    assert any(goal.condition_map().get('task') == 'cmake_build_install'
               and goal.unmapped_condition_keys for goal in libobkit_goals)
    assert any(goal.condition_map().get('task') == 'build'
               and not goal.unmapped_condition_keys for goal in libobkit_goals)


def test_cmake_request_cannot_borrow_makefile_from_another_libobkit_build():
    question = 'RK3576 libobkit 20260923 的 CMake 编译安装怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit', 'platform': 'rk3576',
                           'sdk_release_date': '2026-09-23',
                           'task': 'cmake_build_install'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-build-012'))
    assert not any(goal.condition_map().get('task') == 'build'
                   for goal in schema.goals)


def test_existing_generic_second_sdk_goal_cannot_certify_audio_from_other_tasks():
    question = 'OrbbecSDK 2.9.0 和 libobkit 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': sdk}}
            for sdk in ('OrbbecSDK', 'libobkit')],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert scoped['OrbbecSDK'].condition_map()['feature'] == 'audio_playback'
    assert scoped['libobkit'].condition_map()['feature'] == 'audio_playback'
    assert 'feature' in scoped['libobkit'].unmapped_condition_keys


def test_audio_example_applicability_keeps_sdk_usage_and_device_support_separate():
    question = 'OrbbecSDK 2.9.0 的音频播放示例适用于 WristCam 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    usage = [goal for goal in schema.goals if goal.capability == 'sdk_evidence']
    assert len(usage) == 1
    assert usage[0].condition_map()['feature'] == 'audio_playback'
    assert usage[0].unmapped_condition_keys == ()
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('WristCam',) for goal in schema.goals)


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2.9.0 的音频播放支持 WristCam 吗？',
    'OrbbecSDK 2.9.0 的音频播放能否用于 WristCam？',
    'OrbbecSDK 2.9.0 的音频播放是否支持 WristCam？',
    'OrbbecSDK 2.9.0 的音频播放能否使用 WristCam？',
    'OrbbecSDK 2.9.0 的音频播放可以用于 WristCam 吗？',
))
def test_audio_support_reversed_wording_keeps_both_obligations(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    usage = [goal for goal in schema.goals if goal.capability == 'sdk_evidence']
    assert len(usage) == 1
    assert usage[0].condition_map()['feature'] == 'audio_playback'
    assert usage[0].unmapped_condition_keys == ()
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('WristCam',) for goal in schema.goals)


def test_audio_followup_named_sdk_with_build_keeps_task():
    question = 'OrbbecSDK 2.9.0 的音频播放怎么做？libobkit 20260909 呢？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': sdk}}
            for sdk in ('OrbbecSDK', 'libobkit')],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert scoped['OrbbecSDK'].condition_map()['feature'] == 'audio_playback'
    assert scoped['libobkit'].condition_map()['feature'] == 'audio_playback'
    assert 'feature' in scoped['libobkit'].unmapped_condition_keys


@pytest.mark.parametrize('question', (
    'OrbbecSDK音频播放怎么做？',
    'OrbbecSDK 2.9.0音频播放怎么做？',
))
def test_audio_sdk_name_and_version_accept_adjacent_chinese(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    goal = schema.goals[0]
    assert goal.condition_map()['sdk'] == 'OrbbecSDK'
    assert goal.condition_map()['feature'] == 'audio_playback'
    assert 'feature' not in goal.unmapped_condition_keys
    if '2.9.0' in question:
        assert goal.condition_map()['sdk_version'] == '2.9.0'


def test_named_calibration_yaml_raw_bytes_task_uses_reviewed_example():
    question = 'EGO C++ 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp',
                           'export_files': ['align_calib.yaml', 'imu_calib.yaml'],
                           'task': 'export_raw_bytes'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    goal = schema.goals[0]
    assert goal.condition_map()['task'] == 'calibration_export'
    assert goal.condition_map()['export_files'] == [
        'align_calib.yaml', 'imu_calib.yaml']
    assert goal.unmapped_condition_keys == ()


@pytest.mark.parametrize('question', (
    'Windows C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'Win11 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'macOS系统 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'Android设备 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'iOS平台 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'Linux ARM64设备 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'Linux ARMv8设备 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'Linux riscv64设备 C++ EGO 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'EGO Pro C++ 示例中 align_calib.yaml 和 imu_calib.yaml 怎样按原始字节导出？',
    'EGO C++ 示例中 align_calib.yaml、imu_calib.yaml 和 extra.json 怎样按原始字节导出？',
))
def test_named_calibration_raw_bytes_cannot_borrow_wrong_scope(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp',
                           'export_files': ['align_calib.yaml', 'imu_calib.yaml'],
                           'task': 'export_raw_bytes'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    assert 'task' in schema.goals[0].unmapped_condition_keys


def test_calibration_yaml_and_extra_json_split_known_and_unknown_goals():
    question = 'EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出外参标定 JSON 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp', 'export_files': [
                'alignment_yaml', 'imu_calibration_yaml',
                'extrinsic_calibration_json']},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    assert any(goal.condition_map().get('task') == 'calibration_export'
               and 'task' not in goal.unmapped_condition_keys
               for goal in schema.goals)
    assert any('export_files' in goal.unmapped_condition_keys
               and 'extrinsic_calibration_json' in
               goal.condition_map().get('export_files', [])
               for goal in schema.goals)


@pytest.mark.parametrize('suffix', (
    '我用的是 EGO Pro。', '我用的是 WristCam。',
    '我用 Mac。', '我用 Darwin。', '我还要导出额外的 TXT。',
))
def test_calibration_yaml_split_never_discards_following_scope(suffix):
    question = ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，'
                '还能导出外参标定 JSON 吗？' + suffix)
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp', 'export_files': [
                'alignment_yaml', 'imu_calibration_yaml',
                'extrinsic_calibration_json']},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    assert not any(goal.condition_map().get('task') == 'calibration_export'
                   for goal in schema.goals)


def test_calibration_yaml_split_never_discards_scope_inside_json_clause():
    question = ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，'
                '还能在 EGO Pro 上导出外参标定 JSON 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp', 'export_files': [
                'alignment_yaml', 'imu_calibration_yaml',
                'extrinsic_calibration_json']},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    assert not any(goal.condition_map().get('task') == 'calibration_export'
                   for goal in schema.goals)


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2.9.0 的音频播放示例适用于 EGO Pro 吗？',
    'EGO Pro 能用 OrbbecSDK 2.9.0 播放音频吗？',
    'EGO Pro 能否用 OrbbecSDK 2.9.0 播放音频？',
    '我手上的 EGO Pro 是否支持 OrbbecSDK 2.9.0 音频播放？',
))
def test_audio_applicability_paraphrases_keep_device_support_goal(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    support = [goal for goal in schema.goals
               if goal.capability == 'check_software_support']
    assert len(support) == 1
    assert support[0].entities == ('EGO Pro',)


@pytest.mark.parametrize('question', (
    'EGO Pro 和 WristCam 都支持 OrbbecSDK 2.9.0 音频播放吗？',
    'OrbbecSDK 2.9.0 音频播放示例适用于 EGO Pro 和 WristCam 吗？',
))
def test_audio_applicability_preserves_each_named_device(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert {goal.entities for goal in schema.goals
            if goal.capability == 'check_software_support'} == {
        ('EGO Pro',), ('WristCam',)}


@pytest.mark.parametrize(('question', 'platform'), (
    ('EGO Pro 是否支持 OrbbecSDK 2.9.0 在 Ubuntu ARM64 音频播放？',
     'Linux arm64'),
    ('EGO Pro 是否支持 OrbbecSDK 2.9.0 ARM64 音频播放？',
     'unspecified OS arm64'),
    ('EGO Pro 是否支持 OrbbecSDK 2.9.0 在 Windows ARM64 音频播放？',
     'Windows arm64'),
))
def test_synthesized_audio_usage_goal_keeps_explicit_platform_and_version(
        question, platform):
    catalog = _reviewed_sdk_row('daq-sdk-host-audio-007')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'check_software_support',
            'entities': ['EGO Pro'],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'feature': 'audio_playback'},
        }],
    }, sdk_catalog=catalog)
    usage = [goal for goal in schema.goals
             if goal.capability == 'sdk_evidence']
    assert len(usage) == 1
    assert usage[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
        'feature': 'audio_playback', 'platform': platform}

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{
            'id': goal.id, 'capability': goal.capability,
            'entities': list(goal.entities),
            'conditions': goal.condition_map(),
            'unmapped_condition_keys': list(goal.unmapped_condition_keys),
        } for goal in schema.goals],
    ).dispatch('sdk_evidence', {'query': question})
    assert usage[0].id not in result.content['matched_requirement_ids']


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2.9.0 和 2.8.0 的音频播放用法是否相同？',
    'OrbbecSDK 2.9.0 和 OrbbecSDK 2.8.0 的音频播放用法是否相同？',
))
def test_audio_version_comparison_keeps_each_version_obligation(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert {goal.condition_map().get('sdk_version') for goal in schema.goals} == {
        '2.9.0', '2.8.0'}


def test_model_versioned_audio_goals_do_not_collide_during_comparison():
    question = 'OrbbecSDK 2.9.0 和 2.8.0 的音频播放用法是否相同？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK',
                            'sdk_version': version}}
            for version in ('2.9.0', '2.8.0')],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert {goal.condition_map()['sdk_version'] for goal in schema.goals} == {
        '2.9.0', '2.8.0'}


def test_audio_support_goals_preserve_each_device_version_and_platform():
    question = ('EGO Pro 和 WristCam 都支持 OrbbecSDK 2.9.0 和 2.8.0 '
                '在 Linux x86_64 音频播放吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    assert {(goal.entities, goal.condition_map().get('sdk_version'),
             goal.condition_map().get('platform'))
            for goal in schema.goals if goal.capability == 'check_software_support'} == {
        ((device,), version, 'Linux x86_64')
        for device in ('EGO Pro', 'WristCam')
        for version in ('2.9.0', '2.8.0')}


def test_ubuntu_arm64_audio_support_goal_keeps_architecture_scope():
    question = 'EGO Pro 是否支持 OrbbecSDK 2.9.0 在 Ubuntu ARM64 音频播放？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    support = next(goal for goal in schema.goals
                   if goal.capability == 'check_software_support')
    assert support.condition_map()['platform'] == 'Linux arm64'


@pytest.mark.parametrize(('platform_phrase', 'expected'), (
    ('ARM64', 'unspecified OS arm64'),
    ('Windows ARM64', 'Windows arm64'),
    ('macOS ARM64', 'macOS arm64'),
    ('Windows x64', 'Windows x64'),
    ('Win11 ARM64', 'Windows 11 arm64'),
))
def test_audio_support_keeps_explicit_cpu_scope_across_operating_systems(
        platform_phrase, expected):
    question = (f'EGO Pro 是否支持 OrbbecSDK 2.9.0 在 {platform_phrase} '
                '音频播放？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'audio_playback', 'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-audio-007'))
    support = next(goal for goal in schema.goals
                   if goal.capability == 'check_software_support')
    assert support.condition_map()['platform'] == expected
    assert 'feature' in schema.goals[0].unmapped_condition_keys
    if platform_phrase == 'ARM64':
        assert 'platform' in support.unmapped_condition_keys


def test_model_false_stereo_default_does_not_abort_explicit_baseline_conflict():
    question = ('EGO Pro 前向双目基线，有人说规格书是 100 mm，'
                '又拿 Quad-EGO 网页的 120 mm 要我填表。'
                '能直接定为 120 mm 吗？顺便说 EGO Pro 已核对的 RGB 相机数量。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO Pro'], 'field': 'stereo_mono_baseline',
             'conditions': {'stereo_pair': False}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO Pro'], 'field': 'rgb_camera_count',
             'conditions': {}},
        ],
    })
    assert any(goal.field == 'rgb_camera_count' for goal in schema.goals)
    assert any('baseline' in str(goal.field) for goal in schema.goals)


def test_front_stereo_pair_description_keeps_orientation_unmapped():
    question = ('EGO Pro 前向双目基线是规格书的 100 mm，还是可以按 '
                'Quad-EGO 网页上的 120 mm 填表？另外，EGO Pro 已核对的 '
                'RGB 相机数量是多少？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO Pro'], 'field': 'stereo_mono_baseline',
             'conditions': {'stereo_pair': 'front'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO Pro'], 'field': 'rgb_camera_count',
             'conditions': {}},
        ],
    })
    baseline = next(goal for goal in schema.goals if goal.field == 'stereo_mono_baseline')
    assert baseline.condition_map()['direction'] == 'front'
    assert 'direction' in baseline.unmapped_condition_keys
    assert any(goal.field == 'rgb_camera_count' for goal in schema.goals)


def test_model_sample_type_descriptor_uses_reviewed_example_scope():
    catalog = _reviewed_sdk_row('daq-sdk-host-sync-001')
    question = ('OrbbecSDK 2.9.0 的 C++ 多设备主从同步示例会检查哪些能力？'
                '这能保证 EGO Pro 加两腕带经 HUB 同步录制吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'language': 'cpp', 'sample_type': 'multi_device_sync',
                           'feature': 'multi_device_sync_mode_check_and_set'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert 'sample_type' not in goal.condition_map()
    assert not goal.unmapped_condition_keys

    described_task = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'language': 'cpp', 'task': 'multi_device_sync'},
        }],
    }, sdk_catalog=catalog).goals[0]
    assert described_task.condition_map()['task'] == 'multi_device_sync_mode_check_and_set'
    assert not described_task.unmapped_condition_keys

    unrelated = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'sample_type': 'face_recognition'},
        }],
    }, sdk_catalog=catalog)
    assert unrelated.goals[0].unmapped_condition_keys == ('sample_type',)


def test_reviewed_sync_example_resolves_model_feature_without_certifying_combination():
    catalog = _reviewed_sdk_row('daq-sdk-host-sync-001')
    question = ('OrbbecSDK 2.9.0 的 C++ 多设备主从同步示例会检查哪些能力？'
                '这能保证 EGO Pro 加两腕带经 HUB 同步录制吗？')
    base = {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
            'language': 'cpp', 'feature': 'multi_device_sync'}

    def scoped(conditions):
        return DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [
                {'kind': 'product', 'capability': 'sdk_evidence',
                 'entities': [], 'conditions': conditions},
                {'kind': 'product', 'capability': 'check_software_support',
                 'entities': ['EGO Pro', '腕带', 'HUB'],
                 'conditions': {'sdk': 'OrbbecSDK',
                                'feature': 'multi_device_sync'}},
            ],
        }, sdk_catalog=catalog).goals

    correct = scoped(base)
    assert correct[0].condition_map()['feature'] == 'multi_device_sync_mode_check_and_set'
    assert correct[0].unmapped_condition_keys == ()
    assert correct[1].unmapped_condition_keys == ('feature',)
    for change in ({'sdk': 'libobkit'}, {'platform': 'android'}):
        wrong = scoped({**base, **change})
        assert 'feature' in wrong[0].unmapped_condition_keys
    old_question = question.replace('2.9.0', '2.8.0')
    old = DaqRequestSchema.from_candidate(old_question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {**base, 'sdk_version': '2.8.0'},
        }],
    }, sdk_catalog=catalog)
    assert 'feature' in old.goals[0].unmapped_condition_keys


def test_multiturn_sync_feature_grounds_from_checked_example_scope():
    catalog = _reviewed_sdk_row('daq-sdk-host-sync-001')
    first = ('OrbbecSDK 2.9.0 的 EGO 多设备主从同步 C++ 示例'
             '如何判断模式支持？')
    last = ('先别给组合保证，只解释示例要求的读写权限、位图和'
            '固件最低版本提示。')
    prior = first + '\n那用这个示例能证明我的 EGO Pro 加两台 WristCam 经 HUB 同步录制吗？'
    conditions = {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                  'language': 'cpp',
                  'feature': 'multi_device_sync_mode_check_and_set'}

    def goal(question, *, prior_user_context='', scoped=conditions):
        return DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': ['EGO'] if question == first else [],
                'conditions': scoped,
            }],
        }, prior_user_context=prior_user_context,
           sdk_catalog=catalog).goals[0]

    assert goal(first).unmapped_condition_keys == ()
    assert goal(last, prior_user_context=prior).unmapped_condition_keys == ()
    assert 'feature' in goal(first.replace('2.9.0', '2.8.0'),
                             scoped={**conditions, 'sdk_version': '2.8.0'}).unmapped_condition_keys
    assert 'feature' in goal(first, scoped={**conditions, 'sdk': 'libobkit'}).unmapped_condition_keys
    assert 'feature' in goal(first, scoped={**conditions, 'platform': 'android'}).unmapped_condition_keys


def test_explicit_prior_log_followup_reopens_visible_user_attachment():
    from daq_fae.workflow import allowed_tools_for
    question = ('刚才日志出现 E42。现在可以把它当官方确认的 EGO Pro '
                '固件 v9.9.9 不兼容结论发给客户吗？')
    assert refers_to_prior_attachment(question)
    assert not refers_to_prior_attachment('EGO Pro 固件 v9.9.9 的变更是什么？')
    candidate = {'confidence': 'medium', 'attachment_dependency': 'unknown',
                 'goals': [{'kind': 'product',
                            'capability': 'check_software_support',
                            'entities': ['EGO Pro'],
                            'conditions': {'firmware_version': 'v9.9.9'}}]}
    scoped = DaqRequestSchema.from_candidate(
        question, candidate, attachment_present=True)
    assert scoped.attachment_dependency == 'relevant'
    assert {'search_attachments', 'read_attachment'} <= allowed_tools_for(scoped)
    absent = DaqRequestSchema.from_candidate(
        question, candidate, attachment_present=False)
    assert absent.attachment_dependency == 'unknown'
    unrelated = DaqRequestSchema.from_candidate(
        'EGO Pro 固件 v9.9.9 的变更是什么？', candidate,
        attachment_present=True)
    assert unrelated.attachment_dependency == 'unknown'


def test_reviewed_depth_example_resolves_model_preset_task_in_example_scope():
    catalog = _reviewed_sdk_row('daq-sdk-host-depth-009')
    question = ('OrbbecSDK 的 EGO Depth 网络示例切换预设后，'
                '我还能沿用切换前查到的 StreamProfile 吗？')
    base = {'sdk': 'OrbbecSDK', 'connection': 'network',
            'task': 'preset_switch'}

    def scoped(conditions, asked=question):
        return DaqRequestSchema.from_candidate(asked, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence',
                'entities': ['EGO Depth'], 'conditions': conditions,
            }],
        }, sdk_catalog=catalog).goals[0]

    correct = scoped(base)
    assert correct.condition_map()['task'] == 'preset_profile'
    assert correct.unmapped_condition_keys == ()
    action_first = scoped({**base, 'task': 'switch_preset'})
    assert action_first.condition_map()['task'] == 'preset_profile'
    assert action_first.unmapped_condition_keys == ()
    for change in ({'sdk_version': '2.8.0'}, {'platform': 'android'},
                   {'sdk': 'libobkit'}):
        assert 'task' in scoped({**base, **change}).unmapped_condition_keys
        assert 'task' in scoped({**base, **change,
                                 'task': 'switch_preset'}).unmapped_condition_keys
    other = scoped(base, 'OrbbecSDK 的 EGO Depth 网络示例能做面部识别吗？')
    assert other.unmapped_condition_keys == ('task',)


def test_mode_conditioned_rgb_fps_also_checks_mode_availability():
    question = '如果用这第二个版本的 1920×1080 RGB 模式，帧率也能填 30 fps 吗？'
    prior = ('我的 EGO 双目是 1600×1200 版本，水平和垂直视场角是多少？\n'
             '换成 1920×1200 版本后，视场角和已核实的最大 RGB 帧率分别是多少？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
            'conditions': {'resolution': '1920x1080',
                           'resolution_variant': '1920x1200'},
        }],
    }, prior_user_context=prior)
    fields = {goal.field: goal.condition_map() for goal in schema.goals
              if goal.capability == 'lookup_spec'}
    assert fields['rgb_max_frame_rate'] == {
        'resolution': '1920x1080', 'resolution_variant': '1920x1200'}
    assert fields['rgb_resolution'] == {'resolution_variant': '1920x1200'}


def test_network_ego_sdk_usage_keeps_product_and_network_scope():
    question = ('2026-09-29 Android Wrapper beta2.2.5 的 Java API，网络 EGO 的 '
                'SD 卡文件信息与本地下载该怎么调用？这证明具体固件支持吗？')
    catalog = _reviewed_sdk_row('daq-sdk-android-sdcard-018')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['网络 EGO'],
             'conditions': {'language': 'java', 'platform': 'android',
                            'sdk': 'Android Wrapper',
                            'sdk_release_date': '2026-09-29',
                            'sdk_version': 'beta2.2.5',
                            'task': 'sd_card_file_info_and_local_download'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['网络 EGO'], 'conditions': {}},
        ],
    }, sdk_catalog=catalog)
    usage = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    assert usage.entities == ('EGO',)
    assert usage.condition_map()['connection'] == 'network'
    assert next(goal for goal in schema.goals if goal.capability ==
                'check_software_support').entities == ('网络 EGO',)

    from daq_fae.domain_tools import DaqToolBox
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': list(usage.entities),
                             'conditions': usage.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']


def test_sdk_schema_drops_model_metadata_when_exact_apis_ground_the_source():
    question = ('Android Wrapper 中 SAVING 和 RECORD_SAVED 有何区别？'
                'setEgoStateCallback 与 setEgoRecordingResultCallback 各适合什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {
                'feature': 'saving_vs_record_saved', 'standalone': True,
                'steps': ['sdk="android_wrapper"', 'platform="android"',
                          'api=["setEgoStateCallback","setEgoRecordingResultCallback"]'],
                'task': 'android_recording_state_callback_semantics',
            },
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    goal = schema.goals[0]
    assert goal.condition_map() == {}
    assert goal.unmapped_condition_keys == ()


def test_sdk_followup_normalizes_descriptive_callback_as_source_task():
    question = '综合这两条线索，我能向客户确认完整落盘吗？'
    prior = ('Android Java 录制状态回调显示 SAVING，是否表示文件已经保存完了？'
             '另外 EGO Pro 单机 TF 卡录制时，停止后绿灯常亮但没听见“数据已保存”。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'recording_state_callback',
                           'feature': 'recording_state_saving',
                           'language': 'java', 'platform': 'android'},
        }],
    }, prior_user_context=prior,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    goal = schema.goals[0]
    assert 'api' not in goal.condition_map()
    assert 'feature' not in goal.unmapped_condition_keys
    from daq_fae.domain_tools import DaqToolBox
    catalog = _reviewed_sdk_row('daq-sdk-android-record-state-019')
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': goal.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']


def test_sdk_followup_replaces_descriptive_task_only_with_source_anchored_task():
    question = '综合这两条线索，我能向客户确认完整落盘吗？下一步最小核对是什么？'
    prior = ('Android Java 录制状态回调显示 SAVING，是否表示文件已经保存完了？'
             '另外 EGO Pro 单机 TF 卡录制时，停止后绿灯常亮但没听见“数据已保存”。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'recording_state_callback',
                           'language': 'java', 'platform': 'android',
                           'task': 'recording_state_saving_semantics'},
        }],
    }, prior_user_context=prior,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    goal = schema.goals[0]
    assert goal.condition_map() == {'language': 'java', 'platform': 'android',
                                    'task': 'recording_state'}
    assert goal.unmapped_condition_keys == ()

    unsupported = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'recording_state_callback',
                           'language': 'java', 'platform': 'android',
                           'task': 'recording_state_remote_reset'},
        }],
    }, prior_user_context=prior,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019')).goals[0]
    assert unsupported.condition_map()['task'] == 'recording_state_remote_reset'
    assert unsupported.unmapped_condition_keys

    confirmation = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'recording_state_callback',
                           'feature': 'recording_state_saving',
                           'language': 'java', 'platform': 'android',
                           'task': 'confirm_recording_saved'},
        }],
    }, prior_user_context=prior,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019')).goals[0]
    assert confirmation.condition_map() == {
        'language': 'java', 'platform': 'android', 'task': 'recording_state'}
    assert confirmation.unmapped_condition_keys == ()

    status_alias = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'feature': 'recording_status_callback',
                           'language': 'java', 'platform': 'android'},
        }],
    }, prior_user_context=prior,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019')).goals[0]
    assert status_alias.condition_map() == {
        'language': 'java', 'platform': 'android'}
    assert status_alias.unmapped_condition_keys == ()


def test_two_reviewed_callbacks_do_not_erase_user_asked_remote_reset_task():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = ('Android Java Wrapper 的 setEgoStateCallback 和 '
                'setEgoRecordingResultCallback 能远程重置设备录制状态吗？')
    catalog = _reviewed_sdk_row('daq-sdk-android-record-state-019')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'language': 'java', 'platform': 'android',
                           'task': 'recording_state_remote_reset'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map()['task'] == 'recording_state_remote_reset'
    assert 'task' in goal.unmapped_condition_keys
    assert len(schema.goals) == 2
    known_goal = schema.goals[1]
    assert known_goal.condition_map() == {
        'api': ['setEgoStateCallback', 'setEgoRecordingResultCallback'],
        'language': 'java', 'platform': 'android'}
    assert known_goal.unmapped_condition_keys == ()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=plan.requirements
                        ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert [row['kb_id'] for row in result.content['matches']] == [
        'daq-sdk-android-record-state-019']
    assert result.content['matched_requirement_ids'] == [plan.requirements[1]['id']]
    assert result.content['partial_requirement_ids'] == [plan.requirements[0]['id']]

    wrong_build = [dict(plan.requirements[0], conditions={
        **plan.requirements[0]['conditions'], 'sdk_release_date': '2026-09-21'})]
    wrong_result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=wrong_build,
    ).dispatch('sdk_evidence', {'query': question})
    assert wrong_result.status == 'not_found'

    feature_schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'language': 'java', 'platform': 'android',
                           'feature': 'remote_reset'},
        }],
    }, sdk_catalog=catalog)
    feature_goal = feature_schema.goals[0]
    assert feature_goal.condition_map()['feature'] == 'remote_reset'
    assert 'feature' in feature_goal.unmapped_condition_keys
    assert len(feature_schema.goals) == 2
    feature_result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=feature_schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert feature_result.status == 'ok'
    assert [row['kb_id'] for row in feature_result.content['matches']] == [
        'daq-sdk-android-record-state-019']
    feature_plan = prepare_turn(question, request_schema=feature_schema)
    assert feature_result.content['matched_requirement_ids'] == [
        feature_plan.requirements[1]['id']]


@pytest.mark.parametrize('selector_key', ['task', 'api', 'feature'])
def test_recording_callback_cannot_certify_unreviewed_delete_action(selector_key):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = ('OrbbecSDK Android Java Wrapper 的 setEgoStateCallback '
                '能删除录制状态吗？')
    catalog = _reviewed_sdk_row('daq-sdk-android-record-state-019')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {selector_key: 'recording_state_delete'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map().get(selector_key) == 'recording_state_delete'
    assert selector_key in goal.unmapped_condition_keys

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    for conditions in (goal.condition_map(),
                       {selector_key: 'recording_state_delete'}):
        result = DaqToolBox(
            question=question, knowledge=Knowledge(), role='internal_fae',
            requirements=[{
                'id': 'delete', 'capability': 'sdk_evidence',
                'entities': [], 'conditions': conditions,
            }],
        ).dispatch('sdk_evidence', {'query': question})
        assert result.status == 'not_found'


def test_callback_definition_goal_does_not_erase_asked_delete_action():
    from daq_fae.task_context import prepare_turn

    question = ('OrbbecSDK Android Java Wrapper 的 setEgoStateCallback '
                '能删除录制状态吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'platform': 'android',
                           'language': 'java', 'api': 'setEgoStateCallback'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    assert len(schema.goals) == 2
    delete = next(goal for goal in schema.goals
                  if goal.condition_map().get('task') == 'recording_state_delete')
    assert delete.condition_map()['api'] == 'setEgoStateCallback'
    assert delete.unmapped_condition_keys == ('task',)
    requirement = next(req for req in prepare_turn(
        question, request_schema=schema).requirements
        if req['goal_id'] == delete.id)
    assert requirement['reason'] == 'unmapped_condition_unverified'


def test_android_wrapper_callback_delete_goal_without_orbbecsdk_name():
    question = 'Android Java Wrapper 的 setEgoStateCallback 能删除录制状态吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'platform': 'android', 'language': 'java',
                           'api': 'setEgoStateCallback'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    assert len(schema.goals) == 2
    delete = next(goal for goal in schema.goals
                  if goal.condition_map().get('task') == 'recording_state_delete')
    assert delete.unmapped_condition_keys == ('task',)
    assert 'sdk' not in delete.condition_map()


def test_named_callback_delete_goal_without_sdk_name():
    question = 'setEgoStateCallback 能删除录制状态吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'setEgoStateCallback'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    assert len(schema.goals) == 2
    assert any(goal.condition_map().get('task') == 'recording_state_delete'
               and goal.unmapped_condition_keys == ('task',)
               for goal in schema.goals)
    assert all('sdk' not in goal.condition_map() for goal in schema.goals)


def test_callback_delete_followup_does_not_infer_orbbecsdk_from_catalog():
    question = 'setEgoStateCallback 能删除录制状态吗？'
    prior = ('Android Java Wrapper 的 setEgoStateCallback 中 SAVING 和 '
             'RECORD_SAVED 分别代表什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'setEgoStateCallback', 'language': 'java',
                           'platform': 'android'},
        }],
    }, prior_user_context=prior,
       sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    delete = next(goal for goal in schema.goals
                  if goal.condition_map().get('task') == 'recording_state_delete')
    assert 'sdk' not in delete.condition_map()


def test_second_callback_source_build_does_not_erase_delete_obligation():
    question = 'setEgoStateCallback 能删除录制状态吗？'
    catalog = _reviewed_sdk_row('daq-sdk-android-record-state-019')
    second = dict(catalog.records[0], kb_id='future-build-copy',
                  build='20261001')
    catalog = SdkCatalog([*catalog.records, second], catalog.manifest)
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'api': 'setEgoStateCallback'},
        }],
    }, sdk_catalog=catalog)
    assert len(schema.goals) == 2
    delete = next(goal for goal in schema.goals
                  if goal.condition_map().get('task') == 'recording_state_delete')
    assert delete.unmapped_condition_keys == ('task',)


def test_other_clear_callback_does_not_erase_recording_delete_goal():
    question = ('OrbbecSDK Android Java Wrapper 的 setEgoStateCallback '
                '能删除录制状态吗？还有 clearDirectionKeyCallback 怎么用？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'api': 'setEgoStateCallback'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'api': 'clearDirectionKeyCallback'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    assert len(schema.goals) == 3
    assert sum(goal.condition_map().get('task') == 'recording_state_delete'
               for goal in schema.goals) == 1


def test_unmapped_sdk_selector_cannot_satisfy_evidence_requirement():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = 'OrbbecSDK 2.9.0 的音频播放怎么做？'
    catalog = _reviewed_sdk_row('daq-sdk-host-sync-001')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'task': 'multi_device_sync_mode_check_and_set'},
        }],
    }, sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ('task',)
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['reason'] == 'unmapped_condition_unverified'

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    box = DaqToolBox(question=question, knowledge=Knowledge(),
                     role='internal_fae', requirements=[requirement])
    assert box._sdk_usage_requirement_ids(catalog.records) == []
    result = box.dispatch('sdk_evidence', {'query': 'multi device sync'})
    assert result.status == 'not_found'


@pytest.mark.parametrize('question', [
    'Android Java Wrapper 的 SAVING 和 RECORD_SAVED 能远程重置录制状态吗？',
    'Android Java Wrapper 的 setEgoStateCallback 和 SAVING 能远程重置录制状态吗？',
])
def test_status_constants_cannot_anchor_partial_callback_goal(question):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    catalog = _reviewed_sdk_row('daq-sdk-android-record-state-019')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'language': 'java', 'platform': 'android',
                           'task': 'remote_reset_recording_state'},
        }],
    }, sdk_catalog=catalog)
    assert len(schema.goals) == 1
    assert schema.goals[0].unmapped_condition_keys == ('task',)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'not_found'


@pytest.mark.parametrize('sdk_prefix', [
    '', 'OrbbecSDK 2.9.0 ', 'OrbbecSDK_v2.9.0 ',
])
@pytest.mark.parametrize('model_outputs', [
    'alignment calibration YAML, IMU calibration YAML',
    ['alignment_calibration.yaml', 'imu_calibration.yaml'],
])
def test_descriptive_calibration_export_files_do_not_hide_reviewed_sample(
        sdk_prefix, model_outputs):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = (sdk_prefix + 'EGO C++ 示例导出对齐和 IMU 标定 YAML 时，是 SDK 已经解析好了内容，'
                '还是按原始字节导出？需要检查什么？')
    catalog = _reviewed_sdk_row('daq-sdk-host-calib-002')
    candidate = {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'export_files': model_outputs,
                           'language': 'cpp', 'task': 'export_calibration_yaml'},
        }],
    }
    schema = DaqRequestSchema.from_candidate(question, candidate,
                                             sdk_catalog=catalog)
    goal = schema.goals[0]
    expected = {'language': 'cpp', 'task': 'calibration_export'}
    if sdk_prefix:
        expected['sdk_version'] = '2.9.0'
    assert goal.condition_map() == expected
    assert goal.unmapped_condition_keys == ()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids']
    assert [row['kb_id'] for row in result.content['matches']] == [
        'daq-sdk-host-calib-002']

    wrong_question = question + ' 还要导出 my_private.yaml。'
    wrong_schema = DaqRequestSchema.from_candidate(
        wrong_question, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
                'conditions': {'export_files': ['my_private.yaml'],
                               'language': 'cpp', 'task': 'export_calibration_yaml'},
            }],
        }, sdk_catalog=catalog)
    assert wrong_schema.goals[0].condition_map()['export_files'] == [
        'my_private.yaml']
    wrong_result = DaqToolBox(
        question=wrong_question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(wrong_question,
                                  request_schema=wrong_schema).requirements,
    ).dispatch('sdk_evidence', {'query': wrong_question})
    assert wrong_result.status == 'not_found'


@pytest.mark.parametrize('question,export_files,entities', [
    ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出外参标定 JSON 吗？',
     ['alignment calibration YAML', 'IMU calibration YAML',
      'extrinsic calibration JSON'], ['EGO']),
    ('在 Windows C++ 的 EGO 示例中，导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('EGO Pro C++ 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('EGO 四目 C++ 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('EGO-Pro C++ 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('在EGO Pro的 C++ 示例中导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('在EGO-Depth的 C++ 示例中导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('macOS C++ 的 EGO 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('Linux ARM64 C++ 的 EGO 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
    ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出相机标定 YAML 吗？',
     ['alignment and IMU calibration YAML', 'camera calibration YAML'], ['EGO']),
    ('WristCam C++ 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', []),
    ('UMI Finger C++ 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', []),
    ('Mac C++ 的 EGO 示例导出对齐和 IMU 标定 YAML 时，是原始字节吗？',
     'alignment calibration YAML, IMU calibration YAML', ['EGO']),
])
def test_calibration_export_paraphrase_preserves_unsupported_scope(
        question, export_files, entities):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    catalog = _reviewed_sdk_row('daq-sdk-host-calib-002')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': entities, 'conditions': {
                'language': 'cpp', 'task': 'export_calibration_yaml',
                'export_files': export_files},
        }],
    }, sdk_catalog=catalog)
    assert 'export_files' in schema.goals[0].condition_map()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    if '外参标定 JSON' in question:
        assert result.status == 'ok'
        assert [row['kb_id'] for row in result.content['matches']] == [
            'daq-sdk-host-calib-002']
        assert len(result.content['matched_requirement_ids']) == 1
        assert any('export_files' in goal.unmapped_condition_keys
                   for goal in schema.goals)
    else:
        assert result.status == 'not_found'


@pytest.mark.parametrize('question', [
    'Windows C++ 的 EGO 示例能导出对齐和 IMU 标定 YAML 吗？',
    'EGO Pro C++ 示例能导出对齐和 IMU 标定 YAML 吗？',
    'EGO C++ 示例除对齐和 IMU 标定 YAML 外还能导出外参 JSON 吗？',
    'macOS C++ 的 EGO 示例能导出对齐和 IMU 标定 YAML 吗？',
    'Linux aarch64 C++ 的 EGO 示例能导出对齐和 IMU 标定 YAML 吗？',
    'EGO 四目 C++ 示例能导出对齐和 IMU 标定 YAML 吗？',
    'EGO-Pro C++ 示例能导出对齐和 IMU 标定 YAML 吗？',
    '在EGO Pro的 C++ 示例中能导出对齐和 IMU 标定 YAML 吗？',
    '在EGO-Depth的 C++ 示例中能导出对齐和 IMU 标定 YAML 吗？',
    'WristCam C++ 示例能导出对齐和 IMU 标定 YAML 吗？',
    'UMI Finger C++ 示例能导出对齐和 IMU 标定 YAML 吗？',
    'Mac C++ 的 EGO 示例能导出对齐和 IMU 标定 YAML 吗？',
    'EGO C++ 示例能导出对齐和 IMU 标定 TXT 文件吗？',
    'EGO C++ 示例能导出对齐和 IMU 标定 TXT文件吗？',
    'EGO C++ 示例能导出对齐和 IMU 标定 BIN文件吗？',
    'EGO C++ 示例能导出对齐和 IMU 标定 JSON格式吗？',
    'EGO C++ 示例能导出对齐和 IMU 标定 .bin 文件吗？',
    'EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出 my_private.yaml 吗？',
    'EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出 my_private.yaml文件吗？',
    'EGO C++ 示例能导出 my_private.yaml 吗？',
    'EGO C++ 示例能导出相机标定 YAML 吗？',
])
def test_calibration_export_tool_rechecks_user_scope_when_model_drops_it(question):
    from daq_fae.domain_tools import DaqToolBox

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = _reviewed_sdk_row('daq-sdk-host-calib-002')
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{
            'id': 'goal_dropped_scope', 'capability': 'sdk_evidence',
            'entities': ['EGO'], 'conditions': {
                'language': 'cpp', 'task': 'calibration_export'},
        }],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'not_found'


def test_calibration_export_does_not_certify_mixed_product_question():
    from daq_fae.domain_tools import DaqToolBox

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = _reviewed_sdk_row('daq-sdk-host-calib-002')
        def records_for(self, role):
            return [
                {'kind': 'entity', 'data': {'name': 'EGO'}},
                {'kind': 'entity', 'data': {'name': 'WristCam'}},
            ]
        def sections_for(self, role): return []

    question = 'EGO 和 WristCam C++ 示例能导出对齐和 IMU 标定 YAML 吗？'
    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{
            'id': 'mixed_product', 'capability': 'sdk_evidence',
            'entities': ['EGO'], 'conditions': {'task': 'calibration_export'},
        }],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'not_found'


def test_user_named_calibration_files_survive_model_comma_serialization():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = ('EGO C++ 示例中 align_calib.yaml 和 imu_calib.yaml '
                '怎样按原始字节导出？')
    catalog = _reviewed_sdk_row('daq-sdk-host-calib-002')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO'], 'conditions': {
                'language': 'cpp',
                'export_files': 'align_calib.yaml, imu_calib.yaml',
            },
        }],
    }, sdk_catalog=catalog)
    assert schema.goals[0].condition_map()['export_files'] == [
        'align_calib.yaml', 'imu_calib.yaml']

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids']


def test_libobkit_readme_image_scope_survives_model_feature_paraphrase():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn

    question = ('libobkit.so 有了就能拷到任意 RK3576 板子或任意 EGO Pro '
                '固件使用吗？包内 README 对镜像有什么要求？')
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': {
                'feature': 'image_requirements', 'platform': 'RK3576',
                'sdk': 'libobkit'},
        }],
    }, sdk_catalog=catalog)
    assert schema.goals[0].condition_map() == {
        'platform': 'RK3576', 'sdk': 'libobkit', 'task': 'sdk_scope'}
    assert schema.goals[0].unmapped_condition_keys == ()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': 'libobkit.so README 镜像要求 RK3576'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids']
    assert [source['source_id'] for source in result.sources] == [
        'daq-sdk-device-scope-011']


def test_libobkit_readme_image_scope_survives_model_task_paraphrase():
    question = ('libobkit.so 有了就能拷到任意 RK3576 板子或任意 EGO Pro '
                '固件使用吗？包内 README 对镜像有什么要求？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'task': 'image_requirements',
                           'platform': 'RK3576', 'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert schema.goals[0].condition_map() == {
        'platform': 'RK3576', 'sdk': 'libobkit', 'task': 'sdk_scope'}
    assert schema.goals[0].unmapped_condition_keys == ()


def test_sdk_image_scope_does_not_map_generic_image_quality_question():
    question = 'libobkit.so 的相机图像质量有什么要求？'
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': {
                'feature': 'image_requirements', 'sdk': 'libobkit'},
        }],
    }, sdk_catalog=catalog)
    assert schema.goals[0].condition_map()['feature'] == 'image_requirements'
    assert 'feature' in schema.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('question,prior', [
    ('它的相机图像质量有什么要求？',
     'libobkit.so 包内 README 对固件镜像有什么要求？'),
    ('libobkit.so 包内 README 对固件镜像和相机图像质量分别有什么要求？',
     ''),
])
def test_sdk_image_scope_mapping_does_not_erase_camera_image_goal(
        question, prior):
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': {
                'feature': 'image_requirements', 'sdk': 'libobkit'},
        }],
    }, sdk_catalog=catalog, prior_user_context=prior)
    assert schema.goals[0].condition_map()['feature'] == 'image_requirements'
    assert 'feature' in schema.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('question,prior', [
    ('它的相机图像质量有什么要求？',
     'libobkit.so 包内 README 对固件镜像有什么要求？'),
    ('libobkit.so 包内 README 对固件镜像和相机图像质量分别有什么要求？',
     ''),
    ('libobkit README 对固件镜像和摄像头成像分别有什么要求？', ''),
])
def test_sdk_image_scope_task_mapping_does_not_erase_camera_image_goal(
        question, prior):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': {
                'task': 'image_requirements', 'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'),
       prior_user_context=prior)
    assert schema.goals[0].condition_map()['task'] == 'image_requirements'
    assert 'task' in schema.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('question', [
    'libobkit.so 的相机图像质量有什么要求？',
    'libobkit README 对图像分辨率有什么要求？',
    'libobkit README 对照片清晰度有什么要求？',
    'libobkit README 对固件镜像和照片清晰度分别有什么要求？',
    'libobkit README 对固件镜像和摄像头成像分别有什么要求？',
])
def test_sdk_scope_task_cannot_certify_camera_image_quality_question(question):
    from daq_fae.domain_tools import DaqToolBox

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{
            'id': 'wrong_image_scope', 'capability': 'sdk_evidence',
            'entities': [], 'conditions': {'sdk': 'libobkit', 'task': 'sdk_scope'},
        }],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'not_found'


def test_mixed_firmware_image_and_camera_imaging_keep_two_evidence_goals():
    from daq_fae.task_context import prepare_turn

    question = 'libobkit README 对固件镜像和摄像头成像分别有什么要求？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 2
    imaging = next(goal for goal in schema.goals
                   if goal.condition_map().get('feature') == 'image_requirements')
    assert imaging.unmapped_condition_keys == ('feature',)
    requirements = prepare_turn(question, request_schema=schema).requirements
    assert next(req for req in requirements if req['goal_id'] == imaging.id)[
        'reason'] == 'unmapped_condition_unverified'


def test_followup_readme_mixed_image_question_keeps_imaging_goal():
    question = 'README 对固件镜像和摄像头成像分别有什么要求？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit'},
        }],
    }, prior_user_context='libobkit.so 用于 RK3576 设备端。',
       sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 2
    assert any(goal.condition_map().get('feature') == 'image_requirements'
               and goal.unmapped_condition_keys == ('feature',)
               for goal in schema.goals)


def test_mixed_sdk_imaging_goal_is_not_assigned_to_wrong_sdk():
    question = ('libobkit README 对固件镜像有什么要求？'
                'OrbbecSDK 的相机图像质量有什么要求？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 1


def test_named_second_imaging_subject_is_not_assigned_to_libobkit():
    question = 'libobkit README 对固件镜像和 OpenCV 摄像头成像分别有什么要求？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 1


@pytest.mark.parametrize('question', [
    'libobkit README 对固件镜像有什么要求，摄像头成像有什么要求？',
    'libobkit README 对固件镜像的要求和摄像头成像要求分别是什么？',
    'libobkit README 对 RK3576 固件镜像和摄像头成像分别有什么要求？',
    'libobkit README 对 EGO Pro 固件镜像和摄像头成像分别有什么要求？',
])
def test_mixed_libobkit_requirements_with_natural_connectors(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 2
    assert any(goal.condition_map().get('feature') == 'image_requirements'
               and goal.unmapped_condition_keys == ('feature',)
               for goal in schema.goals)


def test_mixed_imaging_goal_is_not_duplicated_for_task_variant():
    question = 'libobkit README 对固件镜像和摄像头成像分别有什么要求？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit', 'task': 'image_requirements'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 1
    assert schema.goals[0].unmapped_condition_keys == ('task',)


def test_other_sdk_imaging_goal_does_not_erase_libobkit_imaging_goal():
    question = ('libobkit README 对固件镜像和摄像头成像分别有什么要求？'
                'OrbbecSDK 的相机图像质量呢？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'libobkit'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'feature': 'image_requirements'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-scope-011'))
    assert len(schema.goals) == 3
    assert sum(goal.condition_map().get('sdk') == 'libobkit'
               and goal.condition_map().get('feature') == 'image_requirements'
               for goal in schema.goals) == 1


def test_sdk_uid_callback_task_is_not_mistaken_for_an_api_symbol():
    question = ('用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，'
                '怎样核对网络设备移除回调中的 UID？不要推断我的设备和固件已经兼容。')
    catalog = _reviewed_sdk_row('daq-sdk-host-net-uid-hotplug-016')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {
                'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                'sdk_release_date': '2026-09-29', 'platform': 'linux_x86_64',
                'language': 'c++', 'api': 'network_device_removal_callback',
                'task': 'verify_uid_in_network_device_removal_callback',
            },
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert 'api' not in goal.condition_map()
    assert goal.condition_map()['task'] == 'verify_uid_in_network_device_removal_callback'
    assert goal.unmapped_condition_keys == ()
    from daq_fae.domain_tools import DaqToolBox
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': goal.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']


def test_sdk_schema_keeps_real_operational_steps_and_explicit_standalone():
    question = ('Android Wrapper 单机独立运行时，先开机再开始录制的步骤是什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'standalone': True, 'steps': ['开机', '开始录制']},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-android-record-state-019'))
    assert schema.goals[0].condition_map() == {
        'standalone': True, 'steps': ['开机', '开始录制']}


@pytest.mark.parametrize('standalone', [True, False])
def test_identity_goal_ignores_unasked_standalone_model_default(standalone):
    question = ('泛称 EGO 是否支持 1600×1300@30？'
                '能否因此说它只支持 1600×1200？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO'], 'conditions': {'standalone': standalone}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution': '1600x1300'}},
        ],
    })
    assert schema.goals[0].condition_map() == {}


def test_identity_goal_keeps_explicit_standalone_as_separate_context():
    with pytest.raises(SchemaExtractionError, match='schema_identity_context_condition'):
        DaqRequestSchema.from_candidate('EGO 单机独立采集时是什么产品？', {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['EGO'], 'conditions': {'standalone': True},
            }],
        })


def test_repeated_software_goal_does_not_fail_entire_request():
    question = ('EGO Pro 接 Windows 11 时，哪一版 EgoViewer、SDK 和固件能保证四路录制？'
                '请发可转给客户的官方下载链接。')
    software = {'kind': 'product', 'capability': 'check_software_support',
                'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            software, dict(software),
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}},
            {'kind': 'product', 'capability': 'official_links',
             'entities': ['EGO Pro'], 'conditions': {}},
        ],
    })
    assert [goal.capability for goal in schema.goals] == [
        'check_software_support', 'sdk_evidence', 'official_links']
    assert len({goal.id for goal in schema.goals}) == 3


def test_sdk_source_file_is_not_misclassified_as_export_output():
    question = '2026-09-29 OrbbecSDK 包里还有 magnetometer.cpp 示例吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
                           'export_files': 'magnetometer.cpp'},
        }],
    })
    assert schema.goals[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
        'source_files': ['magnetometer.cpp']}


@pytest.mark.parametrize('question', [
    'OrbbecSDK 2026-09-29 构建的包做了什么改动？',
    'OrbbecSDK 2026-09-29 包更新了什么？',
])
def test_dated_sdk_package_change_question_keeps_release_note_goal(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'search_knowledge',
            'entities': [], 'conditions': {
                'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-build-010'))
    assert schema.goals[0].capability == 'search_knowledge'
    assert schema.goals[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29'}


def test_dated_sdk_package_contents_search_goal_uses_sdk_evidence():
    question = 'OrbbecSDK 2026-09-29 包里有哪些构建示例？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'search_knowledge',
            'entities': [], 'conditions': {
                'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-build-010'))
    assert schema.goals[0].capability == 'sdk_evidence'


def test_sdk_package_file_goal_is_separate_from_device_support_goal():
    question = ('2026-09-29 SDK 包里还带有 magnetometer.cpp，'
                '就能确定 UMI Finger 磁力计仍可用吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['UMI Finger'],
             'conditions': {'sdk_release_date': '2026-09-29',
                            'source_files': ['magnetometer.cpp']}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['UMI Finger'],
             'conditions': {'feature': 'magnetometer'}},
        ],
    })
    sdk = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    support = next(goal for goal in schema.goals if goal.capability ==
                   'check_software_support')
    assert sdk.entities == ()
    assert support.entities == ('UMI Finger',)


def test_viewer_build_date_alias_keeps_dated_note_goal_mapped():
    question = ('Android EgoViewer 都显示 v1.0.11，2026-09-23 与 2026-09-29 '
                '的网络设备枚举行为能当作相同吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EgoViewer'],
             'conditions': {'viewer_version': 'v1.0.11', 'platform': 'android',
                            'build_date': date}}
            for date in ('2026-09-23', '2026-09-29')
        ],
    })
    assert [goal.condition_map()['viewer_release_date'] for goal in schema.goals] == [
        '2026-09-23', '2026-09-29']
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_viewer_build_alias_keeps_followup_note_goal_mapped():
    schema = DaqRequestSchema.from_candidate(
        '换到同版本 build 2026092901，它的变更日志记了什么？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'search_knowledge',
                'entities': [], 'conditions': {
                    'viewer_version': '1.0.11', 'platform': 'android',
                    'build': '2026092901'},
            }],
        }, prior_user_context=(
            'Android EgoViewer v1.0.11 build 2026092302 对网络设备枚举做了什么？'))
    assert schema.goals[0].condition_map()['viewer_release_date'] == '2026092901'
    assert not schema.goals[0].unmapped_condition_keys


def test_viewer_log_model_sdk_aliases_keep_exact_viewer_build_goals():
    question = ('Android EgoViewer 都显示 v1.0.11，2026-09-23 与 2026-09-29 '
                '的网络设备枚举行为能当作相同吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EgoViewer'],
             'conditions': {'feature': 'network_device_enumeration',
                            'platform': 'android', 'sdk': 'egoviewer',
                            'sdk_release_date': date, 'sdk_version': '1.0.11'}}
            for date in ('2026-09-23', '2026-09-29')
        ],
    })
    assert [goal.condition_map() for goal in schema.goals] == [
        {'feature': 'network_device_enumeration', 'platform': 'android',
         'viewer_release_date': date, 'viewer_version': '1.0.11'}
        for date in ('2026-09-23', '2026-09-29')]
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_firmware_log_master_slave_aspect_is_a_grounded_note_feature():
    question = ('双目 EGO 固件变更日志在 v1.1.9 与 v1.1.10 分别记载了哪些'
                '主从/同步修改？这些历史说明能证明与 OrbbecSDK 2.9.0 已配对实测吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EGO'],
             'conditions': {'aspect': 'master_slave_sync',
                            'firmware_version': version}}
            for version in ('v1.1.9', 'v1.1.10')
        ]})
    assert [goal.condition_map() for goal in schema.goals] == [
        {'feature': 'master_slave_sync', 'firmware_version': version}
        for version in ('v1.1.9', 'v1.1.10')]
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_android_wrapper_alias_and_slash_separated_apis_are_grounded():
    question = ('Android Java Wrapper 的 exportFirmwareLogs 与 Linux C++ OrbbecSDK '
                'startFirmwareLogExport/stopFirmwareLogExport 如何调用？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'orbbecsdk_java_wrapper',
                            'api': 'exportFirmwareLogs'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'api': 'startFirmwareLogExport/stopFirmwareLogExport'}},
        ]})
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)
    assert schema.goals[1].condition_map()['api'] == [
        'startFirmwareLogExport', 'stopFirmwareLogExport']


def test_identity_goal_rejects_task_context_as_an_evidence_condition():
    question = 'UMI Finger 做采集时这是什么产品？'
    with pytest.raises(SchemaExtractionError, match='schema_identity_context_condition'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger'],
                'conditions': {'usage_context': 'data_collection'},
            }],
        })


def test_identity_selector_stays_on_identity_but_software_context_does_not():
    question = 'EGO 双目 1600×1200 版在 Windows 11 上能用 EgoViewer 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO 双目', 'EgoViewer'],
             'conditions': {'platform': 'Windows 11',
                            'resolution_variant': '1600x1200'}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1600x1200'}
    assert schema.goals[1].condition_map()['platform'] == 'Windows 11'


def test_normalization_collision_cannot_erase_distinct_software_fields():
    question = 'EGO Pro 在 Windows 11 的 EgoViewer、SDK 版本能保证录制吗？'
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {**shared, 'field': 'viewer_version'},
                {**shared, 'field': 'sdk_version'},
            ],
        })


def test_sdk_status_question_discards_spurious_non_spec_field():
    question = ('Android Wrapper 中 SAVING 和 RECORD_SAVED 有何区别？'
                'setEgoStateCallback 与 setEgoRecordingResultCallback 各适合什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'field': 'SAVING / RECORD_SAVED',
            'conditions': {'sdk': 'Android Wrapper', 'platform': 'android',
                           'api': 'setEgoStateCallback'},
        }],
    })
    assert len(schema.goals) == 1
    assert schema.goals[0].field is None
    assert schema.goals[0].capability == 'sdk_evidence'
    assert schema.goals[0].condition_map()['api'] == 'setEgoStateCallback'


def test_normalization_collision_cannot_erase_unknown_goal_detail():
    question = 'EGO Pro 在 Windows 11 的 EgoViewer、SDK 版本能保证录制吗？'
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {**shared, 'software': 'EgoViewer'},
                {**shared, 'software': 'SDK'},
            ],
        })


@pytest.mark.parametrize('broad_first', [True, False])
def test_normalization_collision_cannot_erase_unmapped_variant_condition(broad_first):
    question = 'EGO 双目 1600×1200 版的水平视角是多少？'
    shared = {'kind': 'product', 'capability': 'lookup_spec',
              'entities': ['EGO 双目'], 'field': 'horizontal_fov'}
    broad = {**shared, 'conditions': {'resolution_variant':
                                     ['1600x1200', '3200x2400']}}
    exact = {**shared, 'conditions': {'resolution_variant': '1600x1200'}}
    with pytest.raises(SchemaExtractionError, match='normalization_collision'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high',
            'goals': [broad, exact] if broad_first else [exact, broad],
        })


def test_field12_variant_phrase_is_a_condition_not_a_second_product():
    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目', '1920x1200 版本'],
            'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1920x1200'},
        }],
    }, prior_user_context='我现在用 EGO 双目 1920×1200 版本。')
    assert schema.goals[0].entities == ('EGO 双目',)
    assert schema.goals[0].condition_map()['resolution_variant'] == '1920x1200'


def test_followup_goals_share_one_explicit_variant_without_sharing_measurement_mode():
    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    prior = '我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': field, 'conditions': {}}
            for field in ('horizontal_fov', 'vertical_fov')
        ] + [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': field,
             'conditions': {'resolution_variant': '1920x1200'}}
            for field in ('rgb_resolution', 'rgb_max_frame_rate')
        ],
    }, prior_user_context=prior)
    assert len(schema.goals) == 5
    assert {goal.field for goal in schema.goals} == {
        'horizontal_fov', 'vertical_fov', 'rgb_resolution',
        'rgb_max_frame_rate', 'rgb_native_resolution'}
    assert all(goal.condition_map() == {'resolution_variant': '1920x1200'}
               for goal in schema.goals)


def test_followup_scoped_fov_goals_match_reviewed_evidence():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？'
    prior = '我现在用 EGO 1920×1200 版本，RGB 按原生分辨率采。'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': field, 'conditions': {}}
            for field in ('horizontal_fov', 'vertical_fov')
        ] + [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': field,
             'conditions': {'resolution_variant': '1920x1200'}}
            for field in ('rgb_resolution', 'rgb_max_frame_rate')
        ],
    }, prior_user_context=prior)
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    plan = prepare_turn(question, request_schema=schema)
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=plan.requirements)
    session = DaqEvidencePolicy().begin({'requirements': plan.requirements})
    for field in ('horizontal_fov', 'vertical_fov', 'rgb_resolution',
                  'rgb_max_frame_rate'):
        result = box.dispatch('lookup_spec', {
            'entity': 'EGO', 'field': field,
            'conditions': {'resolution_variant': '1920x1200'},
        })
        assert result.status == 'ok'
        session.observe('lookup_spec', result)
    status = session.snapshot().requirement_status
    assert {need['field']: status[need['id']] for need in plan.requirements} == {
        'horizontal_fov': 'satisfied', 'vertical_fov': 'satisfied',
        'rgb_resolution': 'satisfied', 'rgb_max_frame_rate': 'satisfied',
        'rgb_native_resolution': 'unknown'}


def test_peer_variant_is_not_copied_to_other_product_or_comparison():
    question = ('EGO 双目 1600×1200 和 1920×1200 两版的视角，'
                '以及 UMI Finger 的双目 Mono 分辨率是多少？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {'resolution_variant': '1920x1200'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'vertical_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['UMI Finger'], 'field': 'stereo_mono_resolution',
                 'conditions': {}},
            ],
        })


def test_generic_ego_output_modes_do_not_become_reviewed_variant_selectors():
    question = ('泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 '
                '1600×1200？请按已核对的机型、变体、固件与构建范围回答。')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1300'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO'], 'field': 'resolution_options',
                 'conditions': {}},
            ],
        })


def test_generic_ego_output_mode_cannot_borrow_published_1200_variant():
    question = 'EGO 只支持 1600×1200 吗？1600×1300@30 能不能用？'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'medium', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1200'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO'], 'field': 'resolution_options',
                 'conditions': {}},
            ],
        })


def test_single_ego_variant_does_not_scope_unrelated_umi_goal():
    question = ('EGO 双目 1920×1200 版的水平视角和帧率是多少？'
                'UMI Finger 的双目 Mono 分辨率呢？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['UMI Finger'], 'field': 'stereo_mono_resolution',
             'conditions': {}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1920x1200'}
    assert schema.goals[2].condition_map() == {}


def test_new_variant_cannot_inherit_old_peer_selector():
    question = '如果换成 EGO 双目 3200×2400 版，它的视角和帧率怎样？'
    prior = '原来使用 EGO 双目 1920×1200 版本。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        }, prior_user_context=prior)


def test_old_variant_pronoun_does_not_borrow_new_variant_from_later_clause():
    question = ('它的水平视角是多少？如果换成 EGO 双目 1600×1200 版本，'
                'RGB 最大帧率又是多少？')
    prior = '我现在用 EGO 双目 1920×1200 版本。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_variant_scope'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                 'conditions': {}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1200'}},
            ],
        }, prior_user_context=prior)


def test_variant_list_drops_only_matching_selector_phrases_not_other_products():
    question = 'EGO 双目 1600×1200 版本与 1920×1200 版本和 WristCam 的区别？'
    with pytest.raises(SchemaExtractionError, match='multi_entity_selector'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目', '1600x1200 版本', '1920x1200 版本', 'WristCam'],
                'field': 'horizontal_fov',
                'conditions': {'resolution_variant': ['1600x1200', '1920x1200']},
            }],
        })
    unresolved = DaqRequestSchema.from_candidate('1920×1200 版本怎么样？', {
        'confidence': 'low', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['1920x1200 版本'],
            'conditions': {'resolution_variant': '1920x1200'},
        }],
    })
    assert unresolved.goals[0].entities == ('1920x1200 版本',)


def test_multi_entity_spec_goals_expand_to_independent_evidence_needs():
    from daq_fae.task_context import prepare_turn

    question = ('UMI 视觉模块和 UMI Finger 的双目 Mono 分辨率一样吗？'
                '视觉模块的基线也能按 Finger 的 50 mm 填吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['UMI 视觉模块', 'UMI Finger'],
             'field': field, 'conditions': {}}
            for field in ('stereo_mono_resolution', 'stereo_mono_baseline')
        ],
    })
    assert len(schema.goals) == 4
    assert {(goal.entities, goal.field) for goal in schema.goals} == {
        ((entity,), field)
        for entity in ('UMI 视觉模块', 'UMI Finger')
        for field in ('stereo_mono_resolution', 'stereo_mono_baseline')
    }
    plan = prepare_turn(question, request_schema=schema)
    assert len(plan.requirements) == 4
    assert len({req['goal_id'] for req in plan.requirements}) == 4
    assert all(len(req['entities']) == 1 for req in plan.requirements)


def test_multi_entity_spec_cannot_copy_component_scope_to_device():
    question = 'UMI 视觉模块（组件）和 UMI Finger（设备）的双目 Mono 分辨率各是多少？'
    with pytest.raises(SchemaExtractionError, match='multi_entity_conditions'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI 视觉模块', 'UMI Finger'],
                'field': 'stereo_mono_resolution',
                'conditions': {'scope': 'component'},
            }],
        })


def test_reviewed_claim_scope_uses_each_entities_verified_type():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = 'UMI 视觉模块（组件）和 UMI Finger（设备）的双目 Mono 分辨率各是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': [entity], 'field': 'stereo_mono_resolution',
             'conditions': {'scope': scope}}
            for entity, scope in [('UMI 视觉模块', 'component'),
                                  ('UMI Finger', 'device')]
        ],
    })
    requirements = prepare_turn(question, request_schema=schema).requirements
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=requirements)
    session = DaqEvidencePolicy().begin({'requirements': requirements})
    for entity, scope, claim in [
            ('UMI 视觉模块', 'component', 'claim:umi-vision-module-mono-resolution'),
            ('UMI Finger', 'device', 'claim:umi-finger-mono-resolution')]:
        result = box.dispatch('lookup_spec', {'entity': entity,
                                               'field': 'stereo_mono_resolution',
                                               'conditions': {'scope': scope}})
        assert result.status == 'ok'
        assert [row['id'] for row in result.content['matches']] == [claim]
        session.observe('lookup_spec', result)
    assert set(session.snapshot().requirement_status.values()) == {'satisfied'}
    wrong = box.dispatch('lookup_spec', {'entity': 'UMI Finger',
                                          'field': 'stereo_mono_resolution',
                                          'conditions': {'scope': 'component'}})
    assert wrong.status == 'not_found'
    hidden = DaqToolBox(question=question, knowledge=view, role='channel',
                        requirements=requirements).dispatch('lookup_spec', {
                            'entity': 'UMI Finger',
                            'field': 'stereo_mono_resolution',
                            'conditions': {'scope': 'device'},
                        })
    assert hidden.status == 'not_found'


def test_simultaneous_hub_power_needs_its_own_reviewed_condition():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn

    question = '数采 HUB 有几个下游端口？三个口同时用时总供电输出多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
             'conditions': {'simultaneous_port_count': 3}},
        ],
    })
    requirements = prepare_turn(question, request_schema=schema).requirements
    power = next(req for req in requirements if req['field'] ==
                 'total_downstream_port_power_output')
    assert power['conditions'] == {'simultaneous_port_count': 3}
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=requirements)
    session = DaqEvidencePolicy().begin({'requirements': requirements})
    single = box.dispatch('lookup_spec', {'entity': '数采 HUB',
                                           'field': 'downstream_port_power_output',
                                           'conditions': {'per_port': True}})
    assert single.status == 'ok'
    session.observe('lookup_spec', single)
    assert session.snapshot().requirement_status[power['id']] != 'satisfied'
    total = box.dispatch('lookup_spec', {'entity': '数采 HUB',
                                          'field': 'total_downstream_port_power_output',
                                          'conditions': {'simultaneous_port_count': 3}})
    assert total.status == 'not_found'
    session.observe('lookup_spec', total)
    assert session.snapshot().requirement_status[power['id']] == 'missing'


@pytest.mark.parametrize('model_conditions', [
    {}, {'port_count': 3}, {'simultaneous_port_count': 3},
])
def test_explicit_three_port_total_normalizes_single_power_goal(model_conditions):
    question = '数采 HUB 有几个下游端口？三个口同时用时总供电输出多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
             'conditions': model_conditions},
        ],
    })
    power = next(goal for goal in schema.goals if goal.field and
                 'power_output' in goal.field)
    assert power.field == 'total_downstream_port_power_output'
    assert power.condition_map() == {'simultaneous_port_count': 3}


@pytest.mark.parametrize('model_conditions', [
    {'port_count': 2}, {'simultaneous_port_count': 2}, {'per_port': True},
])
def test_explicit_three_port_total_rejects_wrong_or_single_port_scope(model_conditions):
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 三个口同时用时总供电输出多少？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'],
                    'field': 'downstream_port_power_output',
                    'conditions': model_conditions,
                }],
            })


def test_total_power_and_single_port_power_need_distinct_goals():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 每口供电多少？三个口同时总供电输出多少？', {
                'confidence': 'high', 'goals': [
                    {'kind': 'product', 'capability': 'lookup_spec',
                     'entities': ['数采 HUB'],
                     'field': 'downstream_port_power_output'},
                    {'kind': 'product', 'capability': 'lookup_spec',
                     'entities': ['数采 HUB'],
                     'field': 'downstream_port_power_output',
                     'conditions': {'per_port': True}},
                ],
            })


def test_aggregate_total_cannot_swallow_explicit_per_port_power_goal():
    question = '数采 HUB 每口供电多少？三个口同时总供电输出多少？'
    for only_goal in [
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'], 'field': 'downstream_port_power_output'},
        {'kind': 'product', 'capability': 'lookup_spec',
         'entities': ['数采 HUB'],
         'field': 'total_downstream_port_power_output',
         'conditions': {'simultaneous_port_count': 3}},
    ]:
        with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
            DaqRequestSchema.from_candidate(question, {
                'confidence': 'high', 'goals': [only_goal],
            })


def test_port_count_question_does_not_create_aggregate_power_goal():
    schema = DaqRequestSchema.from_candidate(
        '一台 EGO、两台 WristCam、数采 HUB 四个下游口够用吗？每口供电多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'downstream_port_power_output',
                'conditions': {'per_port': True},
            }],
        })
    assert schema.goals[0].field == 'downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'per_port': True}


def test_combo_power_feasibility_does_not_turn_device_count_into_loaded_ports():
    question = ('一台 EGO Pro 加两台 WristCam 接数采 HUB，4 个下游口看着够，'
                '能保证接线、供电和同步录制吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'inspect_topology',
             'entities': ['EGO Pro', 'WristCam', '数采 HUB'],
             'conditions': {'topology_aspects': ['connection', 'power'],
                            'device_counts': {'EGO Pro': 1, 'WristCam': 2}}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'],
             'field': 'total_downstream_port_power_output',
             'conditions': {'simultaneous_port_count': 3}},
        ],
    })
    total = next(goal for goal in schema.goals
                 if goal.field == 'total_downstream_port_power_output')
    assert total.condition_map() == {}


def test_viewer_log_statement_goal_stays_separate_from_combo_support_goal():
    question = ('桌面 EgoViewer v2.0.12 的日志提到 EGO 加两腕带的首尾和分片对齐；'
                '这能当 EGO Pro + 两台 WristCam + HUB 在 Windows 11 上三路同步落盘的验收依据吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EGO', 'WristCam'],
             'conditions': {'viewer_version': '2.0.12', 'platform': 'desktop',
                            'feature': 'start_end_and_segment_alignment'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO Pro', 'WristCam', 'HUB'],
             'conditions': {'viewer_version': '2.0.12', 'platform': 'windows_11',
                            'task': 'three_stream_synchronized_recording'}},
        ],
    })
    log_goal, support_goal = schema.goals
    assert log_goal.entities == ()
    assert log_goal.condition_map() == {'viewer_version': '2.0.12'}
    assert log_goal.unmapped_condition_keys == ()
    assert support_goal.entities == ('EGO Pro', 'WristCam', 'HUB')


def test_firmware_log_topics_do_not_hide_two_dated_source_goals():
    question = ('双目 EGO 固件 v1.1.9 和 v1.1.10 分别记载哪些主从/时间戳变化？'
                '仅凭日志能确认我手上的固件与 OrbbecSDK 2.9.0 配对实测吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['双目 EGO'],
             'conditions': {'firmware_version': version,
                            'feature': ['master_slave_sync', 'timestamp']}}
            for version in ('v1.1.9', 'v1.1.10')
        ] + [
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['双目 EGO'],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0'}},
        ],
    })
    assert [goal.condition_map() for goal in schema.goals[:2]] == [
        {'firmware_version': 'v1.1.9'},
        {'firmware_version': 'v1.1.10'},
    ]
    assert all(goal.unmapped_condition_keys == () for goal in schema.goals[:2])


@pytest.mark.parametrize('conditions', [
    {'simultaneous_port_count': 3},
    {'per_port': True, 'simultaneous_port_count': 3},
])
def test_field05_single_port_power_rejects_aggregate_context(conditions):
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power'):
        DaqRequestSchema.from_candidate(
            '一台 EGO、两台 WristCam 和数采 HUB，四个下游口够用吗、每口供电多少？'
            '能否直接三路同步采集？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'],
                    'field': 'downstream_port_power_output',
                    'conditions': conditions,
                }],
            })


def test_explicit_per_port_power_fills_omitted_model_condition():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 每口供电多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
            'conditions': {},
        }]})
    assert schema.goals[0].field == 'downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'per_port': True}


@pytest.mark.parametrize('prior_user_context', ['', '我用数采 HUB 的三个下游口。'])
def test_other_product_per_port_question_does_not_require_hub_goal(prior_user_context):
    schema = DaqRequestSchema.from_candidate(
        'UMI Finger 每口供电是多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['UMI Finger'], 'field': 'power_output',
        }]}, prior_user_context=prior_user_context)
    assert schema.goals[0].field == 'power_output'


def test_unknown_other_product_per_port_question_does_not_inherit_hub_goal():
    schema = DaqRequestSchema.from_candidate(
        'UMI Finger 每口供电是多少？', {'confidence': 'low', 'goals': [{
            'kind': 'unknown', 'capability': 'clarify',
        }]}, prior_user_context='我正在使用数采 HUB。')
    assert schema.goals[0].capability == 'clarify'


def test_aggregate_power_cannot_be_omitted_behind_port_count():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 有几个下游口？三个口同时用时总供电输出多少？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['数采 HUB'], 'field': 'downstream_port_count',
                }],
            })


def test_aggregate_power_short_followup_uses_explicit_current_port_count():
    schema = DaqRequestSchema.from_candidate(
        '这三个口同时用时总供电多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        }, prior_user_context='我在用数采 HUB。')
    assert schema.goals[0].field == 'total_downstream_port_power_output'
    assert schema.goals[0].condition_map() == {'simultaneous_port_count': 3}


def test_aggregate_power_followup_carries_explicit_count_across_comma():
    schema = DaqRequestSchema.from_candidate(
        '如果同时用其中 3 个口，能把单口 5V/2A 乘 3 当总供电吗？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        }, prior_user_context='数采 HUB 有几个下游端口？每个口额定输出是多少？')
    assert schema.goals[0].condition_map() == {'simultaneous_port_count': 3}


def test_full_recording_sequence_from_power_on_to_stop_includes_start_step():
    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 不接电脑，用本机 TF 卡录制，开机到停止的按键和灯态是什么？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'],
                'conditions': {'host_connection': 'none',
                               'recording_mode': 'local_tf_card',
                               'indicators': ['led_state'],
                               'steps': ['power_on', 'start_recording',
                                         'stop_recording']},
            }],
        })
    assert not schema.goals[0].unmapped_condition_keys


def test_generic_led_status_label_maps_to_user_requested_lamp_state():
    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 不接电脑，插 TF 卡单机录制，开机、开始、停止各怎么操作？每步要看什么灯态和语音？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'],
                'conditions': {'host_connection': 'none', 'standalone': True,
                               'recording_mode': 'local_tf_card',
                               'steps': ['power_on', 'start_recording',
                                         'stop_recording'],
                               'indicators': ['led_status', 'voice_prompt']},
            }],
        })
    assert schema.goals[0].condition_map()['indicators'] == [
        'led_state', 'voice_prompt']
    assert schema.goals[0].unmapped_condition_keys == ()


def test_generic_led_label_maps_to_user_requested_lamp_state():
    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 不接电脑，用本机 TF 卡录制，开机到停止的按键和灯态是什么？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'],
                'conditions': {'host_connection': 'none',
                               'recording_mode': 'local_tf_card',
                               'steps': ['power_on', 'start_recording',
                                         'stop_recording'],
                               'indicators': ['led']},
            }],
        })
    assert schema.goals[0].condition_map()['indicators'] == ['led_state']
    assert schema.goals[0].unmapped_condition_keys == ()


def test_attachment_proof_question_does_not_treat_completed_state_as_procedure_step():
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'lookup_procedure',
        'entities': ['EGO Pro'],
        'conditions': {'steps': ['stop_recording', 'file_write_complete']},
    }]}
    question = ('看这张现场图片，它能证明 EGO Pro 录制已经停止、'
                'TF 卡文件完整落盘吗？')
    goal = next(goal for goal in DaqRequestSchema.from_candidate(
        question, candidate, attachment_present=True).goals
        if goal.capability == 'lookup_procedure')
    assert goal.condition_map()['steps'] == ['stop_recording']
    assert goal.unmapped_condition_keys == ()

    procedural_question = 'EGO Pro 如何确认 TF 卡文件完整落盘？'
    unresolved = DaqRequestSchema.from_candidate(
        procedural_question, candidate).goals[0]
    assert 'steps' in unresolved.unmapped_condition_keys


def test_procedure_drops_inferred_local_tf_mode_when_user_only_asks_integrity():
    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 停止录制时语音没响、绿灯常亮；能凭截图认定 TF 卡数据完整吗？', {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'],
                'conditions': {'recording_mode': 'local_tf_card',
                               'steps': ['stop_recording'],
                               'indicators': ['voice_prompt', 'green_led']},
            }],
        })
    goal = schema.goals[0]
    assert 'recording_mode' not in goal.condition_map()
    assert 'recording_mode' not in goal.unmapped_condition_keys


def test_two_explicit_aggregate_counts_require_two_separate_goals():
    question = '数采 HUB 三口与四口同时使用时总供电分别多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': count},
        } for count in (3, 4)],
    })
    assert {goal.condition_map()['simultaneous_port_count'] for goal in schema.goals} == {
        3, 4,
    }
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })


def test_other_product_total_power_does_not_create_hub_total_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 有几个下游口？UMI Finger 的总供电输出是多少？', {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['UMI Finger'], 'field': 'power_output'},
            ],
        })
    assert [goal.field for goal in schema.goals] == [
        'downstream_port_count', 'power_output',
    ]


def test_aggregate_power_goal_must_target_hub_not_another_product():
    with pytest.raises(SchemaExtractionError, match='schema_aggregate_power_goal_missing'):
        DaqRequestSchema.from_candidate(
            '数采 HUB 三个口同时总供电多少？UMI Finger 是什么产品？', {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['UMI Finger'],
                    'field': 'total_downstream_port_power_output',
                    'conditions': {'simultaneous_port_count': 3},
                }],
            })


def test_other_product_total_power_in_same_sentence_does_not_create_hub_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 三口接 UMI Finger，UMI Finger 的总功率是多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }],
        })
    assert schema.goals[0].field == 'power_output'


def test_aggregate_power_short_followup_inherits_unique_user_port_count():
    schema = DaqRequestSchema.from_candidate(
        '那总供电输出多少？', {'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['数采 HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': 3},
        }]}, prior_user_context='我用数采 HUB 的三个下游口。')
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_english_three_ports_requires_three_port_total_goal():
    question = ('For the DAQ HUB, what is the total power output '
                'when three ports are used simultaneously?')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['DAQ HUB'],
            'field': 'total_downstream_port_power_output',
            'conditions': {'simultaneous_port_count': 3},
        }],
    })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_hub_capacity_four_is_not_another_simultaneous_total_goal():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 四个下游口中三个口同时用时总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


def test_comma_followup_total_inherits_current_clause_simultaneous_count():
    schema = DaqRequestSchema.from_candidate(
        '数采 HUB 四口中三个口同时使用，总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['数采 HUB'],
                'field': 'total_downstream_port_power_output',
                'conditions': {'simultaneous_port_count': 3},
            }],
        })
    assert schema.goals[0].condition_map()['simultaneous_port_count'] == 3


@pytest.mark.parametrize('question', [
    '数采 HUB 有几个下游端口？每个端口的已发布供电输出是多少？这能直接当成整机总供电吗？',
    '数采 HUB 多少个下游口、单口输出多少？能把单口额定值乘端口数当整机总供电吗？',
])
def test_qualitative_total_question_keeps_port_facts_without_invented_simultaneous_count(question):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_count'},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'downstream_port_power_output',
             'conditions': {'per_port': True}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['数采 HUB'], 'field': 'total_downstream_port_power_output',
             'conditions': {'simultaneous_port_count': 4}},
        ],
    })
    by_field = {goal.field: goal for goal in schema.goals}
    assert by_field['downstream_port_power_output'].condition_map() == {'per_port': True}
    assert by_field['total_downstream_port_power_output'].condition_map() == {}


def test_other_product_qualitative_power_does_not_license_hub_total_goal():
    question = 'HUB 有几个下游口？EGO Pro 的供电够吗？'
    with pytest.raises(SchemaExtractionError,
                       match='schema_aggregate_power_unasked'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['HUB'], 'field': 'total_downstream_port_power_output'},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO Pro'], 'field': 'power_input'},
            ],
        })


def test_native_resolution_hard_condition_is_separate_from_resolution_option():
    question = ('客户要求 EGO 双目 1920×1200 原生 RGB 分辨率是硬条件，'
                '60 fps 只是偏好；能推荐 1920×1200 版吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1200'}},
            {'kind': 'product', 'capability': 'selection',
             'entities': ['EGO 双目'], 'conditions': {}},
        ],
    })
    fields = {goal.field for goal in schema.goals if goal.capability == 'lookup_spec'}
    assert {'rgb_resolution', 'rgb_native_resolution', 'rgb_max_frame_rate'} <= fields


def test_explicit_soft_fps_preference_remains_a_spec_goal_when_model_omits_it():
    question = ('客户要求 RGB 原生 1920×1200 是硬条件，60 fps 为可放宽偏好；'
                '能否直接推荐 EGO 双目 1920 版为满足硬条件？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_native_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    fps = [goal for goal in schema.goals if goal.field == 'rgb_max_frame_rate']
    assert len(fps) == 1
    assert fps[0].condition_map()['resolution_variant'] == '1920x1200'


def test_separate_single_and_combination_procedure_goals_are_not_collapsed():
    question = ('EGO Pro 单机 TF 卡录制的开机和停止步骤是什么？'
                '如果再加两台 WristCam 经 HUB 连接电脑，这套步骤能保证三路同步吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'inspect_topology',
             'entities': ['EGO Pro', 'WristCam', 'HUB'],
             'conditions': {'topology_aspects': ['sync']}},
            {'kind': 'product', 'capability': 'lookup_procedure',
             'entities': ['EGO Pro', 'WristCam', 'HUB'],
             'conditions': {'steps': ['power_on', 'stop_recording']}},
        ],
    })
    procedures = [goal for goal in schema.goals
                  if goal.capability == 'lookup_procedure']
    assert any(goal.entities == ('EGO Pro',)
               and goal.condition_map().get('standalone') is True
               for goal in procedures)
    assert any(set(goal.entities) == {'EGO Pro', 'WristCam', 'HUB'}
               for goal in procedures)


def test_unasked_camera_configuration_cannot_break_non_spec_operating_goal():
    question = 'EGO Pro 单机录制如果再加两台 WristCam 和 HUB，三路能同步吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'inspect_topology',
             'entities': ['EGO Pro', 'WristCam', 'HUB'],
             'conditions': {'camera_config': 'stereo'}},
        ],
    })
    assert schema.goals[0].capability == 'inspect_topology'
    assert 'camera_config' not in schema.goals[0].condition_map()


def test_unasked_camera_configuration_cannot_break_rgb_count_and_fps_goals():
    question = ('EGO Pro 有 4 颗 RGB，相应规格写 60 fps；'
                '我能承诺四路 RGB 同时各 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO Pro'], 'field': field,
             'conditions': {'camera_config': 'stereo'}}
            for field in ('rgb_camera_count', 'rgb_max_frame_rate')
        ],
    })
    assert {goal.field for goal in schema.goals} == {
        'rgb_camera_count', 'rgb_max_frame_rate',
        'rgb_simultaneous_max_frame_rate'}
    assert all('camera_config' not in goal.condition_map()
               for goal in schema.goals)


def test_simultaneous_rgb_fps_requires_independent_concurrency_evidence():
    question = ('EGO Pro 有 4 颗 RGB，相应规格写 60 fps；'
                '我能承诺四路 RGB 同时各 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO Pro'], 'field': 'rgb_max_frame_rate',
            'conditions': {},
        }],
    })
    assert {goal.field for goal in schema.goals} == {
        'rgb_camera_count', 'rgb_max_frame_rate',
        'rgb_simultaneous_max_frame_rate'}
    assert all(goal.critical for goal in schema.goals)


def test_later_viewer_log_does_not_satisfy_restoration_status_goal():
    question = ('Android EgoViewer v1.0.11 的 2026092302 构建临时屏蔽网络设备枚举；'
                '2026092901 日志没写恢复，能断言后一个构建已恢复吗？')
    goals = [{
        'kind': 'product', 'capability': 'search_knowledge',
        'entities': [], 'conditions': {
            'platform': 'android', 'viewer_version': '1.0.11',
            'viewer_release_date': build,
            'feature': 'network_device_enumeration',
        },
    } for build in ('2026092302', '2026092901')]
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': goals,
    })
    status = [goal for goal in schema.goals
              if goal.capability == 'check_software_support']
    assert len(status) == 1
    assert status[0].condition_map() == {
        'platform': 'android', 'viewer_version': '1.0.11',
        'viewer_release_date': '2026092901',
        'feature': 'network_device_enumeration'}


def test_device_capture_lifecycle_descriptor_binds_only_checked_device_c_row():
    question = ('libobkit C 示例要同时取相机、IMU、音频，初始化、配置、'
                '启停与退出顺序是什么？这能证明任意 EGO 双目固件都支持吗？')
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
        'conditions': {'sdk': 'libobkit', 'language': 'c',
                       'task': 'simultaneous_camera_imu_audio_capture'},
    }]}
    checked = DaqRequestSchema.from_candidate(
        question, candidate,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-device-capture-013'))
    assert checked.goals[0].condition_map()['task'] == 'capture'
    assert checked.goals[0].unmapped_condition_keys == ()
    other = DaqRequestSchema.from_candidate(
        question, candidate,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-device-build-012'))
    assert other.goals[0].condition_map()['task'] == \
        'simultaneous_camera_imu_audio_capture'
    assert 'task' in other.goals[0].unmapped_condition_keys


def test_device_capture_plus_any_firmware_preserves_reviewed_sdk_scope_goal():
    question = ('libobkit C 示例要同时取相机、IMU、音频，初始化、配置、'
                '启停与退出顺序是什么？这能证明任意 EGO 双目固件都支持吗？')
    rows = [_reviewed_sdk_row(kb_id).records[0] for kb_id in (
        'daq-sdk-device-capture-013', 'daq-sdk-device-scope-011')]
    catalog = SdkCatalog(rows, {'records_sha256': 'fixture-records'})
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit', 'language': 'c',
                           'task': 'simultaneous_camera_imu_audio_capture'},
        }],
    }, sdk_catalog=catalog)
    sdk_goals = [goal for goal in schema.goals
                 if goal.capability == 'sdk_evidence']
    assert {'capture', 'sdk_scope'} <= {
        goal.condition_map().get('task') for goal in sdk_goals}
    assert all(goal.unmapped_condition_keys == () for goal in sdk_goals)
    assert any(goal.capability == 'check_software_support'
               for goal in schema.goals)

    from daq_fae.task_context import prepare_turn
    catalog.manifest['records_sha256'] = '0' * 64

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=prepare_turn(question, request_schema=schema).requirements,
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert {'daq-sdk-device-capture-013', 'daq-sdk-device-scope-011'} <= {
        source['source_id'] for source in result.sources}


def test_device_compatibility_followup_preserves_reviewed_image_binding_without_capture_goal():
    question = '所以可以把 libobkit.so 拷到任意 EGO Pro 固件上用吗？'
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'check_software_support',
            'entities': ['EGO Pro'], 'conditions': {'sdk': 'libobkit'},
        }],
    }, sdk_catalog=catalog)
    assert any(goal.capability == 'check_software_support'
               and goal.entities == ('EGO Pro',) for goal in schema.goals)
    assert any(goal.capability == 'sdk_evidence'
               and goal.condition_map() == {'sdk': 'libobkit', 'task': 'sdk_scope'}
               and goal.unmapped_condition_keys == () for goal in schema.goals)


def test_device_compatibility_does_not_borrow_other_builds_image_binding():
    question = 'libobkit 20261001 可以拷到任意 EGO Pro 固件上用吗？'
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'check_software_support',
            'entities': ['EGO Pro'],
            'conditions': {'sdk': 'libobkit', 'sdk_release_date': '2026-10-01'},
        }],
    }, sdk_catalog=catalog)
    assert not any(goal.capability == 'sdk_evidence'
                   and goal.condition_map().get('task') == 'sdk_scope'
                   for goal in schema.goals)


def test_device_scope_is_not_borrowed_when_a_new_capture_package_lacks_its_readme():
    import copy
    question = ('libobkit C 示例同时采集相机、IMU、音频，'
                '这能证明任意 EGO 双目固件都支持吗？')
    old_capture = _reviewed_sdk_row('daq-sdk-device-capture-013').records[0]
    old_scope = _reviewed_sdk_row('daq-sdk-device-scope-011').records[0]
    new_capture = copy.deepcopy(old_capture)
    new_capture['package'] = 'device_c_new_build_without_reviewed_scope'
    new_capture['kb_id'] = 'fixture-new-device-capture'
    catalog = SdkCatalog([old_capture, old_scope, new_capture],
                         {'records_sha256': 'fixture-records'})
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit', 'task': 'capture'},
        }],
    }, sdk_catalog=catalog)
    assert not any(goal.capability == 'sdk_evidence'
                   and goal.condition_map().get('task') == 'sdk_scope'
                   for goal in schema.goals)


@pytest.mark.parametrize('question', [
    'libobkit C 示例同时取相机、IMU、音频，初始化和退出顺序是什么？',
    'libobkit C 示例里的相机图像质量能代表任意 EGO 双目固件吗？',
    'OrbbecSDK C 示例能证明任意 EGO 双目固件都支持吗？',
])
def test_device_sdk_scope_not_added_for_capture_only_imaging_or_other_sdk(question):
    catalog = _reviewed_sdk_row('daq-sdk-device-scope-011')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'libobkit' if 'libobkit' in question else 'OrbbecSDK'},
        }],
    }, sdk_catalog=catalog)
    assert not any(goal.capability == 'sdk_evidence'
                   and goal.condition_map().get('task') == 'sdk_scope'
                   for goal in schema.goals)


def test_calibration_export_descriptor_binds_only_checked_host_example():
    question = ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，'
                '是 SDK 已经解析好了内容，还是按原始字节导出？需要检查什么？')
    candidate = {'confidence': 'medium', 'goals': [{
        'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
        'conditions': {'language': 'cpp',
                       'task': 'export_alignment_and_imu_calibration_yaml'},
    }]}
    checked = DaqRequestSchema.from_candidate(
        question, candidate,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-host-calib-002'))
    assert checked.goals[0].condition_map()['task'] == 'calibration_export'
    assert checked.goals[0].unmapped_condition_keys == ()
    other = DaqRequestSchema.from_candidate(
        question, candidate,
        sdk_catalog=_reviewed_sdk_row('daq-sdk-device-capture-013'))
    assert 'task' in other.goals[0].unmapped_condition_keys


def test_red_led_unknown_does_not_erase_checked_stop_recording_steps():
    question = ('客户说 EGO Pro 录制时看到了红灯。能把红灯解释成文件保存完成吗？'
                '已审单机流程里实际有哪些可核对的完成检查点？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_procedure',
             'entities': ['EGO Pro'],
             'conditions': {'indicators': ['red_led'],
                            'task': 'interpret_indicator_file_save_complete'}},
            {'kind': 'product', 'capability': 'lookup_procedure',
             'entities': ['EGO Pro'],
             'conditions': {'standalone': True,
                            'task': 'recording_completion_checkpoints'}},
        ],
    })
    unknown, known = schema.goals
    assert unknown.condition_map()['indicators'] == ['red_led']
    assert 'task' in unknown.unmapped_condition_keys
    assert known.condition_map() == {
        'standalone': True, 'steps': ['stop_recording']}
    assert known.unmapped_condition_keys == ()


def test_explicit_short_ego_variant_names_are_identity_selectors():
    question = ('Quad EGO 是已审的 EGO 双目 1600 版或 1920 版吗？'
                '如果不是已核实同一产品，先别给我它的视场角。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['Quad EGO'], 'conditions': {}},
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    }, knowledge=_short_variant_knowledge())
    assert [goal.unmapped_condition_keys for goal in schema.goals] == [(), (), ()]


def _short_variant_knowledge(*, variants=('1600x1200', '1920x1200')):
    class Knowledge:
        def records_for(self, role):
            assert role == 'internal_fae'
            return [{'kind': 'entity', 'scope': {
                        'product': 'ego', 'resolution_variant': variant,
                        'required_selectors': ['resolution_variant']},
                     'data': {'name': 'EGO', 'aliases': ['EGO 双目']}}
                    for variant in variants]
    return Knowledge()


def test_short_variant_label_requires_unique_current_reviewed_identity():
    question = 'EGO 双目 1600 版的视场角是多少？'

    def variant_goal(value, knowledge=None, asked=question):
        return DaqRequestSchema.from_candidate(asked, {
            'confidence': 'medium', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目'], 'field': 'horizontal_fov',
                'conditions': {'resolution_variant': value},
            }],
        }, knowledge=knowledge).goals[0]

    assert variant_goal('1600x1200', _short_variant_knowledge()).unmapped_condition_keys == ()
    assert 'resolution_variant' in variant_goal('1600x1200').unmapped_condition_keys
    ambiguous = _short_variant_knowledge(variants=(
        '1600x1200', '1600x1300', '1920x1200'))
    assert 'resolution_variant' in variant_goal('1600x1200', ambiguous).unmapped_condition_keys
    updated = _short_variant_knowledge(variants=('1600x1300', '1920x1200'))
    assert variant_goal('1600x1300', updated).unmapped_condition_keys == ()
    other_product = 'EGO Pro 1600 版的视场角是多少？'
    with pytest.raises(SchemaExtractionError, match='entity_not_grounded'):
        variant_goal('1600x1200', _short_variant_knowledge(), other_product)


def test_model_width_variant_key_binds_only_unique_visible_ego_sku():
    question = ('客户要求 RGB 原生 1920×1200 是硬条件，60 fps 为可放宽偏好；'
                '能否直接推荐 EGO 双目 1920 版为满足硬条件？')
    candidate = {'confidence': 'high', 'goals': [
        {'kind': 'product', 'capability': capability,
         'entities': ['EGO 双目'],
         **({'field': field} if field else {}),
         'conditions': {'variant': '1920',
                        **({'resolution': '1920x1200'}
                           if field == 'rgb_max_frame_rate' else {})}}
        for capability, field in (
            ('resolve_entity', None), ('lookup_spec', 'rgb_resolution'),
            ('lookup_spec', 'rgb_native_resolution'),
            ('lookup_spec', 'rgb_max_frame_rate'), ('selection', None))]}
    schema = DaqRequestSchema.from_candidate(
        question, candidate, knowledge=_short_variant_knowledge())
    assert all(goal.condition_map().get('resolution_variant') == '1920x1200'
               and 'variant' not in goal.condition_map()
               and not goal.unmapped_condition_keys for goal in schema.goals)
    fps = next(goal for goal in schema.goals
               if goal.field == 'rgb_max_frame_rate')
    assert fps.condition_map()['resolution'] == '1920x1200'

    no_catalog = DaqRequestSchema.from_candidate(question, candidate)
    assert all('resolution_variant' not in goal.condition_map()
               for goal in no_catalog.goals)
    ambiguous = DaqRequestSchema.from_candidate(
        question, candidate, knowledge=_short_variant_knowledge(
            variants=('1600x1200', '1920x1200', '1920x1080')))
    assert all('resolution_variant' not in goal.condition_map()
               for goal in ambiguous.goals)


def test_host_side_sdk_goal_keeps_cross_platform_firmware_log_evidence():
    question = ('Android Java Wrapper 的 exportFirmwareLogs 和主机侧 C++ 的 '
                'startFirmwareLogExport 都叫什么、怎么结束？'
                '这能证明我的 EGO 双目固件一定支持导出吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'api': 'exportFirmwareLogs', 'language': 'java',
                            'platform': 'android'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'api': 'startFirmwareLogExport', 'language': 'cpp',
                            'platform': 'host'}},
        ],
    })
    host = next(goal for goal in schema.goals
                if goal.condition_map().get('api') == 'startFirmwareLogExport')
    assert host.condition_map()['platform'] == 'host_side'
    assert not host.unmapped_condition_keys

    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.sdk_catalog import SdkCatalog
    from daq_fae.task_context import prepare_turn
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = SdkCatalog(rows, {'records_sha256': 'test'})
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': 'startFirmwareLogExport C++ '
                                                      '固件日志导出'})
    assert result.status == 'ok'
    assert 'daq-sdk-host-fwlog-003' in {
        source['source_id'] for source in result.sources}


def test_source_named_sdk_example_and_concept_are_not_lost_as_unknown_api():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('OrbbecSDK 的 EGO Depth 网络示例切换预设后，'
                '我还能沿用切换前查到的 StreamProfile 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO Depth'],
            'conditions': {'sdk': 'OrbbecSDK', 'api': 'StreamProfile'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map().get('task') == 'preset_profile'
    assert 'api' not in goal.condition_map()
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert 'daq-sdk-host-depth-009' in {
        source['source_id'] for source in result.sources}


@pytest.mark.parametrize('extra', [
    {'sample_type': 'network', 'feature': 'preset_switch'},
    {'connection_mode': 'network', 'feature': 'preset_switch'},
])
def test_model_descriptors_for_network_preset_example_bind_source_task(extra):
    from daq_fae.knowledge.sdk_catalog import SdkCatalog
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('OrbbecSDK 的 EGO Depth 网络示例切换预设后，'
                '我还能沿用切换前查到的 StreamProfile 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO Depth'], 'conditions': {
                'sdk': 'OrbbecSDK', 'api': 'StreamProfile', **extra},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map() == {
        'sdk': 'OrbbecSDK', 'connection': 'network',
        'task': 'preset_profile'}
    assert not goal.unmapped_condition_keys
    from daq_fae.task_context import prepare_turn
    from daq_fae.domain_tools import DaqToolBox
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': question})
    assert 'daq-sdk-host-depth-009' in {
        source['source_id'] for source in result.sources}


@pytest.mark.parametrize('model_task', [
    'reuse_stream_profile_after_preset_switch',
    'switch_preset_reuse_stream_profile',
])
def test_question_shaped_sdk_task_maps_only_through_reviewed_example(model_task):
    from daq_fae.knowledge.sdk_catalog import SdkCatalog
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('OrbbecSDK 的 EGO Depth 网络示例切换预设后，'
                '我还能沿用切换前查到的 StreamProfile 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO Depth'], 'conditions': {
                'sdk': 'OrbbecSDK', 'connection': 'network',
                'task': model_task},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map()['task'] == 'preset_profile'
    assert not goal.unmapped_condition_keys


def test_descriptive_calibration_outputs_bind_reviewed_export_task():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('EGO C++ 示例导出对齐和 IMU 标定 YAML 时，'
                '是 SDK 已经解析好了内容，还是按原始字节导出？需要检查什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO'], 'conditions': {
                'language': 'cpp',
                'export_files': ['alignment_calibration_yaml',
                                 'imu_calibration_yaml'],
            },
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map().get('task') == 'calibration_export'
    assert 'export_files' not in goal.condition_map()
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': question})
    assert 'daq-sdk-host-calib-002' in {
        source['source_id'] for source in result.sources}


def test_ble_install_and_demo_questions_keep_each_platforms_source_scope():
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('EgoLowBle 1.1.8 Windows x64 和 Linux x64 包分别怎么链接与放置？'
                'Linux 包里一定有预编译 demo 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'platform': platform, 'sdk': 'EgoLowBle',
                           'sdk_version': '1.1.8', 'task': task},
        } for platform, task in (
            ('windows_x64', 'link_and_deploy'),
            ('linux_x64', 'link_and_deploy'),
            ('linux_x64', 'prebuilt_demo_presence'),
        )],
    }, sdk_catalog=catalog)
    by_platform = [(g.condition_map()['platform'], g.condition_map()['task'])
                   for g in schema.goals]
    assert ('windows_x64', ['link_library', 'deploy_dll']) in by_platform
    assert ('linux_x64', 'inspect_package') in by_platform
    assert ('linux_x64', 'link_and_deploy') in by_platform
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': question})
    assert {'daq-sdk-ble-windows-install-020',
            'daq-sdk-ble-linux-install-021'} <= {
                source['source_id'] for source in result.sources}


def test_ble_link_and_place_alias_uses_reviewed_platform_records():
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('EgoLowBle 1.1.8 Windows x64 和 Linux x64 包分别怎么链接与放置？'
                'Linux 包里一定有预编译 demo 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'platform': platform, 'sdk': 'EgoLowBle',
                           'sdk_version': '1.1.8', 'task': 'link_and_place'},
        } for platform in ('windows_x64', 'linux_x64')],
    }, sdk_catalog=catalog)
    goals = {goal.condition_map()['platform']: goal for goal in schema.goals}
    assert goals['windows_x64'].condition_map()['task'] == [
        'link_library', 'deploy_dll']
    assert goals['linux_x64'].condition_map()['task'] == 'link_and_deploy'
    assert all(not goal.unmapped_condition_keys for goal in goals.values())


@pytest.mark.parametrize('selector', ['sample_type', 'feature'])
def test_model_prebuilt_demo_descriptor_maps_to_reviewed_linux_package(selector):
    from daq_fae.knowledge.sdk_catalog import SdkCatalog
    rows = [json.loads(line) for line in
            (Path(__file__).resolve().parents[1] /
             'Knowledge_SDK/SDK_Knowledge.jsonl').read_text().splitlines()]
    catalog = SdkCatalog(rows, {'records_sha256': 'test'})
    question = ('EgoLowBle 1.1.8 Windows x64 和 Linux x64 包分别怎么链接与放置？'
                'Linux 包里一定有预编译 demo 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                           'platform': 'linux_x64', selector: 'prebuilt_demo'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map() == {
        'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
        'platform': 'linux_x64', 'task': 'inspect_package'}
    assert not goal.unmapped_condition_keys
    from daq_fae.task_context import prepare_turn
    from daq_fae.domain_tools import DaqToolBox
    class Knowledge:
        release_id = 'dev-test'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'sdk_evidence', {'query': question})
    assert 'daq-sdk-ble-linux-install-021' in {
        source['source_id'] for source in result.sources}


def test_explicit_new_product_total_overrides_prior_hub_topic():
    schema = DaqRequestSchema.from_candidate(
        '那 UMI Finger 的总供电输出多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['UMI Finger'], 'field': 'power_output',
            }],
        }, prior_user_context='我用数采 HUB 的三个下游口。')
    assert schema.goals[0].field == 'power_output'


@pytest.mark.parametrize('explicit_location', ['scope', 'conditions'])
def test_explicit_claim_scope_overrides_entity_device_type(explicit_location):
    from daq_fae.domain_tools import DaqToolBox
    row = {'kind': 'claim', 'scope': {'product': 'umi-finger'},
           'data': {'entity_id': 'entity:umi-finger', 'conditions': {}}}
    (row['scope'] if explicit_location == 'scope' else
     row['data']['conditions'])['scope'] = 'component'
    entities = {'entity:umi-finger': {'kind': 'entity', 'data': {
        'name': 'UMI Finger', 'entity_type': 'device'}}}
    assert not DaqToolBox._condition_supported(row, 'scope', 'device', entities)
    assert DaqToolBox._condition_supported(row, 'scope', 'component', entities)


def test_multi_entity_identity_goals_expand_without_sharing_status():
    question = 'EGO Pro 和 WristCam 分别是什么产品？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'resolve_entity',
            'entities': ['EGO Pro', 'WristCam'], 'conditions': {},
        }],
    })
    assert len(schema.goals) == 2
    assert {goal.entities for goal in schema.goals} == {
        ('EGO Pro',), ('WristCam',),
    }


@pytest.mark.parametrize('question,candidate,field,conditions', [
    ('UMI Finger 的鱼眼 RGB 分辨率是多少？',
     {'field': 'resolution', 'conditions': {'sensor': 'fisheye_rgb'}},
     'rgb_resolution', {'lens_type': 'fisheye'}),
    ('UMI Finger 的双目 Mono 基线是多少？',
     {'field': 'baseline', 'conditions': {'camera_type': 'mono', 'stereo_pair': True}},
     'stereo_mono_baseline', {}),
    ('UMI 视觉模块的鱼眼 RGB 分辨率是多少？',
     {'field': 'fisheye_rgb_resolution',
      'conditions': {'scope': 'module', 'camera_type': 'fisheye_rgb'}},
     'rgb_resolution', {'lens_type': 'fisheye', 'scope': 'module'}),
    ('EGO Pro 的 RGB 最大帧率是多少？',
     {'field': 'rgb_max_frame_rate', 'conditions': {'sensor': 'rgb'}},
     'rgb_max_frame_rate', {}),
    ('UMI Finger 的双目 Mono 基线是多少？',
     {'field': 'stereo_baseline',
      'conditions': {'camera_type': 'mono', 'camera_config': 'stereo'}},
     'stereo_mono_baseline', {}),
])
def test_schema_normalizes_explicit_camera_qualifiers(question, candidate, field, conditions):
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['UMI Finger' if 'Finger' in question
                                                   else 'UMI 视觉模块' if '视觉模块' in question
                                                   else 'EGO Pro'], **candidate}],
    })
    assert schema.goals[0].field == field
    assert schema.goals[0].condition_map() == conditions


def test_schema_rejects_camera_qualifier_absent_from_user_words():
    with pytest.raises(SchemaExtractionError):
        DaqRequestSchema.from_candidate('UMI Finger 的 RGB 分辨率是多少？', {
            'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                          'entities': ['UMI Finger'], 'field': 'resolution',
                                          'conditions': {'sensor': 'fisheye_rgb'}}],
        })


def test_schema_discards_unrequested_device_scope_but_keeps_explicit_system_scope():
    hub = DaqRequestSchema.from_candidate('数采 HUB 的下游端口有几个？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['数采 HUB'], 'field': 'downstream_port_count',
                                      'conditions': {'scope': 'device'}}],
    })
    assert hub.goals[0].condition_map() == {}
    system = DaqRequestSchema.from_candidate('EG-DB 整机深度精度是多少？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['EG-DB'], 'field': 'depth_accuracy',
                                      'conditions': {'scope': 'whole_system'}}],
    })
    assert system.goals[0].condition_map() == {'scope': 'whole_system'}


def test_field_encoded_downstream_port_condition_is_not_a_second_requirement():
    schema = DaqRequestSchema.from_candidate('数采 HUB 的下游端口供电输出规格是多少？', {
        'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'entities': ['数采 HUB'],
                                      'field': 'downstream_port_power_output',
                                      'conditions': {'port': 'downstream', 'scope': 'device'}}],
    })
    assert schema.goals[0].condition_map() == {}


def test_unmapped_topology_and_software_conditions_are_explicit_gaps():
    from daq_fae.task_context import prepare_turn
    cases = [
        ('一台 EGO 和两台 WristCam 通过 HUB 连接 PC 后，如何开流、采集并检查保存结果？',
         {'kind': 'product', 'capability': 'inspect_topology',
          'entities': ['EGO', 'WristCam', 'HUB', 'PC'],
          'conditions': {'ego_count': 1, 'wristcam_count': 2,
                         'connection': 'hub_to_pc'}}),
        ('EGO Pro 在 Windows 11 上适用哪个 EgoViewer 版本？',
         {'kind': 'product', 'capability': 'check_software_support',
          'entities': ['EGO Pro', 'EgoViewer'], 'field': 'software_version',
          'conditions': {'os': 'Windows 11', 'software': 'EgoViewer'}}),
    ]
    for question, goal in cases:
        schema = DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [goal],
        })
        assert schema.goals[0].unmapped_condition_keys
        plan = prepare_turn(question, request_schema=schema)
        assert plan.requirements[0]['reason'] == 'unmapped_condition_unverified'


def test_field02_fps_target_is_not_a_claim_measurement_condition():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1920×1200 原生 RGB 能跑 60 fps 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
            'conditions': {'resolution_variant': '1920x1200',
                           'rgb_resolution': '1920x1200',
                           'rgb_max_frame_rate': 60},
        }],
    })
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {'resolution_variant': '1920x1200'}
    assert requirement.get('reason') != 'unmapped_condition_unverified'
    assert requirement['question_constraints'] == {
        'rgb_resolution': '1920x1200', 'rgb_max_frame_rate': 60,
    }


def test_spec_goal_keeps_user_resolution_when_model_encodes_it_as_step():
    from daq_fae.task_context import prepare_turn
    question = '泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 1600×1200？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
            'conditions': {'steps': ['resolution=1600x1300']},
        }],
    })
    goal = next(goal for goal in schema.goals
                if goal.field == 'rgb_max_frame_rate')
    assert goal.condition_map() == {'resolution': '1600x1300'}
    requirement = next(requirement for requirement in
                       prepare_turn(question, request_schema=schema).requirements
                       if requirement['conditions'].get('resolution') == '1600x1300')
    assert requirement['conditions']['resolution'] == '1600x1300'
    assert 'steps' not in requirement.get('question_constraints', {})


def test_spec_goal_drops_unasked_standalone_false_while_recovering_mode():
    question = '泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 1600×1200？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
            'conditions': {'standalone': False,
                           'steps': ['resolution=1600x1300']},
        }],
    })
    assert schema.goals[0].condition_map() == {'resolution': '1600x1300'}


def test_procedure_drops_model_inferred_no_host_from_independent_recording():
    question = ('一台 EGO Pro 四目用本机 TF 卡独立录制：从断电插卡、开机到开始和停止录制，'
                '应按什么顺序操作？各步的语音和绿灯状态如何核对？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro 四目'],
            'conditions': {'standalone': True, 'host_connection': 'none',
                           'recording_mode': 'local_tf_card',
                           'steps': ['insert_card_power_off', 'power_on',
                                     'start_recording', 'stop_recording'],
                           'indicators': ['voice_prompt', 'green_led']},
        }],
    })
    goal = schema.goals[0]
    assert 'host_connection' not in goal.condition_map()
    assert 'host_connection' not in goal.unmapped_condition_keys


def test_spec_goal_rejects_other_mentioned_resolution_as_mode_target():
    question = '泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 1600×1200？'
    with pytest.raises(SchemaExtractionError, match='schema_spec_resolution_target_conflict'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
                'conditions': {'steps': ['resolution=1600x1200']},
            }],
        })


def test_at_fps_mode_is_required_even_without_the_word_fps():
    question = '泛称 EGO 是否支持 1600×1300@30？能否因此说它只支持 1600×1200？'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
                'conditions': {},
            }],
        })


def test_field01_capture_task_does_not_filter_static_fov_claim():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1600×1200 版本做手部动作采集，水平视场角是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': '1600x1200'},
        }],
    })
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {'resolution_variant': '1600x1200'}


def test_field01_comparison_expands_each_variant_to_an_independent_spec_goal():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1600×1200 和 1920×1200 两个版本的水平视角怎么比？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO 双目'], 'field': 'horizontal_fov',
            'conditions': {'resolution_variant': ['1600x1200', '1920x1200']},
        }],
    })
    assert len(schema.goals) == 2
    assert [goal.condition_map()['resolution_variant'] for goal in schema.goals] == [
        '1600x1200', '1920x1200']
    assert [item['conditions']['resolution_variant'] for item in
            prepare_turn(question, request_schema=schema).requirements] == [
                '1600x1200', '1920x1200']


def test_field01_generic_identity_goal_does_not_block_reviewed_variant_comparison():
    from daq_fae.task_context import prepare_turn
    question = ('做手部动作采集，优先要宽视野和 60 fps，在 EGO 双目 '
                '1600×1200 和 1920×1200 两个版本里怎么选？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目']},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert [goal.capability for goal in schema.goals] == [
        'resolve_entity', 'resolve_entity', 'lookup_spec', 'lookup_spec']
    assert [goal.condition_map().get('resolution_variant') for goal in
            schema.goals[:2]] == ['1600x1200', '1920x1200']
    assert [item['conditions'].get('resolution_variant') for item in
            prepare_turn(question, request_schema=schema).requirements[:2]] == [
                '1600x1200', '1920x1200']


def test_field13_rgb_mode_question_cannot_become_stereo_mono_resolution():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'stereo_mono_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
        ],
    })
    assert schema.goals[0].field == 'rgb_resolution'


def test_field13_output_mode_cannot_become_product_variant():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    with pytest.raises(SchemaExtractionError, match='resolution_variant_not_user_variant'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_resolution',
                 'conditions': {'resolution_variant': '1920x1080'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1080'}},
            ],
        })


def test_field13_output_mode_remains_measurement_condition_on_base_variant():
    from daq_fae.task_context import prepare_turn
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
        ],
    })
    goals = [goal for goal in schema.goals if goal.field == 'rgb_max_frame_rate']
    assert len(goals) == 1
    assert goals[0].condition_map() == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }
    requirement = [item for item in prepare_turn(question, request_schema=schema).requirements
                   if item.get('field') == 'rgb_max_frame_rate'][0]
    assert requirement['conditions'] == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }


def test_field13_explicit_mode_requires_a_matching_fps_measurement_goal():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_resolution',
                 'conditions': {'resolution_variant': '1920x1200'}},
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        })


@pytest.mark.parametrize('question', [
    'EGO 双目 1920×1200 版在 1920×1080 下 RGB 最高多少帧？',
    'EGO 双目 1920×1200 版改用 1920×1080，RGB 帧率能到 60 吗？',
    'EGO 双目 1600×1200 版本和 1920×1200 版本各是多少帧？前者在 1280×720 下又是多少？',
])
def test_explicit_mode_fps_query_requires_the_mode_condition(question):
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200'}},
            ],
        })


def test_mode_change_for_recording_does_not_rewrite_native_fps_question():
    question = ('EGO 双目 1920×1200 版原生 RGB 帧率是多少？'
                '切到 1920×1080 后还能正常录像吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_procedure',
             'entities': ['EGO 双目'], 'conditions': {'resolution': '1920x1080'}},
        ],
    })
    assert schema.goals[0].condition_map() == {'resolution_variant': '1920x1200'}


def test_mode_fps_goal_must_bind_the_requested_variant_and_rgb_channel():
    question = ('EGO 双目 1600×1200 版与 1920×1200 版对比；'
                '1920×1200 版切到 1920×1080 后 RGB 最高多少帧？')
    for field, variant in [('rgb_max_frame_rate', '1600x1200'),
                           ('depth_frame_rate', '1920x1200')]:
        with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
            DaqRequestSchema.from_candidate(question, {
                'confidence': 'high', 'goals': [{
                    'kind': 'product', 'capability': 'lookup_spec',
                    'entities': ['EGO 双目'], 'field': field,
                    'conditions': {'resolution_variant': variant,
                                   'resolution': '1920x1080'},
                }],
            })


def test_two_output_modes_each_require_a_fps_goal():
    question = ('EGO 双目 1920×1200 版的两种输出模式 '
                '1920×1080 与 1280×720 各是多少帧？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec',
                'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                'conditions': {'resolution_variant': '1920x1200',
                               'resolution': '1920x1080'},
            }],
        })


@pytest.mark.parametrize('question,capability,field', [
    ('EGO 双目 1920×1200 版原生已知 30 fps，切到 1920×1080 后 SDK 能用吗？',
     'check_software_support', None),
    ('EGO 双目 1920×1200 版原生 RGB 是 30 fps，切到 1920×1080 后怎么录制？',
     'lookup_procedure', None),
    ('EGO 双目 1920×1200 版原生 30 fps，切到 1920×1080 后能否选这个模式？',
     'lookup_spec', 'rgb_resolution'),
])
def test_background_fps_does_not_force_mode_frame_rate_goal(question, capability, field):
    goal = {'kind': 'product', 'capability': capability, 'entities': ['EGO 双目'],
            'conditions': {'resolution_variant': '1920x1200'}}
    if field:
        goal['field'] = field
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [goal],
    })
    assert len(schema.goals) == 1


def test_rgb_mode_does_not_apply_to_following_mono_fps_question():
    question = ('EGO 双目 1920×1200 版在 1920×1080 下 RGB 最高多少帧，'
                'Mono 最高多少帧？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200',
                            'resolution': '1920x1080'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'mono_max_frame_rate',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert schema.goals[1].condition_map() == {'resolution_variant': '1920x1200'}


def test_return_to_native_fps_requires_a_second_mode_goal():
    question = ('EGO 双目 1920×1200 版在 1920×1080 下 RGB 多少帧，'
                '回到原生分辨率又是多少帧？')
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200',
                                'resolution': '1920x1080'}},
            ],
        })


def test_followup_mode_uses_single_explicit_prior_variant():
    question = '切到 1920×1080 后 RGB 最高多少帧？'
    prior = '我在用 EGO 双目 1920×1200 版。'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1600x1200',
                                'resolution': '1920x1080'}},
            ],
        }, prior_user_context=prior)


def test_same_mode_explicit_rgb_and_mono_each_require_fps_goal():
    question = 'EGO 双目 1920×1200 版的 RGB 和 Mono 在 1920×1080 下各是多少帧？'
    with pytest.raises(SchemaExtractionError, match='schema_missing_measurement_mode'):
        DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [
                {'kind': 'product', 'capability': 'lookup_spec',
                 'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                 'conditions': {'resolution_variant': '1920x1200',
                                'resolution': '1920x1080'}},
            ],
        })


def test_comparison_of_two_explicit_variants_is_not_a_mode_change():
    question = 'EGO 双目 1600×1200 版本和 1920×1200 版本的 RGB 帧率分别是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': ['1600x1200', '1920x1200']}},
        ],
    })
    assert {goal.condition_map()['resolution_variant'] for goal in schema.goals} == {
        '1600x1200', '1920x1200',
    }


def test_field13_model_schema_retries_with_version_and_mode_distinguished():
    question = ('EGO 双目 1920×1200 版除了原生分辨率还能选 '
                '1920×1080 吗？切到 1080 后 RGB 能到 60 fps 吗？')

    class SequenceAdapter:
        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None, required_tool=None):
            self.calls.append(messages)
            condition = ({'resolution_variant': '1920x1080'} if len(self.calls) == 1 else
                         {'resolution_variant': '1920x1200', 'resolution': '1920x1080'})
            yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
                   'arguments': {'confidence': 'high', 'goals': [
                       {'kind': 'product', 'capability': 'lookup_spec',
                        'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
                        'conditions': condition},
                   ]}}

    adapter = SequenceAdapter()
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert schema.goals[0].condition_map() == {
        'resolution_variant': '1920x1200', 'resolution': '1920x1080',
    }
    assert '产品版本' in adapter.calls[1][0]['content']


def test_field12_native_fps_followup_requires_native_rgb_resolution_evidence():
    question = '如果设备换成 1600×1200 版本，这三项要怎么改？'
    prior = ('我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。\n'
             '它的水平、垂直视场角分别是多少？原生分辨率最高多少帧？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'vertical_fov',
             'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
             'conditions': {'resolution_variant': '1600x1200'}},
        ],
    }, prior_user_context=prior)
    assert any(goal.capability == 'lookup_spec' and goal.field == 'rgb_resolution'
               and goal.condition_map() == {'resolution_variant': '1600x1200'}
               for goal in schema.goals)


def test_context_statement_can_keep_product_scope_and_a_pure_clarification_goal():
    schema = DaqRequestSchema.from_candidate('我用 EGO 双目 1600×1200 版本', {
        'confidence': 'low', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目'], 'conditions': {'resolution_variant': '1600x1200'}},
            {'kind': 'unknown', 'capability': 'clarify', 'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}, 'field': 'user_intent'},
        ],
    })
    assert schema.goals[1].kind == 'unknown'
    assert schema.goals[1].entities == ()
    assert schema.goals[1].field is None
    assert schema.goals[1].condition_map() == {}


def test_context_statement_can_keep_unmapped_device_counts_without_schema_failure():
    schema = DaqRequestSchema.from_candidate(
        '我使用一台 EGO、两台 WristCam 和 HUB 接 PC 采集。', {
            'confidence': 'low', 'goals': [
                {'kind': 'product', 'capability': 'inspect_topology',
                 'entities': ['EGO', 'WristCam', 'HUB'],
                 'conditions': {'device_counts': {'EGO': 1, 'WristCam': 2, 'HUB': 1},
                                'host': 'PC', 'task': 'capture'}},
                {'kind': 'unknown', 'capability': 'clarify',
                 'entities': ['EGO', 'WristCam', 'HUB'], 'field': 'user_intent'},
            ],
        })
    assert schema.goals[0].unmapped_condition_keys == ('device_counts', 'host')
    assert schema.goals[0].condition_map()['task'] == 'capture'
    assert schema.goals[1].entities == ()


def test_new_topology_constraint_shape_becomes_unsatisfied_gap_not_schema_error():
    from daq_fae.task_context import prepare_turn
    question = '一台 EGO 和两台 WristCam 通过 HUB 连接 PC 后，如何开流并检查保存结果？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{'kind': 'product',
                                       'capability': 'inspect_topology',
                                       'entities': ['EGO', 'WristCam', 'HUB'],
                                       'conditions': {'combination': {
                                           'devices': [{'name': 'EGO', 'count': 1},
                                                       {'name': 'WristCam', 'count': 2}],
                                           'hub': 'HUB'}}}],
    })
    assert schema.goals[0].unmapped_condition_keys == ('combination',)
    assert prepare_turn(question, request_schema=schema).requirements[0]['reason'] == \
        'unmapped_condition_unverified'


def test_non_spec_goal_does_not_make_an_unprovable_claim_field():
    from daq_fae.task_context import prepare_turn
    question = 'EGO 双目 1920×1200 版本的 RGB 可选分辨率有哪些？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目 1920×1200 版本'], 'field': 'product_variant',
             'conditions': {'resolution_variant': '1920x1200'}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目 1920×1200 版本'], 'field': 'rgb_resolution',
             'conditions': {'resolution_variant': '1920x1200'}},
        ],
    })
    assert schema.goals[0].field is None
    plan = prepare_turn(question, request_schema=schema,
                        grounded_targets={'entities': ['entity:ego-1920'],
                                          'conditions': {'resolution_variant': '1920x1200'}})
    assert 'field' not in plan.requirements[0]


class CandidateAdapter:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = []

    def chat(self, messages, tools=None, required_tool=None):
        self.calls.append((messages, tools, required_tool))
        yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
               'arguments': self.candidate}
        yield {'type': 'stop', 'stop_reason': 'tool_use'}


def test_schema_retries_once_when_model_invents_an_entity():
    class SequenceAdapter:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            entity = 'invented-model' if self.calls == 1 else 'EGO Pro'
            yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
                   'arguments': {'confidence': 'medium', 'goals': [{
                       'kind': 'product', 'capability': 'resolve_entity',
                       'entities': [entity],
                   }]}}

    adapter = SequenceAdapter()
    schema = extract_request_schema('EGO Pro 的规格是多少？', adapter=adapter)
    assert adapter.calls == 2
    assert schema.goals[0].entities == ('EGO Pro',)


@pytest.mark.parametrize('question,candidate,kind', [
    ('介绍一下你自己', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}, 'service'),
    ('Who are you?', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}, 'service'),
    ('EGO 双目水平视场角是多少？', {'confidence': 'high', 'goals': [
        {'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
         'field': 'horizontal_fov'}]}, 'product'),
    ('你是谁？EGO 的视场角是多少？', {'confidence': 'high', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'},
        {'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EGO'],
         'field': 'horizontal_fov'}]}, 'mixed'),
])
def test_model_candidate_produces_validated_composable_goals(question, candidate, kind):
    adapter = CandidateAdapter(candidate)
    schema = extract_request_schema(question, adapter=adapter)
    assert schema.request_kind == kind
    assert schema.original_question == question
    assert schema.effective_question == question
    assert len({goal.id for goal in schema.goals}) == len(schema.goals)
    assert adapter.calls[0][2] == 'submit_daq_request_schema'


def test_unknown_candidate_stays_unknown_without_product_search():
    schema = DaqRequestSchema.from_candidate('不太确定想问什么', {
        'confidence': 'low', 'goals': [{'kind': 'unknown', 'capability': 'clarify'}],
    })
    assert schema.request_kind == 'unknown'
    assert schema.goals[0].capability == 'clarify'


def test_whole_system_scope_is_preserved_as_a_stricter_product_qualifier():
    schema = DaqRequestSchema.from_candidate('EG-DB 整机深度精度是多少？', {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EG-DB'],
            'field': 'depth_accuracy', 'conditions': {'scope': 'whole_system'},
        }],
    })
    assert schema.goals[0].condition_map()['scope'] == 'whole_system'


def test_english_question_requests_english_presentation():
    schema = DaqRequestSchema.from_candidate('Who are you?', {
        'confidence': 'high', 'goals': [{
            'kind': 'service', 'capability': 'service_identity',
        }],
    })
    assert schema.answer_language == 'en'


def test_chinese_sdk_question_with_english_api_names_stays_chinese():
    candidate = {'confidence': 'high', 'answer_language': 'en', 'goals': [{
        'kind': 'product', 'capability': 'sdk_evidence',
    }]}
    question = ('Android Wrapper 中 SAVING 和 RECORD_SAVED 有何区别？'
                'setEgoStateCallback 与 setEgoRecordingResultCallback 各适合什么？')
    assert DaqRequestSchema.from_candidate(question, candidate).answer_language == 'zh'
    assert DaqRequestSchema.from_candidate(
        '请用英文回答：SAVING 和 RECORD_SAVED 有何区别？', candidate,
    ).answer_language == 'en'
    assert DaqRequestSchema.from_candidate(
        '请用英文回答：SAVING 和 RECORD_SAVED 有何区别？', candidate,
        selected_answer_language='zh',
    ).answer_language == 'zh'


def test_selected_answer_language_overrides_model_and_question_language():
    candidate = {'confidence': 'high', 'answer_language': 'zh', 'goals': [
        {'kind': 'service', 'capability': 'service_identity'}]}
    assert DaqRequestSchema.from_candidate(
        '介绍一下你自己', candidate, selected_answer_language='en',
    ).answer_language == 'en'
    assert DaqRequestSchema.from_candidate(
        'Who are you?', {**candidate, 'answer_language': 'en'},
        selected_answer_language='zh',
    ).answer_language == 'zh'


def test_model_cannot_invent_entity_or_override_original_question():
    with pytest.raises(SchemaExtractionError, match='entity_not_grounded'):
        DaqRequestSchema.from_candidate('这个参数是多少？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_spec', 'entities': ['EG-DB'],
                'field': 'accuracy',
            }],
        })


def test_effective_question_uses_only_user_history_not_model_rewrite():
    schema = DaqRequestSchema.from_candidate('它的视场角呢？', {
        'confidence': 'medium', 'effective_question': 'EGO 的视场角是 999°吗？',
        'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                   'entities': ['EGO'], 'field': 'horizontal_fov'}],
    }, prior_user_context='我用 EGO 双目 1600×1200 版本')
    assert 'EGO 双目 1600×1200 版本' in schema.effective_question
    assert '999' not in schema.effective_question


@pytest.mark.parametrize('candidate', [
    {'confidence': 'high', 'goals': []},
    {'confidence': 'high', 'goals': [{'kind': 'service', 'capability': 'lookup_spec'}]},
    {'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'service_identity'}]},
    {'confidence': 'high', 'goals': [{'kind': 'product', 'capability': 'lookup_spec',
                                      'field': 'horizontal_fov', 'entities': ['EGO'],
                                      'conditions': {'secret': 'injected'}}]},
])
def test_invalid_schema_fails_explicitly(candidate):
    with pytest.raises(SchemaExtractionError):
        DaqRequestSchema.from_candidate('EGO 参数', candidate)


class SequencedSchemaAdapter:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def chat(self, messages, tools=None, required_tool=None):
        self.calls.append((messages, required_tool))
        yield from self.responses[len(self.calls) - 1]


def test_identity_context_condition_retries_without_losing_the_identity_goal():
    from pathlib import Path
    from daq_fae.domain_evidence import DaqEvidencePolicy
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.task_context import prepare_turn
    question = 'UMI Finger 做采集时这是什么产品？'
    def event(conditions):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'medium', 'goals': [{
                     'kind': 'product', 'capability': 'resolve_entity',
                     'entities': ['UMI Finger'], 'conditions': conditions,
                 }]}}]
    adapter = SequencedSchemaAdapter([
        event({'usage_context': 'data_collection'}), event({}),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert 'schema_identity_context_condition' not in str(schema)
    assert '采集任务' in adapter.calls[1][0][0]['content']
    assert schema.goals[0].condition_map() == {}
    requirement = prepare_turn(question, request_schema=schema).requirements[0]
    assert requirement['conditions'] == {}
    assert requirement.get('reason') != 'unmapped_condition_unverified'
    active = read_active_release(Path(__file__).resolve().parents[1] /
                                 'data/knowledge/published')
    view = ReviewedKnowledge.from_manifest(active['release_id'], active['manifest'])
    for role, expected_status in [('internal_fae', 'satisfied'),
                                  ('channel', 'missing')]:
        box = DaqToolBox(question=question, knowledge=view, role=role,
                         requirements=[requirement])
        result = box.dispatch('resolve_entity', {'text': 'UMI Finger'})
        session = DaqEvidencePolicy().begin({'requirements': [requirement]})
        session.observe('resolve_entity', result)
        assert session.snapshot().requirement_status[requirement['id']] == expected_status


def test_unknown_identity_qualifier_cannot_be_silently_dropped():
    with pytest.raises(SchemaExtractionError, match='schema_identity_context_condition'):
        DaqRequestSchema.from_candidate('UMI Finger Rev B 是什么产品？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger'], 'conditions': {'model_variant': 'Rev B'},
            }],
        })


def test_identity_retry_preserves_separate_software_question():
    from daq_fae.task_context import prepare_turn
    question = 'UMI Finger 是什么产品？在 Windows 11 上能用 EgoViewer 采集吗？'
    def event(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    identity = {'kind': 'product', 'capability': 'resolve_entity',
                'entities': ['UMI Finger']}
    software = {'kind': 'product', 'capability': 'check_software_support',
                'entities': ['UMI Finger', 'EgoViewer'],
                'conditions': {'platform': 'Windows 11'}}
    adapter = SequencedSchemaAdapter([
        event([{**identity, 'conditions': {'platform': 'Windows 11'}}, software]),
        event([identity, software]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    requirements = prepare_turn(question, request_schema=schema).requirements
    assert len(adapter.calls) == 2
    assert {item['capability'] for item in requirements} == {
        'resolve_entity', 'check_software_support',
    }
    assert next(item for item in requirements if item['capability'] ==
                'resolve_entity')['conditions'] == {}
    assert next(item for item in requirements if item['capability'] ==
                'check_software_support')['conditions']['platform'] == 'Windows 11'


def test_schema_retry_explains_normalization_collision_for_software_components():
    question = ('EGO Pro 接 Windows 11 时，哪一版 EgoViewer、SDK 和固件能保证四路录制？'
                '请发官方下载链接。')
    def event(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    shared = {'kind': 'product', 'capability': 'check_software_support',
              'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}}
    adapter = SequencedSchemaAdapter([
        event([{**shared, 'field': 'viewer_version'},
               {**shared, 'field': 'sdk_version'}]),
        event([shared,
               {'kind': 'product', 'capability': 'sdk_evidence',
                'entities': ['EGO Pro'], 'conditions': {'platform': 'Windows 11'}},
               {'kind': 'product', 'capability': 'official_links',
                'entities': ['EGO Pro']}]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert [goal.capability for goal in schema.goals] == [
        'check_software_support', 'sdk_evidence', 'official_links']
    assert len(adapter.calls) == 2
    assert '非规格目标不要填写 field' in adapter.calls[1][0][0]['content']


def test_product_count_cannot_be_reduced_to_service_coverage():
    question = '你知道多少款数采产品'
    def event(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    adapter = SequencedSchemaAdapter([
        event([{'kind': 'service', 'capability': 'service_coverage'}]),
        event([{'kind': 'product', 'capability': 'catalog'}]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert [goal.capability for goal in schema.goals] == ['catalog']
    assert len(adapter.calls) == 2
    assert 'catalog' in adapter.calls[1][0][0]['content']


def test_catalog_ignores_unasked_default_standalone_false():
    question = '你们有哪些数采产品？请按已发布资料列出名称。'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'catalog',
             'conditions': {'standalone': False}},
            {'kind': 'service', 'capability': 'service_coverage'},
        ],
    })
    catalog = next(goal for goal in schema.goals if goal.capability == 'catalog')
    assert catalog.condition_map() == {}


def test_sdk_package_usage_ignores_unasked_default_standalone_false():
    question = ('用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，'
                '怎样核对网络设备移除回调中的 UID？不要推断设备固件兼容。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'sdk_release_date': '2026-09-29',
                           'platform': 'linux_x86_64', 'language': 'c++',
                           'feature': 'network_device_enumeration',
                           'task': 'check_uid_in_network_device_removal_callback',
                           'standalone': False},
        }],
    })
    assert schema.goals[0].condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
        'sdk_release_date': '2026-09-29', 'platform': 'linux_x86_64',
        'language': 'c++', 'feature': 'network_device_enumeration',
        'task': 'check_uid_in_network_device_removal_callback',
    }


def test_device_side_libobkit_build_goal_drops_unasked_standalone_and_maps_platform():
    question = ('四目 EGO 设备端 libobkit 示例怎么编译和部署？'
                '它能和任意整机镜像搭配吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['四目 EGO'],
             'conditions': {'platform': 'device', 'sdk': 'libobkit',
                            'standalone': True, 'task': 'compile_and_deploy'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO'], 'conditions': {'sdk': 'libobkit'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-device-build-012'))
    sdk_goal = next(goal for goal in schema.goals
                    if goal.capability == 'sdk_evidence')
    assert sdk_goal.condition_map() == {
        'platform': 'device_side', 'sdk': 'libobkit',
        'task': 'compile_and_deploy',
    }
    assert sdk_goal.unmapped_condition_keys == ()


def test_umi_note_history_encoded_selectors_are_typed_not_operating_steps():
    question = ('OrbbecSDK 2026-09-21 与 2026-09-23 两份变更日志分别如何记载 '
                'UMI Finger 磁力计？2026-09-29 包中仍有 magnetometer.cpp，'
                '能据此说后续构建已恢复或设备已实测吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['UMI Finger'], 'conditions': {'steps': [
                 'sdk=OrbbecSDK', 'sdk_release_date=2026-09-21',
                 'feature=magnetometer']}},
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['UMI Finger'], 'conditions': {'steps': [
                 'sdk=OrbbecSDK', 'sdk_release_date=2026-09-23',
                 'feature=magnetometer']}},
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['UMI Finger'], 'conditions': {'steps': [
                 'sdk=OrbbecSDK', 'sdk_release_date=2026-09-29',
                 'source_files=magnetometer.cpp']}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['UMI Finger'], 'conditions': {'steps': [
                 'sdk=OrbbecSDK', 'feature=magnetometer']}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023'))
    assert [goal.condition_map() for goal in schema.goals] == [
        {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-21',
         'feature': 'magnetometer'},
        {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-23',
         'feature': 'magnetometer'},
        {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
         'source_files': ['magnetometer.cpp']},
        {'sdk': 'OrbbecSDK', 'feature': 'magnetometer'},
    ]
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)
    assert next(goal for goal in schema.goals
                if goal.capability == 'sdk_evidence').entities == ()


def test_ble_linux_package_goals_share_reviewed_sdk_usage_scope():
    question = ('EgoLowBle 1.1.8 Linux x64 包提供什么 demo 和构建资料？'
                '预编译 demo 是否必定包含？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'linux_x64',
                            'task': 'demo_and_build_materials'}},
            {'kind': 'product', 'capability': 'search_knowledge', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'linux_x64',
                            'feature': 'prebuilt_demo_inclusion'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-ble-linux-install-021'))
    assert [goal.capability for goal in schema.goals] == [
        'sdk_evidence', 'sdk_evidence']
    assert [goal.condition_map().get('task') or goal.condition_map().get('feature')
            for goal in schema.goals] == ['inspect_package', 'inspect_package']
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_ios_package_phrases_bind_reviewed_framework_and_simulator_scope():
    question = ('2026-09-29 OrbbecSDK 2.9.0 iOS 包应如何嵌入 framework？'
                '这个包是否包含 Simulator 产物、能否据此确认 EGO 固件兼容？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'platform': 'ios', 'task': 'framework_embedding'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'platform': 'ios', 'feature': 'simulator_artifact'}},
        ],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-ios-framework-022'))
    assert [goal.condition_map().get('task') or goal.condition_map().get('feature')
            for goal in schema.goals] == ['embed_framework', 'simulator_artifacts']
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_host_sync_phrase_binds_reviewed_sdk_mode_example():
    question = ('用 2026-09-29 的 OrbbecSDK 2.9.0 C++ 示例，'
                'EGO 多设备主从同步模式应该怎样检查并设置？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'sdk_release_date': '2026-09-29', 'language': 'c++',
                           'feature': 'master_slave_sync'},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-sync-001'))
    assert schema.goals[0].condition_map()['feature'] == 'multi_device_sync_mode_check_and_set'
    assert schema.goals[0].unmapped_condition_keys == ()


def test_sdk_build_dates_are_not_sdk_versions_in_log_and_package_goals():
    question = ('OrbbecSDK 2026-09-21 与 2026-09-23 两份变更日志分别如何记载'
                ' UMI Finger 磁力计？2026-09-29 包中仍有 magnetometer.cpp，'
                '能据此说后续构建已恢复或设备已实测吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['UMI Finger'],
             'conditions': {'feature': 'magnetometer',
                            'sdk_release_date': date, 'sdk_version': date}}
            for date in ('2026-09-21', '2026-09-23')
        ] + [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk_version': '2026-09-29',
                           'source_files': ['magnetometer.cpp']},
        }],
    }, sdk_catalog=_reviewed_sdk_row('daq-sdk-host-magnetometer-sample-023'))
    assert [goal.condition_map().get('sdk_release_date') for goal in schema.goals] == [
        '2026-09-21', '2026-09-23', '2026-09-29']
    assert all('sdk_version' not in goal.condition_map() for goal in schema.goals)


def test_schema_retries_when_explicit_spec_question_was_reduced_to_identity():
    question = ('客户手上是 EGO 双目 1920×1200 版，要求 RGB 在 1920×1200 '
                '原生分辨率跑 60 fps。这个指标能确认吗？如果换 1600×1200 版呢？')
    def call(goals):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    adapter = SequencedSchemaAdapter([
        call([{'kind': 'product', 'capability': 'resolve_entity',
               'entities': ['EGO 双目']}]),
        call([{'kind': 'product', 'capability': 'lookup_spec',
               'entities': ['EGO 双目'], 'field': 'rgb_max_frame_rate',
               'conditions': {'resolution_variant': ['1920x1200', '1600x1200']}},
              {'kind': 'product', 'capability': 'lookup_spec',
               'entities': ['EGO 双目'], 'field': 'rgb_resolution',
               'conditions': {'resolution_variant': ['1920x1200', '1600x1200']}}]),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert 'schema_missing_explicit_spec_goal' in adapter.calls[1][0][0]['content']
    assert len([goal for goal in schema.goals if goal.capability == 'lookup_spec']) == 6
    from daq_fae.workflow import allowed_tools_for
    assert 'lookup_spec' in allowed_tools_for(schema)


def test_schema_rejects_repeated_identity_only_answer_to_explicit_spec_question():
    question = 'EGO Pro 的 RGB 最大帧率是多少？'
    event = [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
              'arguments': {'confidence': 'high', 'goals': [
                  {'kind': 'product', 'capability': 'resolve_entity',
                   'entities': ['EGO Pro']} ]}}]
    adapter = SequencedSchemaAdapter([event, event])
    with pytest.raises(SchemaExtractionError, match='schema_missing_explicit_spec_goal'):
        extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2


@pytest.mark.parametrize('question,goals', [
    ('我现在用 EGO 双目 1920×1200 版本，RGB 按原生分辨率采。', [
        {'kind': 'product', 'capability': 'resolve_entity',
         'entities': ['EGO 双目']}]),
    ('EGO 双目 1920×1200 版 RGB 按原生分辨率采，请按步骤说怎么接线和录制', [
        {'kind': 'product', 'capability': 'lookup_procedure',
         'entities': ['EGO 双目']}]),
    ('你能查询 EGO 双目的 RGB 分辨率吗？', [
        {'kind': 'service', 'capability': 'service_capabilities'},
        {'kind': 'product', 'capability': 'resolve_entity',
         'entities': ['EGO 双目']}]),
    ('EGO 双目 RGB 分辨率在 EgoViewer 哪一版可用？', [
        {'kind': 'product', 'capability': 'check_software_support',
         'entities': ['EGO 双目', 'EgoViewer']}]),
])
def test_schema_completeness_guard_preserves_non_value_requests(question, goals):
    response = [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': goals}}]
    adapter = SequencedSchemaAdapter([response])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 1
    assert all(goal.capability != 'lookup_spec' for goal in schema.goals)


def test_schema_retries_once_when_auto_tool_choice_omits_required_call():
    adapter = SequencedSchemaAdapter([
        [{'type': 'text_delta', 'text': 'I can help.'},
         {'type': 'stop', 'stop_reason': 'end_turn'}],
        [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
          'arguments': {'confidence': 'high', 'goals': [
              {'kind': 'product', 'capability': 'check_software_support',
               'entities': ['EGO Pro', 'EgoViewer']}]}},
         {'type': 'stop', 'stop_reason': 'tool_use'}],
    ])
    schema = extract_request_schema(
        'EGO Pro 在 Windows 11 上适用哪个 EgoViewer 版本？', adapter=adapter)
    assert schema.goals[0].capability == 'check_software_support'
    assert len(adapter.calls) == 2
    assert all(call[1] == 'submit_daq_request_schema' for call in adapter.calls)
    assert '上一次回复没有调用' in adapter.calls[1][0][0]['content']


def test_schema_retry_is_bounded_and_invalid_candidate_is_not_hidden():
    no_tool = [{'type': 'stop', 'stop_reason': 'end_turn'}]
    adapter = SequencedSchemaAdapter([no_tool, no_tool])
    with pytest.raises(SchemaExtractionError, match='model_schema_missing_or_ambiguous'):
        extract_request_schema('EgoViewer 是什么？', adapter=adapter)
    assert len(adapter.calls) == 2

    invalid_response = [{'type': 'tool_call',
        'name': 'submit_daq_request_schema',
        'arguments': {'confidence': 'high', 'goals': []}}]
    invalid = SequencedSchemaAdapter([invalid_response, invalid_response])
    with pytest.raises(SchemaExtractionError, match='schema_goals_invalid'):
        extract_request_schema('EgoViewer 是什么？', adapter=invalid)
    assert len(invalid.calls) == 2


def test_schema_retries_uncontrolled_topology_key_then_keeps_evidence_debt():
    question = 'EGO Pro 单机采集的供电拓扑是什么？'
    def event(conditions):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': [{
                     'kind': 'product', 'capability': 'inspect_topology',
                     'entities': ['EGO Pro'], 'conditions': conditions}]}}]
    adapter = SequencedSchemaAdapter([
        event({'scene_mode': 'standalone'}),
        event({'standalone': True, 'topology_aspects': ['power']}),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    assert '未受控的条件键' in adapter.calls[1][0][0]['content']
    correction = adapter.calls[1][0][0]['content'].split('上一次使用了未受控的条件键。', 1)[1]
    assert 'sdk_version' in correction
    assert schema.goals[0].unmapped_condition_keys == ()
    repeated = SequencedSchemaAdapter([
        event({'scene_mode': 'standalone'}),
        event({'scene_mode': 'standalone'}),
    ])
    unresolved = extract_request_schema(question, adapter=repeated)
    assert len(repeated.calls) == 2
    assert unresolved.goals[0].unmapped_condition_keys == ('scene_mode',)
    from daq_fae.task_context import prepare_turn
    assert prepare_turn(question, request_schema=unresolved).requirements[0]['reason'] == \
        'unmapped_condition_unverified'


def test_topology_boolean_string_normalizes_only_when_user_grounded():
    from daq_fae.request_schema import _SCHEMA_TOOL
    standalone_property = _SCHEMA_TOOL['function']['parameters']['properties'][
        'goals']['items']['properties']['conditions']['properties']['standalone']
    assert standalone_property == {'type': 'boolean'}
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'inspect_topology',
        'entities': ['EGO Pro 四目'],
        'conditions': {'host_connection': 'none', 'standalone': 'true',
                       'topology_aspects': ['power', 'connection', 'storage']},
    }]}
    question = '一台 EGO Pro 四目不连接电脑或手机时，单机独立采集的供电、连接与存储拓扑是什么？'
    grounded = DaqRequestSchema.from_candidate(question, candidate).goals[0]
    assert grounded.condition_map()['standalone'] is True
    assert grounded.unmapped_condition_keys == ()
    ungrounded = DaqRequestSchema.from_candidate(
        'EGO Pro 四目不连接电脑或手机的供电、连接与存储拓扑是什么？',
        candidate).goals[0]
    assert 'standalone' in ungrounded.unmapped_condition_keys


def test_schema_step_codes_keep_uncovered_procedures_expressible_and_retry_once():
    from daq_fae.request_schema import _SCHEMA_TOOL
    step_schema = _SCHEMA_TOOL['function']['parameters']['properties']['goals'][
        'items']['properties']['conditions']['properties']['steps']
    assert step_schema['items']['type'] == 'string'
    assert 'enum' not in step_schema['items']
    question = 'EGO Pro 单机录制时，断电插卡、开机、开始和停止录制的步骤是什么？'
    def event(steps):
        return [{'type': 'tool_call', 'name': 'submit_daq_request_schema',
                 'arguments': {'confidence': 'high', 'goals': [{
                     'kind': 'product', 'capability': 'lookup_procedure',
                     'entities': ['EGO Pro'], 'conditions': {'steps': steps},
                 }]}}]
    adapter = SequencedSchemaAdapter([
        event(['insert_card_while_on']),
        event(['insert_card_power_off', 'power_on',
               'start_recording', 'stop_recording']),
    ])
    schema = extract_request_schema(question, adapter=adapter)
    assert len(adapter.calls) == 2
    correction = adapter.calls[1][0][0]['content'].split('步骤值未通过核验。', 1)[1]
    assert 'insert_card_power_off' in correction
    assert '其他设备' in correction
    assert schema.goals[0].unmapped_condition_keys == ()
    repeated = SequencedSchemaAdapter([
        event(['insert_card_while_on']), event(['insert_card_while_on']),
    ])
    unresolved = extract_request_schema(question, adapter=repeated)
    assert len(repeated.calls) == 2
    assert unresolved.goals[0].unmapped_condition_keys == ('steps',)
    combo = DaqRequestSchema.from_candidate(
        'EGO 和 WristCam 通过 HUB 时，怎样配对并启动组开流？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO', 'WristCam', 'HUB'],
                'conditions': {'steps': ['pair_wristcam', 'start_group_stream']},
            }],
        })
    assert combo.goals[0].condition_map()['steps'] == [
        'pair_wristcam', 'start_group_stream']
    assert combo.goals[0].unmapped_condition_keys == ('steps',)


def test_prior_log_attachment_scope_does_not_swallow_product_support_goal():
    question = ('刚才日志出现 E42。现在可以把它当官方确认的 '
                'EGO Pro 固件 v9.9.9 不兼容结论发给客户吗？')
    prior = ('请读这份现场日志，指出里面实际写了什么错误及设备、固件、SDK 版本；'
             '能据此确认 EGO Pro 和该 SDK 组合不兼容吗？')
    attachment = {'kind': 'attachment', 'capability': 'search_attachments',
                  'entities': ['EGO Pro'], 'conditions': {}}
    support = {'kind': 'product', 'capability': 'check_software_support',
               'entities': ['EGO Pro'],
               'conditions': {'firmware_version': 'v9.9.9'}}
    for raw_goals in ([attachment, support], [attachment]):
        schema = DaqRequestSchema.from_candidate(
            question, {'confidence': 'medium', 'goals': raw_goals},
            prior_user_context=prior, attachment_present=True)
        assert any(goal.capability == 'search_attachments' and
                   goal.entities == () for goal in schema.goals)
        assert any(goal.capability == 'check_software_support' and
                   goal.entities == ('EGO Pro',) for goal in schema.goals)


def test_log_followup_attachment_conditions_cannot_break_support_question():
    question = ('刚才日志出现 E42。现在可以把它当官方确认的 '
                'EGO Pro 固件 v9.9.9 不兼容结论发给客户吗？')
    candidate = {'confidence': 'medium', 'goals': [{
        'kind': 'attachment', 'capability': 'search_attachments',
        'entities': ['EGO Pro'],
        'conditions': {'error_code': 'E42', 'firmware_version': 'v9.9.9'},
    }]}
    schema = DaqRequestSchema.from_candidate(
        question, candidate, attachment_present=True)
    assert any(goal.capability == 'search_attachments' and
               goal.entities == () and goal.conditions == ()
               for goal in schema.goals)
    assert any(goal.capability == 'check_software_support' and
               goal.entities == ('EGO Pro',)
               for goal in schema.goals)


def test_software_support_goal_drops_unattributed_sdk_name():
    question = 'setEgoStateCallback 能删除录制状态吗？'
    prior = ('Android Java Wrapper 的 setEgoStateCallback 中 SAVING 和 '
             'RECORD_SAVED 分别代表什么？')
    candidate = {'confidence': 'medium', 'goals': [{
        'kind': 'product', 'capability': 'check_software_support',
        'entities': [],
        'conditions': {'api': 'setEgoStateCallback', 'sdk': 'OrbbecSDK',
                       'task': 'recording_state_delete'},
    }]}
    schema = DaqRequestSchema.from_candidate(
        question, candidate, prior_user_context=prior)
    assert 'sdk' not in schema.goals[0].condition_map()
    explicit = DaqRequestSchema.from_candidate(
        'OrbbecSDK 的 setEgoStateCallback 能删除录制状态吗？',
        candidate)
    assert explicit.goals[0].condition_map()['sdk'] == 'OrbbecSDK'

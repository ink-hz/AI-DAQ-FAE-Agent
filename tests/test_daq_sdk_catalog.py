"""SDK usage is source checked and separate from software support."""

import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest
from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.knowledge.sdk_catalog import SdkCatalog, _serialized, build_catalog
from daq_fae.knowledge.sdk_release import activate_sdk_release, load_active_sdk_release, stage_sdk_release
from daq_fae.request_schema import DaqRequestSchema, extract_request_schema
from scripts.archive_candidate_snapshot import create_archive


def test_sdk_usage_statements_separate_source_facts_from_review_limits():
    rows = json.loads(Path("Knowledge_SDK/curated.json").read_text())
    by_id = {row["kb_id"]: row["answer"] for row in rows}
    assert "不能据此确认具体设备或固件支持" not in by_id["daq-sdk-android-fwlog-017"]
    assert "不能证明 UMI Finger 磁力计" not in by_id["daq-sdk-host-magnetometer-sample-023"]
    assert "不能据此确认 EGO 设备" not in by_id["daq-sdk-ios-framework-022"]


def test_mixed_sdk_audio_search_keeps_reviewed_host_example_and_unknown_device_sdk():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 和 libobkit 的音频播放分别怎么做？'
    assert 'daq-sdk-host-audio-007' in {
        row['kb_id'] for row in catalog.search(question, 'internal_fae', include_all=True)}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': sdk}}
            for sdk in ('OrbbecSDK', 'libobkit')],
    }, sdk_catalog=catalog)
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert 'feature' not in scoped['OrbbecSDK'].unmapped_condition_keys
    assert 'feature' in scoped['libobkit'].unmapped_condition_keys


def test_mixed_sdk_build_applies_to_its_named_library_only():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 和 libobkit 20260909 的音频播放分别怎么做？'
    assert 'daq-sdk-host-audio-007' in {
        row['kb_id'] for row in catalog.search(question, 'internal_fae', include_all=True)}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': sdk}}
            for sdk in ('OrbbecSDK', 'libobkit')],
    }, sdk_catalog=catalog)
    scoped = {goal.condition_map().get('sdk'): goal for goal in schema.goals}
    assert 'feature' not in scoped['OrbbecSDK'].unmapped_condition_keys
    assert 'feature' in scoped['libobkit'].unmapped_condition_keys


def test_mixed_sdk_build_owner_accepts_adjacent_chinese():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK音频播放和libobkit 20260909相比呢？'
    assert 'daq-sdk-host-audio-007' in {
        row['kb_id'] for row in catalog.search(question, 'internal_fae', include_all=True)}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'feature': 'audio_playback'},
        }],
    }, sdk_catalog=catalog)
    assert 'feature' not in schema.goals[0].unmapped_condition_keys


def test_sdk_tool_query_for_unreviewed_version_does_not_return_other_goal():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 和 2.8.0 的音频播放用法是否相同？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': version,
                            'feature': 'audio_playback'}}
            for version in ('2.9.0', '2.8.0')],
    }, sdk_catalog=catalog)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{
                         'id': goal.id, 'capability': goal.capability,
                         'entities': list(goal.entities),
                         'conditions': goal.condition_map(),
                         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
                     } for goal in schema.goals])
    old = box.dispatch('sdk_evidence', {
        'query': 'OrbbecSDK 2.8.0 audio playback usage', 'version': '2.8.0'})
    assert old.status == 'not_found'
    assert old.content['matched_requirement_ids'] == []
    new = box.dispatch('sdk_evidence', {
        'query': 'OrbbecSDK 2.9.0 audio playback usage'})
    assert new.status == 'ok'
    assert {row['kb_id'] for row in new.content['matches']} == {
        'daq-sdk-host-audio-007'}


@pytest.mark.parametrize('other', ('SDL2', 'OpenAL'))
def test_sdk_tool_query_for_other_audio_library_cannot_recover_orbbec(other):
    catalog = _candidate_catalog()
    question = f'OrbbecSDK 2.9.0 和 {other} 音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': name, 'feature': 'audio_playback'}}
            for name in ('OrbbecSDK', other)],
    }, sdk_catalog=catalog)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{
                         'id': goal.id, 'capability': goal.capability,
                         'entities': list(goal.entities),
                         'conditions': goal.condition_map(),
                         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
                     } for goal in schema.goals])
    unrelated = box.dispatch('sdk_evidence', {
        'query': f'{other} audio playback'})
    assert unrelated.status == 'not_found'
    assert unrelated.content['matched_requirement_ids'] == []
    orbbec = box.dispatch('sdk_evidence', {
        'query': 'OrbbecSDK 2.9.0 audio playback'})
    assert orbbec.status == 'ok'
    assert {row['kb_id'] for row in orbbec.content['matches']} == {
        'daq-sdk-host-audio-007'}


def test_package_contents_returns_reviewed_subset_without_claiming_full_inventory():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 的包内容与 OpenAL 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OpenAL', 'task': 'audio_playback'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'feature': 'openal', 'task': 'audio_playback'}},
        ],
    }, sdk_catalog=catalog)
    known = [goal for goal in schema.goals
             if goal.capability == 'sdk_evidence'
             and goal.condition_map() == {
                 'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0'}]
    assert len(known) == 1
    assert any(goal.condition_map().get('task') == 'inspect_package'
               and 'task' in goal.unmapped_condition_keys
               for goal in schema.goals)
    assert not any(goal.condition_map().get('sdk') == 'OrbbecSDK'
                   and goal.condition_map().get('feature') == 'openal'
                   for goal in schema.goals)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{
                         'id': goal.id, 'capability': goal.capability,
                         'entities': list(goal.entities),
                         'conditions': goal.condition_map(),
                         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
                         'reason': ('unmapped_condition_unverified'
                                    if goal.unmapped_condition_keys else ''),
                     } for goal in schema.goals])
    result = box.dispatch('sdk_evidence', {
        'query': 'OrbbecSDK 2.9.0 package contents'})
    assert result.status == 'ok'
    assert known[0].id in result.content['matched_requirement_ids']
    assert all(row['version'] == '2.9.0'
               for row in result.content['matches'])
    assert all(row['sdk_layer'] == 'host_cpp'
               for row in result.content['matches'])
    assert result.content['package_inventory_scope'] == (
        'returned_source_checked_records_are_a_subset_not_the_full_package')


@pytest.mark.parametrize('question', (
    'OrbbecSDK 的 2.8.0 版本包里有什么？',
    'OrbbecSDK 版本 2.8.0 包里有什么？',
    'OrbbecSDK 的版本是 2.8.0，包里有什么？',
))
def test_possessive_sdk_version_cannot_be_erased_by_generic_package_goal(question):
    catalog = _candidate_catalog()
    assert not any(row['version'] == '2.9.0' for row in
                   catalog.search(question, 'internal_fae', include_all=True))
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=catalog)
    sdk_goals = [goal for goal in schema.goals
                 if goal.capability == 'sdk_evidence'
                 and goal.condition_map().get('sdk') == 'OrbbecSDK']
    assert sdk_goals
    assert all(goal.condition_map().get('sdk_version') == '2.8.0'
               for goal in sdk_goals)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{
                         'id': goal.id, 'capability': goal.capability,
                         'entities': list(goal.entities),
                         'conditions': goal.condition_map(),
                         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
                     } for goal in sdk_goals])
    other_version = box.dispatch('sdk_evidence', {'query': 'OrbbecSDK 包内容'})
    assert other_version.content['matched_requirement_ids'] == []


def test_respectively_package_and_openal_drops_invented_cross_sdk_api_goal():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 的包内容与 OpenAL 的音频播放分别怎么做？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OpenAL', 'task': 'audio_playback'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'api': 'OpenAL', 'task': 'audio_playback'}},
        ],
    }, sdk_catalog=catalog)
    assert not any(goal.condition_map().get('sdk') == 'OrbbecSDK'
                   and goal.condition_map().get('api') == 'OpenAL'
                   for goal in schema.goals)
    assert any(goal.condition_map().get('sdk') == 'OpenAL'
               for goal in schema.goals)


@pytest.mark.parametrize('question', (
    'OrbbecSDK 的包内容与 OpenAL 的音频播放分别怎么做？',
    'OrbbecSDK 2.8.0 的包内容与 OpenAL 的音频播放分别怎么做？',
    'OrbbecSDK 2.9.0 不是我要的，请给我 OrbbecSDK 2.8.0 的包内容。',
    'OrbbecSDK 2.9.0 包内容不用了，请改查 OrbbecSDK 2.8.0 的包内容。',
    'OrbbecSDK 2.9.0 的包内容不要，请给我 OrbbecSDK 的包内容总览。',
))
def test_package_subset_cannot_promote_model_invented_sdk_version(question):
    catalog = _candidate_catalog()
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'task': 'inspect_package'},
        }],
    }, sdk_catalog=catalog)
    assert not any(goal.condition_map() == {
        'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0'}
        and not goal.unmapped_condition_keys for goal in schema.goals)


def test_retracted_sdk_package_version_keeps_current_request_unknown():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2.9.0 不是我要的，请给我 OrbbecSDK 2.8.0 '
                '的包内容。')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'task': 'inspect_package'},
        }, {
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0'},
        }],
    }, sdk_catalog=catalog)
    assert any(goal.condition_map().get('sdk_version') == '2.8.0'
               and goal.condition_map().get('task') == 'inspect_package'
               and 'task' in goal.unmapped_condition_keys
               for goal in schema.goals)
    assert not any(goal.condition_map().get('sdk_version') == '2.9.0'
                   for goal in schema.goals)


def test_magnetometer_example_goal_survives_viewer_changelog_clause():
    catalog = _candidate_catalog()
    question = ('EgoViewer desktop 2026-09-23 的变更日志是什么？'
                'OrbbecSDK 2026-09-29 包里有磁力计示例吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
                           'feature': 'magnetometer'},
        }],
    }, sdk_catalog=catalog)
    assert 'feature' not in schema.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2026-09-23 包里有磁力计示例吗？',
    'EgoViewer desktop 2026-09-23 包里有磁力计示例吗？',
    'OrbbecSDK 2026-09-29 的 Android 包里有磁力计示例吗？',
))
def test_magnetometer_example_alias_requires_matching_sdk_build_and_platform(question):
    catalog = _candidate_catalog()
    assert not catalog.grounds_selector(question, 'feature', 'magnetometer')


def test_sdk_cross_platform_comparison_keeps_known_linux_and_unknown_android():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 的 Linux x86_64 与 Android 包里'
                '分别有磁力计示例吗？')
    hits = catalog.search(question, 'internal_fae', include_all=True)
    assert 'daq-sdk-host-magnetometer-sample-023' in {
        row['kb_id'] for row in hits}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
                            'platform': platform, 'feature': 'magnetometer',
                            'task': 'inspect_package'}}
            for platform in ('linux_x86_64', 'android')],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map().get('platform'): goal
                   for goal in schema.goals}
    assert 'feature' not in by_platform['linux_x86_64'].unmapped_condition_keys
    assert 'task' not in by_platform['linux_x86_64'].condition_map()
    assert 'feature' in by_platform['android'].unmapped_condition_keys
    assert 'task' in by_platform['android'].unmapped_condition_keys

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
    assert result.status == 'ok'
    assert {row['kb_id'] for row in result.content['matches']} == {
        'daq-sdk-host-magnetometer-sample-023'}
    assert result.content['matched_requirement_ids'] == [
        by_platform['linux_x86_64'].id]
    android_only = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{
            'id': goal.id, 'capability': goal.capability,
            'entities': list(goal.entities),
            'conditions': goal.condition_map(),
            'unmapped_condition_keys': list(goal.unmapped_condition_keys),
        } for goal in schema.goals],
    ).dispatch('sdk_evidence', {
        'query': 'OrbbecSDK 2026-09-29 Android magnetometer example'})
    assert android_only.status == 'not_found'


def test_sdk_cross_build_comparison_keeps_reviewed_build_when_other_is_unknown():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 20260929 与 20260930 的磁力计示例各有哪些？'
    assert 'daq-sdk-host-magnetometer-sample-023' in {
        row['kb_id'] for row in catalog.search(question, 'internal_fae',
                                               include_all=True)}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': build,
                            'feature': 'magnetometer'}}
            for build in ('20260929', '20260930')],
    }, sdk_catalog=catalog)
    by_build = {goal.condition_map().get('sdk_release_date'): goal
                for goal in schema.goals}

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
            'entities': list(goal.entities), 'conditions': goal.condition_map(),
            'unmapped_condition_keys': list(goal.unmapped_condition_keys),
        } for goal in schema.goals],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert {row['kb_id'] for row in result.content['matches']} == {
        'daq-sdk-host-magnetometer-sample-023'}
    assert result.content['matched_requirement_ids'] == [by_build['20260929'].id]
    assert by_build['20260930'].id not in result.content['matched_requirement_ids']


@pytest.mark.parametrize('question', (
    'OrbbecSDK 2.9.0 与 2.9.1 的音频播放示例各有哪些？',
    'OrbbecSDK 2.9.1 与 2.9.0 的音频播放示例各有哪些？',
))
def test_sdk_cross_version_search_keeps_reviewed_version(question):
    catalog = _candidate_catalog()
    assert 'daq-sdk-host-audio-007' in {
        row['kb_id'] for row in catalog.search(question, 'internal_fae',
                                               include_all=True)}
    assert catalog.search('OrbbecSDK 2.9.1 音频播放示例',
                          'internal_fae', include_all=True) == []
    assert catalog.search('OrbbecSDK 的 2.8.0 版本音频播放怎么做？',
                          'internal_fae', include_all=True) == []


def test_sdk_tool_cross_platform_query_keeps_android_row_when_linux_has_arch():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 Linux x86_64 和 Android 包里 '
                'SD 卡文件下载分别怎么做？')
    assert {'daq-sdk-host-sdcard-005', 'daq-sdk-android-sdcard-018'} <= {
        row['kb_id'] for row in catalog.search(
            question, 'internal_fae', include_all=True)}

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[
            {'id': 'linux', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'sdk_release_date': '2026-09-29',
                            'platform': 'Linux x86_64', 'task': 'download'}},
            {'id': 'android', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'sdk_release_date': '2026-09-29',
                            'platform': 'Android', 'task': 'file_download'}},
        ],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert {'daq-sdk-host-sdcard-005', 'daq-sdk-android-sdcard-018'} <= {
        row['kb_id'] for row in result.content['matches']}
    assert result.content['matched_requirement_ids'] == ['android', 'linux']


def test_sdk_tool_mixed_libraries_does_not_apply_host_cpu_to_device_sdk():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2.9.0 Linux x86_64 的磁力计示例和 RK3576 '
                'libobkit 的 CMake 编译安装分别怎么做？')
    assert {'daq-sdk-host-magnetometer-sample-023',
            'daq-sdk-device-build-012'} <= {
        row['kb_id'] for row in catalog.search(
            question, 'internal_fae', include_all=True)}

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[
            {'id': 'host', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'platform': 'Linux x86_64',
                            'feature': 'magnetometer'}},
            {'id': 'device', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'libobkit', 'platform': 'device_side',
                            'task': 'build'}},
        ],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert {'daq-sdk-host-magnetometer-sample-023',
            'daq-sdk-device-build-012'} <= {
        row['kb_id'] for row in result.content['matches']}
    assert result.content['matched_requirement_ids'] == ['device', 'host']


@pytest.mark.parametrize('selector_slot', ('feature', 'task'))
def test_sdcard_download_paraphrase_binds_each_reviewed_platform_task(
        selector_slot):
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 Linux x86_64 和 Android 包里 '
                'SD 卡文件下载分别怎么做？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'sdk_release_date': '2026-09-29',
                            'platform': platform,
                            selector_slot: 'sd_card_file_download'}}
            for platform in ('linux_x86_64', 'android')],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map()['platform']: goal
                   for goal in schema.goals}
    assert by_platform['linux_x86_64'].condition_map()['task'] == 'download'
    assert by_platform['android'].condition_map()['task'] == 'file_download'
    assert all('feature' not in goal.condition_map()
               and not goal.unmapped_condition_keys
               for goal in by_platform.values())

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{
                         'id': goal.id, 'capability': goal.capability,
                         'entities': list(goal.entities),
                         'conditions': goal.condition_map(),
                         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
                     } for goal in schema.goals])
    for platform, source_id in (
            ('Linux x86_64', 'daq-sdk-host-sdcard-005'),
            ('Android', 'daq-sdk-android-sdcard-018')):
        result = box.dispatch('sdk_evidence', {
            'query': f'OrbbecSDK 2026-09-29 {platform} SD card file download'})
        assert result.status == 'ok'
        assert source_id in {row['kb_id'] for row in result.content['matches']}
        assert by_platform[('linux_x86_64' if platform.startswith('Linux')
                            else 'android')].id in result.content['matched_requirement_ids']


def test_sdcard_comparison_restores_android_when_model_only_returns_generic_goal():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 Linux x86_64 和 Android 包里 '
                'SD 卡文件下载分别怎么做？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK',
                           'sdk_release_date': '2026-09-29'},
        }],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map().get('platform'): goal
                   for goal in schema.goals}
    assert set(by_platform) == {'linux_x86_64', 'android'}
    assert by_platform['linux_x86_64'].condition_map()['task'] == 'download'
    assert by_platform['android'].condition_map()['task'] == 'file_download'
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


@pytest.mark.parametrize(('question', 'candidate_scope', 'axis', 'expected'), (
    ('OrbbecSDK 2026-09-29 的 Linux x86_64 与 Android 包里分别有磁力计示例吗？',
     {'sdk_release_date': '2026-09-29'}, 'platform',
     {'linux_x86_64', 'android'}),
    ('OrbbecSDK 2.8.0 和 2.9.0 的 Linux x86_64 包里分别有磁力计示例吗？',
     {'sdk_version': '2.9.0', 'platform': 'linux_x86_64'}, 'sdk_version',
     {'2.8.0', '2.9.0'}),
    ('OrbbecSDK 2026-09-23 和 2026-09-29 的 Linux x86_64 包里分别有磁力计示例吗？',
     {'sdk_release_date': '2026-09-29', 'platform': 'linux_x86_64'},
     'sdk_release_date', {'2026-09-23', '2026-09-29'}),
))
def test_magnetometer_comparison_restores_each_explicit_scope(
        question, candidate_scope, axis, expected):
    catalog = _candidate_catalog()
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'feature': 'magnetometer',
                           'task': 'inspect_package', **candidate_scope},
        }],
    }, sdk_catalog=catalog)
    values = {goal.condition_map().get(axis) for goal in schema.goals}
    assert values == expected
    assert len(schema.goals) == 2
    known = next(goal for goal in schema.goals if
                 (goal.condition_map().get(axis) == 'linux_x86_64'
                  if axis == 'platform' else
                  goal.condition_map().get(axis) in {
                      '2.9.0', '2026-09-29'}))
    assert 'feature' not in known.unmapped_condition_keys
    assert 'task' not in known.condition_map()
    unknown = next(goal for goal in schema.goals if goal.id != known.id)
    assert 'feature' in unknown.unmapped_condition_keys


def test_magnetometer_comparison_adds_feature_when_model_only_named_sdk():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 的 Linux x86_64 与 Android 包里'
                '分别有磁力计示例吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK',
                           'sdk_release_date': '2026-09-29'},
        }],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map().get('platform'): goal
                   for goal in schema.goals}
    assert set(by_platform) == {'linux_x86_64', 'android'}
    assert all(goal.condition_map().get('feature') == 'magnetometer'
               for goal in schema.goals)
    assert not by_platform['linux_x86_64'].unmapped_condition_keys
    assert 'feature' in by_platform['android'].unmapped_condition_keys


def test_magnetometer_explicit_version_platform_matrix_does_not_drop_cells():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2.8.0 和 2.9.0 每个版本在 Linux x86_64 和 '
                'Android 两个平台的包里各有磁力计示例吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'platform': 'linux_x86_64',
                           'feature': 'magnetometer',
                           'task': 'inspect_package'},
        }],
    }, sdk_catalog=catalog)
    cells = {(goal.condition_map().get('sdk_version'),
              goal.condition_map().get('platform')): goal
             for goal in schema.goals}
    assert set(cells) == {
        ('2.8.0', 'linux_x86_64'), ('2.8.0', 'android'),
        ('2.9.0', 'linux_x86_64'), ('2.9.0', 'android')}
    assert not cells['2.9.0', 'linux_x86_64'].unmapped_condition_keys
    assert all(cells[key].unmapped_condition_keys
               for key in cells if key != ('2.9.0', 'linux_x86_64'))


@pytest.mark.parametrize('version,known', (('2.8.0', False), ('2.9.0', True)))
@pytest.mark.parametrize('phrase', ('OrbbecSDK {version}',
                                     'OrbbecSDK 的 {version} 版本'))
def test_single_sdk_version_is_preserved_across_platform_comparison(
        version, known, phrase):
    catalog = _candidate_catalog()
    question = (f'{phrase.format(version=version)} 的 Linux x86_64 与 Android 包里'
                '分别有磁力计示例吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK'},
        }],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map().get('platform'): goal
                   for goal in schema.goals}
    assert set(by_platform) == {'linux_x86_64', 'android'}
    assert all(goal.condition_map().get('sdk_version') == version
               for goal in schema.goals)

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
            'entities': list(goal.entities), 'conditions': goal.condition_map(),
            'unmapped_condition_keys': list(goal.unmapped_condition_keys),
        } for goal in schema.goals],
    ).dispatch('sdk_evidence', {'query': 'magnetometer example'})
    if known:
        assert result.status == 'ok'
        assert result.content['matched_requirement_ids'] == [
            by_platform['linux_x86_64'].id]
    else:
        assert result.status == 'not_found'
        assert not result.content.get('matched_requirement_ids')


def test_sdk_compared_builds_pair_with_their_own_platforms():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-21 Linux x86_64 和 2026-09-29 '
                'Android 包里 SD 卡文件下载分别怎么做？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': build,
                            'platform': platform,
                            'feature': 'sd_card_file_download'}}
            for build, platform in (
                ('2026-09-21', 'linux_x86_64'),
                ('2026-09-29', 'android'))],
    }, sdk_catalog=catalog)
    assert {(goal.condition_map().get('sdk_release_date'),
             goal.condition_map().get('platform')) for goal in schema.goals} == {
                 ('2026-09-21', 'linux_x86_64'),
                 ('2026-09-29', 'android')}


def test_sdk_comparison_does_not_borrow_subject_from_next_sentence():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 的 Linux x86_64 与 Android 包里'
                '分别有什么示例？另问 EGO 的 SD 卡文件怎么下载？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK',
                           'sdk_release_date': '2026-09-29',
                           'platform': 'linux_x86_64'},
        }],
    }, sdk_catalog=catalog)
    assert not any(goal.condition_map().get('task') in {
        'download', 'file_download'} for goal in schema.goals)
    assert any(goal.condition_map().get('task') is None
               for goal in schema.goals)


def test_sdk_comparison_keeps_separate_package_inventory_clause_and_version():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 Linux x86_64 与 Android 包里 SD 卡文件下载分别怎么做？'
                'OrbbecSDK 2.9.0 的包内容总览是什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'sdk_release_date': '2026-09-29',
                            'platform': 'linux_x86_64',
                            'feature': 'sd_card_file_download'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'task': 'inspect_package'}},
        ],
    }, sdk_catalog=catalog)
    download = [goal for goal in schema.goals
                if goal.condition_map().get('task') in {'download', 'file_download'}]
    assert {goal.condition_map().get('platform') for goal in download} == {
        'linux_x86_64', 'android'}
    assert all('sdk_version' not in goal.condition_map() for goal in download)
    assert any(goal.condition_map().get('task') == 'inspect_package'
               and goal.condition_map().get('sdk_version') == '2.9.0'
               for goal in schema.goals)


def test_sdk_platform_matrix_preserves_each_platforms_named_api():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 Linux x86_64 的 getSdcardFileList 和 '
                'Android 的 getSdcardFileInfo 下载 SD 卡文件分别怎么做？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK',
                            'sdk_release_date': '2026-09-29',
                            'platform': platform, 'api': api,
                            'task': task}}
            for platform, api, task in (
                ('linux_x86_64', 'getSdcardFileList', 'download'),
                ('android', 'getSdcardFileInfo', 'file_download'))],
    }, sdk_catalog=catalog)
    by_platform = {goal.condition_map().get('platform'): goal
                   for goal in schema.goals}
    assert by_platform['linux_x86_64'].condition_map()['api'] == 'getSdcardFileList'
    assert by_platform['android'].condition_map()['api'] == 'getSdcardFileInfo'


@pytest.mark.parametrize(('question', 'platform', 'build'), (
    ('OrbbecSDK 2026-09-29 的 Linux x86_64 包里有磁力计示例吗，怎么编译运行？',
     'linux_x86_64', '2026-09-29'),
    ('OrbbecSDK 2026-09-23 的 Linux x86_64 包里有磁力计示例吗？',
     'linux_x86_64', '2026-09-23'),
    ('OrbbecSDK 2026-09-29 的 Android 包里有磁力计示例吗？',
     'android', '2026-09-29'),
))
def test_magnetometer_presence_normalization_preserves_unreviewed_scope(
        question, platform, build):
    catalog = _candidate_catalog()
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': build,
                           'platform': platform, 'feature': 'magnetometer',
                           'task': 'inspect_package'},
        }],
    }, sdk_catalog=catalog)
    assert schema.goals[0].condition_map().get('task') == 'inspect_package'
    assert 'task' in schema.goals[0].unmapped_condition_keys


def test_magnetometer_presence_is_not_blocked_by_other_platform_followup():
    catalog = _candidate_catalog()
    question = ('OrbbecSDK 2026-09-29 的 Linux x86_64 包里有磁力计示例吗？'
                'Android 包怎么编译？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
                            'platform': 'linux_x86_64', 'feature': 'magnetometer',
                            'task': 'inspect_package'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '2026-09-29',
                            'platform': 'android', 'task': 'compile'}},
        ],
    }, sdk_catalog=catalog)
    linux = next(goal for goal in schema.goals
                 if goal.condition_map().get('platform') == 'linux_x86_64')
    android = next(goal for goal in schema.goals
                   if goal.condition_map().get('platform') == 'android')
    assert 'task' not in linux.condition_map()
    assert 'task' in android.unmapped_condition_keys


def test_callable_symbol_must_be_a_source_checked_call(tmp_path):
    archive = Path('data/knowledge/local_archive/sdk-originals-1015-local-20261015')
    if not archive.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    rows = json.loads(Path('Knowledge_SDK/curated.json').read_text())
    callback = next(row for row in rows
                    if row['kb_id'] == 'daq-sdk-android-record-state-019')
    assert callback['callable_symbols'] == [
        'setEgoStateCallback', 'setEgoRecordingResultCallback']
    callback['callable_symbols'] = ['SAVING']
    curated = tmp_path / 'curated.json'
    curated.write_text(json.dumps(rows, ensure_ascii=False))
    with pytest.raises(ValueError, match='callable symbol lacks source call span'):
        build_catalog(curated, archive)


def test_sdk_tool_keeps_package_scope_and_build_status_typed():
    catalog = _candidate_catalog()

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    question = "四目 EGO 设备端 libobkit 示例怎么编译和部署？它能和任意整机镜像搭配吗？"
    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role="internal_fae",
        requirements=[{"id": "build", "capability": "sdk_evidence",
                       "entities": ["四目 EGO"],
                       "conditions": {"sdk": "libobkit", "task": "build_and_deploy"}}],
    ).dispatch("sdk_evidence", {
        "query": "libobkit 设备端示例 编译 部署 build and deploy example",
    })
    assert result.status == "ok"
    assert {row["kb_id"] for row in result.content["matches"]} >= {
        "daq-sdk-device-build-012", "daq-sdk-device-scope-011",
    }
    assert result.content["matched_requirement_ids"] == ["build"]
    assert result.content["device_support"] == "unknown"

    install = DaqToolBox(
        question="EgoLowBle 1.1.8 Windows x64 DLL 怎么集成？",
        knowledge=Knowledge(), role="internal_fae",
    ).dispatch("sdk_evidence", {
        "query": "EgoLowBle 1.1.8 Windows x64 DLL 怎么集成？",
    })
    assert install.status == "ok"
    assert install.content["example_build_status"] == "not_applicable"


def test_device_side_build_schema_retrieves_reviewed_libobkit_usage():
    catalog = _candidate_catalog()
    question = ("四目 EGO 设备端 libobkit 示例怎么编译和部署？"
                "它能和任意整机镜像搭配吗？")
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "medium", "goals": [{
            "kind": "product", "capability": "sdk_evidence",
            "entities": ["四目 EGO"],
            "conditions": {"platform": "device", "sdk": "libobkit",
                           "standalone": True, "task": "compile_and_deploy"},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role="internal_fae",
        requirements=[{"id": goal.id, "capability": goal.capability,
                       "entities": list(goal.entities),
                       "conditions": goal.condition_map()}],
    ).dispatch("sdk_evidence", {"query": "libobkit 示例编译与部署 device 端"})
    assert result.status == "ok"
    assert "daq-sdk-device-build-012" in {
        row["kb_id"] for row in result.content["matches"]}
    assert result.content["matched_requirement_ids"] == [goal.id]


def _candidate_catalog() -> SdkCatalog:
    directory = Path("Knowledge_SDK")
    records = [json.loads(line) for line in (directory / "SDK_Knowledge.jsonl").read_text().splitlines()]
    manifest = json.loads((directory / "SDK_Knowledge_manifest.json").read_text())
    return SdkCatalog(records, manifest)


def test_sdk_search_ranks_audio_topic_above_shared_sdk_identity():
    catalog = _candidate_catalog()
    question = 'OrbbecSDK 2.9.0 的音频播放怎么做？'
    hits = catalog.search(question, 'internal_fae')
    assert hits and hits[0]['kb_id'] == 'daq-sdk-host-audio-007'
    assert catalog.search('OrbbecSDK 2.8.0 的音频播放怎么做？',
                          'internal_fae') == []

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{'id': 'audio', 'capability': 'sdk_evidence',
                       'entities': [], 'conditions': {
                           'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                           'task': 'audio_playback'}}],
    ).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['audio']
    assert 'daq-sdk-host-audio-007' in {
        row['kb_id'] for row in result.content['matches']}


def test_audio_tool_does_not_certify_wrong_sdk_version_or_platform():
    catalog = _candidate_catalog()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    for question in ('OrbbecSDK 2.8.0 的音频播放怎么做？',
                     'OrbbecSDK 2.9.0 的 Win11 音频播放怎么做？',
                     'OrbbecSDK 2.9.0 在 Linux ARM64 怎么播放音频？',
                     'OrbbecSDK 2.9.0 在 Ubuntu ARM64 怎么播放音频？',
                     'OrbbecSDK 2.9.0 在 Linux ARMv8 怎么播放音频？',
                     'OrbbecSDK 2.9.0 在 Linux riscv64 怎么播放音频？',
                     'OrbbecSDK 2.9.0 在 ARM64 怎么播放音频？'):
        schema = DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
                'conditions': {'sdk': 'OrbbecSDK', 'feature': 'audio_playback'},
            }],
        }, sdk_catalog=catalog)
        goal = schema.goals[0]
        result = DaqToolBox(
            question=question, knowledge=Knowledge(), role='internal_fae',
            requirements=[{'id': goal.id, 'capability': 'sdk_evidence',
                           'entities': [], 'conditions': goal.condition_map(),
                           'unmapped_condition_keys': goal.unmapped_condition_keys}],
        ).dispatch('sdk_evidence', {'query': question})
        assert goal.id not in result.content.get('matched_requirement_ids', [])


def test_scoped_sdk_search_survives_unrelated_viewer_build_in_user_question():
    catalog = _candidate_catalog()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(
        question=('EgoLowBle Windows x64 DLL 怎样放？另请核对 EgoViewer Android '
                  '2026092302 的变更日志。'),
        knowledge=Knowledge(), role='internal_fae', requirements=[
            {'id': 'ble', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'platform': 'windows_x64',
                            'task': 'link_library_and_place_dll'}}])
    result = box.dispatch('sdk_evidence', {'query': 'EgoLowBle Windows x64 DLL 怎样放'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['ble']
    assert {source['source_id'] for source in result.sources} == {
        'daq-sdk-ble-windows-install-020'}


def test_sdk_source_filename_recovers_exact_hashed_sample():
    catalog = _candidate_catalog()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    requirement = {'id': 'uid_file', 'capability': 'sdk_evidence', 'entities': [],
                   'conditions': {'sdk': 'OrbbecSDK', 'platform': 'linux_x64',
                                  'sdk_release_date': '20260929',
                                  'source_files': ['ego_net_uid_hotplug.cpp']}}
    box = DaqToolBox(question='OrbbecSDK Linux x64 20260929 ego_net_uid_hotplug.cpp',
                     knowledge=Knowledge(), role='internal_fae',
                     requirements=[requirement])
    result = box.dispatch('sdk_evidence', {'query': box.question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['uid_file']
    assert {source['source_id'] for source in result.sources} == {
        'daq-sdk-host-net-uid-hotplug-016'}


def test_sdk_search_recovers_each_goal_when_tool_query_contains_viewer_clause():
    catalog = _candidate_catalog()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(
        question=('EgoLowBle Windows x64 DLL 怎样放？另请核对 EgoViewer Android '
                  '2026092302 的变更日志。'),
        knowledge=Knowledge(), role='internal_fae', requirements=[
            {'id': 'ble', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'platform': 'windows_x64',
                            'task': 'link_library_and_place_dll'}}])
    result = box.dispatch('sdk_evidence', {'query': box.question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['ble']


def test_host_uid_hotplug_record_has_source_bound_scope_without_device_support():
    directory = Path("Knowledge_SDK")
    records = [json.loads(line) for line in (directory / "SDK_Knowledge.jsonl").read_text().splitlines()]
    uid = next(row for row in records if row["kb_id"] == "daq-sdk-host-net-uid-hotplug-016")
    assert uid["package"] == "host_cpp_20260929"
    assert uid["build"] == "20260929"
    assert uid["source_refs"]
    assert all(ref["member_sha256"] and ref["package_sha256"] for ref in uid["source_refs"])
    assert "兼容" not in uid["answer"]


def test_archived_host_uid_hotplug_example_retrieves_by_api_intent():
    directory = Path("Knowledge_SDK")
    archive = Path("data/knowledge/local_archive/sdk-originals-1015-local-20261015")
    if not archive.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = SdkCatalog.load(directory, archive)
    rows = catalog.search("OrbbecSDK 网络设备 UID 热插拔移除回调怎么核对", "internal_fae")
    uid = next(row for row in rows if row["kb_id"] == "daq-sdk-host-net-uid-hotplug-016")
    assert uid["platform"] == "Linux x86_64"


def test_archived_magnetometer_sample_is_package_fact_not_umi_support():
    archive = Path("data/knowledge/local_archive/sdk-originals-1015-local-20261015")
    if not archive.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = SdkCatalog.load(Path("Knowledge_SDK"), archive)
    rows = catalog.search(
        "OrbbecSDK 2026-09-29 包中 magnetometer.cpp 示例是否存在", "internal_fae")
    sample = next(row for row in rows if row["kb_id"] ==
                  "daq-sdk-host-magnetometer-sample-023")
    assert not sample.get("outputs")
    assert sample["build"] == "20260929"
    assert any(ref["member"].endswith("/magnetometer.cpp")
               for ref in sample["source_refs"])
    assert sample["doc_type"] == "example"
    assert sample["sdk_layer"] == "host_cpp"
    assert "UMI Finger" not in sample["answer"]


def test_sdk_search_hint_does_not_certify_umi_device_applicability():
    catalog = _candidate_catalog()
    sample = next(row for row in catalog.records if row['kb_id'] ==
                  'daq-sdk-host-magnetometer-sample-023')
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    box = DaqToolBox(knowledge=Knowledge(), role='internal_fae', requirements=[
        {'id': 'umi', 'capability': 'sdk_evidence',
         'entities': ['UMI Finger'],
         'conditions': {'task': 'magnetometer_sample'}},
        {'id': 'package', 'capability': 'sdk_evidence', 'entities': [],
         'conditions': {'task': 'magnetometer_sample'}},
    ])
    assert box._sdk_usage_requirement_ids([sample]) == ['package']


def test_example_subject_must_be_named_by_verified_source_member(tmp_path):
    archive = Path('data/knowledge/local_archive/sdk-originals-1015-local-20261015')
    if not archive.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    curated = json.loads(Path('Knowledge_SDK/curated.json').read_text())
    depth = next(row for row in curated if row['kb_id'] ==
                 'daq-sdk-host-depth-009')
    assert depth['example_subjects'] == ['EGO Depth']
    depth['example_subjects'] = ['UMI Finger']
    tampered = tmp_path / 'curated.json'
    tampered.write_text(json.dumps(curated, ensure_ascii=False))
    with pytest.raises(ValueError, match='example subject lacks source member'):
        build_catalog(tampered, archive)


def test_linux_ble_link_and_runtime_path_are_source_checked():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Linux x64 包怎么链接 libEgoLowBle.so，'
                '运行时把库放在哪里？')
    goal = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                           'platform': 'linux_x64', 'task': 'link_and_deploy'},
        }],
    }, sdk_catalog=catalog).goals[0]
    assert goal.unmapped_condition_keys == ()

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[{
                            'id': goal.id, 'capability': goal.capability,
                            'entities': list(goal.entities),
                            'conditions': goal.condition_map(),
                        }]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert goal.id in result.content['matched_requirement_ids']
    assert 'daq-sdk-ble-linux-link-024' in {
        row['kb_id'] for row in result.content['matches']}
    record = next(row for row in catalog.records
                  if row['kb_id'] == 'daq-sdk-ble-linux-link-024')
    assert {'LINUX_GUIDE.md', 'build_linux.sh', 'run_demo.sh'} <= {
        ref['member'].split('\\')[-1] for ref in record['source_refs']}
    assert 'LD_LIBRARY_PATH' in record['answer']
    assert 'RPATH' in record['answer']


def test_zip_member_api_fact_is_hash_and_line_bound(tmp_path, monkeypatch):
    from daq_fae.knowledge import sdk_catalog
    monkeypatch.setattr(sdk_catalog, "PACKAGES", {
        "android": {"glob": "android.zip", "version": "beta2.2.5", "build": "20260929",
                    "platform": "Android", "format": "zip"},
    })
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "android.zip", "w") as bundle:
        bundle.writestr("wrapper/doc/api.md", "Header\nDevice.exportFirmwareLogs(callback)\n")
    archive = tmp_path / "archive"
    create_archive(source, archive, "2026-10-16")
    curated = tmp_path / "curated.json"
    curated.write_text(json.dumps([{
        "kb_id": "android-log", "package": "android", "sdk_layer": "android_java",
        "doc_type": "api", "title": "Android firmware log", "question_patterns": ["Android firmware log"],
        "answer": "Java API exists; device support unknown.", "symbols": ["exportFirmwareLogs"],
        "topics": ["firmware_log"], "last_verified": "2026-10-16",
        "source_refs": [{"member_suffix": "doc/api.md", "start_line": 2, "end_line": 2}],
    }]))
    records, manifest = build_catalog(curated, archive)
    assert records[0]["source_refs"][0]["member"] == "wrapper/doc/api.md"
    assert records[0]["source_refs"][0]["member_sha256"]
    assert records[0]["last_verified"] == "2026-10-16"
    assert manifest["record_count"] == 1


@pytest.mark.parametrize("unsafe_name", ["../api.md", "C:\\temp\\api.md", "C:relative\\api.md", "\\\\server\\share\\api.md"])
def test_zip_source_member_rejects_traversal_and_absolute_names(tmp_path, unsafe_name):
    from daq_fae.knowledge.sdk_catalog import _source_member
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(unsafe_name, "API fact\n")
    with zipfile.ZipFile(path) as bundle, pytest.raises(ValueError, match="unsafe path"):
        _source_member(bundle, "api.md")


def test_zip_source_member_rejects_large_uncompressed_text(tmp_path):
    from daq_fae.knowledge.sdk_catalog import _source_member
    path = tmp_path / "large.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("doc/api.md", "A" * 2_000_001)
    with zipfile.ZipFile(path) as bundle, pytest.raises(ValueError, match="too large"):
        _source_member(bundle, "api.md")


def test_android_sdk_intent_does_not_route_to_host_cpp():
    android = {"kb_id": "android-log", "sdk_layer": "android_java", "version": "beta2.2.5",
               "build": "20260929", "platform": "Android", "doc_type": "api",
               "title": "Android 固件日志导出", "question_patterns": ["OrbbecSDK Android exportFirmwareLogs"],
               "symbols": ["exportFirmwareLogs"], "topics": ["firmware_log"],
               "answer": "Android Java API", "outputs": []}
    host = {**android, "kb_id": "host-log", "sdk_layer": "host_cpp", "platform": "Linux x86_64"}
    catalog = SdkCatalog([android, host], {})
    rows = catalog.search("OrbbecSDK Android Java exportFirmwareLogs 固件日志", "internal_fae")
    assert [row["kb_id"] for row in rows] == ["android-log"]


def test_explicit_android_and_host_cpp_question_preserves_both_layers():
    base = {"version": "2.9.0", "build": "20260929", "platform": "Linux x86_64",
            "doc_type": "api", "title": "固件日志导出", "symbols": ["exportFirmwareLogs"],
            "topics": ["firmware_log"], "answer": "SDK API", "outputs": []}
    android = {**base, "kb_id": "android-log", "sdk_layer": "android_java",
               "platform": "Android", "question_patterns": ["Android Java 固件日志导出"]}
    host = {**base, "kb_id": "host-log", "sdk_layer": "host_cpp",
            "question_patterns": ["Linux C++ 固件日志导出"]}
    catalog = SdkCatalog([android, host], {})
    rows = catalog.search("OrbbecSDK Android Java 与 Linux C++ 固件日志导出", "internal_fae")
    assert {row["kb_id"] for row in rows} == {"android-log", "host-log"}


@pytest.mark.parametrize("question,expected_id", [
    ("EgoLowBle 1.1.8 Windows 怎么链接？EGO 固件 1.1.8 能用吗？", "daq-sdk-ble-windows-install-020"),
    ("OrbbecSDK 2.9.0 iOS framework 如何嵌入？能证明 EGO 固件兼容吗？", "daq-sdk-ios-framework-022"),
])
def test_mixed_sdk_and_device_question_keeps_device_support_unknown(question, expected_id):
    directory = Path("Knowledge_SDK")
    records = [json.loads(line) for line in (directory / "SDK_Knowledge.jsonl").read_text().splitlines()]
    manifest = json.loads((directory / "SDK_Knowledge_manifest.json").read_text())
    catalog = SdkCatalog(records, manifest)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    toolbox = DaqToolBox(question=question, knowledge=Knowledge(), role="internal_fae")
    usage = toolbox.dispatch("sdk_evidence", {"query": question})
    assert usage.status == "ok"
    assert usage.content["device_support"] == "unknown"
    assert expected_id in {row["kb_id"] for row in usage.content["matches"]}
    assert toolbox.dispatch("check_software_support", {"query": question}).status == "not_found"


def test_ble_version_118_is_not_mistaken_for_orbbecsdk_version():
    ble = {"kb_id": "ble-install", "sdk_layer": "ble_cpp", "version": "1.1.8",
           "build": "20260929", "platform": "Windows x64", "doc_type": "install",
           "title": "EgoLowBle Windows 集成", "question_patterns": ["EgoLowBle 1.1.8 怎么链接"],
           "symbols": ["EgoLowBle.lib"], "topics": ["ble", "install"],
           "answer": "Link import library", "outputs": []}
    catalog = SdkCatalog([ble], {})
    assert [row["kb_id"] for row in catalog.search(
        "Windows EgoLowBle 1.1.8 怎么链接", "internal_fae")] == ["ble-install"]


def test_sdk_usage_matcher_respects_java_ble_and_ios_scope():
    class Knowledge:
        release_id = "dev-sdk"
        def records_for(self, role): return []
        def sections_for(self, role): return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "android", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk": "OrbbecSDK Android Wrapper", "language": "Java",
                        "api": "exportFirmwareLogs", "platform": "Android"}},
        {"id": "wrong_os", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk": "OrbbecSDK Android Wrapper", "language": "Java",
                        "api": "exportFirmwareLogs", "platform": "Windows"}},
        {"id": "ios_swift", "capability": "sdk_evidence", "entities": [],
         "conditions": {"sdk": "OrbbecSDK", "language": "Swift", "platform": "iOS"}},
    ])
    android_row = {"sdk_layer": "android_java", "version": "beta2.2.5", "build": "20260929",
                   "platform": "Android", "doc_type": "api", "symbols": ["exportFirmwareLogs"],
                   "topics": ["firmware_log"], "usage_tasks": ["firmware_log_export"]}
    ios_row = {"sdk_layer": "ios_framework", "version": "2.9.0", "build": "20260929",
               "platform": "iOS ARM64", "doc_type": "install", "symbols": [],
               "topics": ["framework"], "usage_tasks": ["integrate_framework"]}
    assert toolbox._sdk_usage_requirement_ids([android_row, ios_row]) == ["android"]


def test_all_24_catalog_rows_certify_their_exact_sdk_usage_scope():
    records = [json.loads(line) for line in Path("Knowledge_SDK/SDK_Knowledge.jsonl").read_text().splitlines()]
    assert len(records) == 24

    class Knowledge:
        release_id = "dev-sdk"
        def records_for(self, role): return []
        def sections_for(self, role): return []

    sdk_names = {"host_cpp": "OrbbecSDK", "device_c": "libobkit",
                 "android_java": "Android Wrapper", "ble_cpp": "EgoLowBle",
                 "ios_framework": "OrbbecSDK"}
    languages = {"host_cpp": "C++", "device_c": "C", "android_java": "Java",
                 "ble_cpp": "C++"}
    for row in records:
        conditions = {"sdk": sdk_names[row["sdk_layer"]], "sdk_version": row["version"],
                      "sdk_release_date": row["build"], "platform": row["platform"],
                      "task": row["usage_tasks"][0], "feature": row["usage_tasks"][0]}
        if row["sdk_layer"] in languages:
            conditions["language"] = languages[row["sdk_layer"]]
        if row["doc_type"] == "example":
            conditions["sample_type"] = "example"
        requirement = {"id": "sdk-goal", "capability": "sdk_evidence", "entities": [],
                       "conditions": conditions}
        toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae",
                             requirements=[requirement])
        assert toolbox._sdk_usage_requirement_ids([row]) == ["sdk-goal"], row["kb_id"]


def test_uid_comparison_intent_certifies_only_source_tagged_host_example():
    row = next(json.loads(line) for line in Path("Knowledge_SDK/SDK_Knowledge.jsonl").read_text().splitlines()
               if "daq-sdk-host-net-uid-hotplug-016" in line)

    class Knowledge:
        release_id = "dev-sdk"
        def records_for(self, role): return []
        def sections_for(self, role): return []

    requirement = {"id": "uid-goal", "capability": "sdk_evidence", "entities": [],
                   "conditions": {"sdk": "OrbbecSDK", "sdk_version": "2.9.0",
                                  "sdk_release_date": "2026-09-29", "platform": "linux x86_64",
                                  "language": "c++", "sample_type": "example",
                                  "feature": "uid_check",
                                  "task": "network_device_removal_callback"}}
    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae",
                         requirements=[requirement])
    assert toolbox._sdk_usage_requirement_ids([row]) == ["uid-goal"]


def test_uid_hotplug_schema_grounds_user_stated_chinese_feature():
    question = ("用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，"
                "怎样核对网络设备移除回调中的 UID？不要推断我的设备和固件已经兼容。")
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence", "entities": [],
                   "conditions": {"api": "uid", "feature": "network_device_removal_callback",
                                  "language": "c++", "platform": "linux x86_64",
                                  "sdk": "OrbbecSDK", "sdk_release_date": "2026-09-29",
                                  "sdk_version": "2.9.0"}}],
    }, sdk_catalog=_candidate_catalog())
    assert schema.goals[0].unmapped_condition_keys == ()


def test_uid_hotplug_model_concept_is_a_task_not_an_invented_api():
    question = ("用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，"
                "怎样核对网络设备移除回调中的 UID？不要推断我的设备和固件已经兼容。")
    catalog = _candidate_catalog()
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "goals": [{
            "kind": "product", "capability": "sdk_evidence", "entities": [],
            "conditions": {"api": "device_removed_callback", "sdk": "OrbbecSDK",
                           "sdk_version": "2.9.0", "sdk_release_date": "2026-09-29",
                           "platform": "linux_x86_64", "language": "c++"}}],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map()["task"] == "device_removed_callback"
    assert "api" not in goal.condition_map()
    assert goal.unmapped_condition_keys == ()
    row = next(row for row in catalog.records
               if row["kb_id"] == "daq-sdk-host-net-uid-hotplug-016")
    class Knowledge:
        release_id = "dev-sdk"
    assert DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "uid-goal", "capability": "sdk_evidence", "entities": [],
         "conditions": goal.condition_map()},
    ])._sdk_usage_requirement_ids([row]) == ["uid-goal"]


def test_uid_hotplug_schema_rejects_feature_absent_from_user_question():
    question = "用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，怎样读取 UID？"
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence", "entities": [],
                   "conditions": {"feature": "network_device_removal_callback",
                                  "sdk": "OrbbecSDK", "sdk_release_date": "2026-09-29",
                                  "sdk_version": "2.9.0"}}],
    }, sdk_catalog=_candidate_catalog())
    assert schema.goals[0].unmapped_condition_keys == ("feature",)


def test_uid_hotplug_schema_does_not_translate_without_source_checked_catalog():
    question = "用 OrbbecSDK 示例怎样核对网络设备移除回调中的 UID？"
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence", "entities": [],
                   "conditions": {"feature": "network_device_removal_callback"}}],
    })
    assert schema.goals[0].unmapped_condition_keys == ("feature",)


def test_android_sdcard_schema_grounds_user_stated_chinese_sdk_task():
    question = ("2026-09-29 Android Wrapper beta2.2.5 的 Java API，"
                "网络 EGO 的 SD 卡文件信息与本地下载该怎么调用？这证明具体固件支持吗？")
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence", "entities": ["EGO"],
                   "conditions": {"feature": "network_ego_sd_card_file_access",
                                  "task": "sd_card_file_info_and_local_download",
                                  "language": "java", "platform": "android",
                                  "sdk": "Android Wrapper", "sdk_release_date": "2026-09-29",
                                  "sdk_version": "beta2.2.5"}}],
    }, sdk_catalog=_candidate_catalog())
    assert schema.goals[0].unmapped_condition_keys == ()


def test_sdk_schema_extraction_uses_only_loaded_source_checked_catalog():
    question = "用 OrbbecSDK 示例怎样核对网络设备移除回调中的 UID？"

    class Adapter:
        def chat(self, messages, tools, required_tool):
            yield {"type": "tool_call", "name": "submit_daq_request_schema",
                   "arguments": {"confidence": "high", "request_kind": "product",
                                 "goals": [{"kind": "product", "capability": "sdk_evidence",
                                            "entities": [], "conditions": {
                                                "feature": "network_device_removal_callback"}}]}}

    class Knowledge:
        sdk_catalog = _candidate_catalog()

    schema = extract_request_schema(question, adapter=Adapter(), knowledge=Knowledge())
    assert schema.goals[0].unmapped_condition_keys == ()


def test_uid_hotplug_requirement_binds_user_uid_to_reviewed_getter_symbol():
    row = next(json.loads(line) for line in Path("Knowledge_SDK/SDK_Knowledge.jsonl").read_text().splitlines()
               if "daq-sdk-host-net-uid-hotplug-016" in line)

    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role): return []
        def sections_for(self, role): return []

    requirement = {"id": "uid-hotplug", "capability": "sdk_evidence", "entities": [],
                   "conditions": {"api": "uid", "feature": "network_device_removal_callback",
                                  "language": "c++", "platform": "linux x86_64",
                                  "sdk": "OrbbecSDK", "sdk_release_date": "2026-09-29",
                                  "sdk_version": "2.9.0"}}
    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae",
                         requirements=[requirement])
    assert toolbox._sdk_usage_requirement_ids([row]) == ["uid-hotplug"]


@pytest.mark.parametrize("override", [
    {"sdk_release_date": "2026-09-23"},
    {"platform": "Windows x64"},
    {"api": "serial"},
])
def test_uid_hotplug_concept_cannot_cross_build_platform_or_api(override):
    row = next(json.loads(line) for line in Path("Knowledge_SDK/SDK_Knowledge.jsonl").read_text().splitlines()
               if "daq-sdk-host-net-uid-hotplug-016" in line)

    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role): return []
        def sections_for(self, role): return []

    conditions = {"api": "uid", "feature": "network_device_removal_callback",
                  "language": "c++", "platform": "linux x86_64", "sdk": "OrbbecSDK",
                  "sdk_release_date": "2026-09-29", "sdk_version": "2.9.0",
                  **override}
    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "usage", "capability": "sdk_evidence", "entities": [],
         "conditions": conditions},
        {"id": "device", "capability": "check_software_support", "entities": ["EGO"],
         "conditions": conditions},
    ])
    assert toolbox._sdk_usage_requirement_ids([row]) == []


@pytest.mark.parametrize("kb_id,conditions", [
    ("daq-sdk-android-fwlog-017", {"api": "exportFirmwareLogs", "platform": "android",
      "sdk": "Android Wrapper", "sdk_release_date": "2026-09-29"}),
    ("daq-sdk-android-sdcard-018", {"feature": "network_ego_sd_card_file_access",
      "language": "java", "platform": "android", "sdk": "Android Wrapper",
      "sdk_release_date": "2026-09-29", "sdk_version": "beta2.2.5",
      "task": "sd_card_file_info_and_local_download"}),
])
def test_real_dev_sdk_selectors_certify_scoped_usage_only(kb_id, conditions):
    row = next(json.loads(line) for line in Path("Knowledge_SDK/SDK_Knowledge.jsonl").read_text().splitlines()
               if kb_id in line)

    class Knowledge:
        release_id = "dev-sdk"
        def records_for(self, role): return []
        def sections_for(self, role): return []

    usage = {"id": "usage", "capability": "sdk_evidence", "entities": ["EGO"],
             "conditions": conditions}
    device = {"id": "device", "capability": "check_software_support",
              "entities": ["EGO"], "conditions": conditions}
    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae",
                         requirements=[usage, device])
    assert toolbox._sdk_usage_requirement_ids([row]) == ["usage"]


def _package(path: Path, member: str, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    from daq_fae.knowledge import sdk_catalog
    monkeypatch.setattr(sdk_catalog, "PACKAGES", {
        "host": {"glob": "host.tar.gz", "version": "2.9.0", "build": "20260929", "platform": "Linux"},
    })
    source = tmp_path / "source"
    _package(source / "host.tar.gz", "pkg/examples/sync.cpp",
             b"int main() {\n ob_capture_config_t cfg; cfg.enable_mask = setSyncMode();\n}\n")
    archive = tmp_path / "archive"
    create_archive(source, archive, "2026-10-15")
    directory = tmp_path / "catalog"
    directory.mkdir()
    (directory / "curated.json").write_text(json.dumps([{
        "kb_id": "sdk-sync", "package": "host", "sdk_layer": "host_cpp",
        "doc_type": "example", "title": "EGO 多设备同步", "question_patterns": ["怎么设置同步模式"],
        "answer": "调用 setSyncMode 前检查位图。", "symbols": ["setSyncMode"], "topics": ["sync"],
        "source_refs": [{"member_suffix": "examples/sync.cpp", "start_line": 1, "end_line": 3}],
    }]))
    records, manifest = build_catalog(directory / "curated.json", archive)
    (directory / "SDK_Knowledge.jsonl").write_bytes(_serialized(records))
    (directory / "SDK_Knowledge_manifest.json").write_text(json.dumps(manifest))
    return directory, archive


def test_catalog_source_binding_and_tamper(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    assert catalog.search("EGO 同步模式", "internal_fae")[0]["kb_id"] == "sdk-sync"
    assert {"ob_capture_config_t", "enable_mask"} <= set(catalog.records[0]["code_tokens"])
    assert catalog.search("EGO 同步模式", "channel") == []
    assert catalog.search("20260923 EGO 同步模式", "internal_fae") == []
    assert catalog.search("2026-09-23 EGO 同步模式", "internal_fae") == []
    records = (directory / "SDK_Knowledge.jsonl").read_text().replace("检查位图", "直接设置")
    (directory / "SDK_Knowledge.jsonl").write_text(records)
    with pytest.raises(ValueError, match="differs"):
        SdkCatalog.load(directory, archive)


def test_firmware_version_does_not_hide_sdk_usage_evidence(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    assert catalog.search("EGO 固件 1.1.8 怎么设置同步模式", "internal_fae")
    assert catalog.search("OrbbecSDK 1.1.8 怎么设置同步模式", "internal_fae") == []


def test_catalog_rejects_invalid_source_lines(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    curated = json.loads((directory / "curated.json").read_text())
    curated[0]["source_refs"][0]["end_line"] = 4
    (directory / "curated.json").write_text(json.dumps(curated))
    with pytest.raises(ValueError, match="line span"):
        build_catalog(directory / "curated.json", archive)


def test_catalog_rejects_symbol_outside_selected_source_spans(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    curated = json.loads((directory / "curated.json").read_text())
    curated[0]["symbols"].append("inventedApi")
    (directory / "curated.json").write_text(json.dumps(curated))
    with pytest.raises(ValueError, match="SDK symbol lacks source span"):
        build_catalog(directory / "curated.json", archive)


def test_sdk_tool_returns_usage_without_certifying_device_support(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae")
    result = toolbox.dispatch("sdk_evidence", {"query": "EGO 同步模式怎么设置"})
    assert result.status == "ok"
    assert result.content["device_support"] == "unknown"
    assert result.content["matched_requirement_ids"] == []
    assert result.sources and "source_refs" in result.sources[0]
    assert toolbox.dispatch("check_software_support", {"query": "EGO 同步模式"}).status == "not_found"


def test_sdk_tool_marks_example_build_as_unverified(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role): return []
        def sections_for(self, role): return []

    result = DaqToolBox(knowledge=Knowledge(), role="internal_fae").dispatch(
        "sdk_evidence", {"query": "EGO 同步模式怎么设置"})
    assert result.status == "ok"
    assert result.content["example_build_status"] == "unknown"


def test_sdk_usage_covers_only_usage_goal_in_dev(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirements = [
        {"id": "usage", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_version": "2.9.0"}, "evidence_class": "governed_required"},
        {"id": "support", "capability": "check_software_support", "entities": ["EGO"],
         "conditions": {"firmware_version": "1.1.9"}, "evidence_class": "governed_required"},
    ]
    toolbox = DaqToolBox(question="EGO 同步模式怎么设置", knowledge=Knowledge(),
                         role="internal_fae", requirements=requirements)
    result = toolbox.dispatch("sdk_evidence", {"query": "EGO 同步模式"})
    assert result.content["matched_requirement_ids"] == ["usage"]
    dev = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": requirements})
    dev.observe("sdk_evidence", result)
    assert dev.status == {"usage": "satisfied", "support": "unknown"}
    regular = DaqEvidencePolicy().begin({"requirements": requirements})
    regular.observe("sdk_evidence", result)
    assert regular.status == {"usage": "unknown", "support": "unknown"}


def test_sdk_build_date_is_a_supported_request_condition():
    question = "用 2026-09-29 的 OrbbecSDK 2.9.0 示例怎样设置 EGO 同步模式？"
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence",
                   "entities": ["EGO"], "conditions": {
                       "sdk_version": "2.9.0", "sdk_release_date": "2026-09-29"}}],
    })
    assert schema.goals[0].unmapped_condition_keys == ()


def test_sdk_task_context_requires_exact_source_checked_usage_task():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "capture", "capability": "sdk_evidence", "entities": [],
         "conditions": {"task": ["capture", "startup"], "language": "C", "sdk": "libobkit"}},
        {"id": "other", "capability": "sdk_evidence", "entities": [],
         "conditions": {"task": ["firmware_upgrade"], "sdk": "libobkit"}},
        {"id": "composite", "capability": "sdk_evidence", "entities": [],
         "conditions": {"feature": "camera_imu_audio_capture_init_start_order",
                        "language": "c", "sdk": "libobkit"}},
        {"id": "phrased_task", "capability": "sdk_evidence", "entities": [],
         "conditions": {"feature": "camera_imu_audio_capture",
                        "task": "initialization_and_start_order",
                        "language": "c", "sdk": "libobkit"}},
    ])
    row = {"sdk_layer": "device_c", "version": "20260909", "build": "20260909",
           "platform": "RK3576 AArch64 Linux", "doc_type": "example",
           "usage_tasks": ["capture", "startup"],
           "topics": ["camera", "imu", "audio"],
           "symbols": ["ob_capture_init", "ob_capture_start"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["capture", "composite", "phrased_task"]


def test_sdk_name_alias_can_match_package_usage_without_user_build_selector():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "capture", "capability": "sdk_evidence", "entities": [],
         "conditions": {"sdk_name": "libobkit", "language_binding": "C",
                        "task": ["capture", "startup"]}},
    ])
    row = {"sdk_layer": "device_c", "version": "20260909", "build": "20260909",
           "platform": "RK3576 AArch64 Linux", "doc_type": "example",
           "usage_tasks": ["capture", "startup"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["capture"]

    host = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "host", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_name": "OrbbecSDK", "language_binding": "C++"}},
    ])
    assert host._sdk_usage_requirement_ids([{**row, "sdk_layer": "host_cpp"}]) == ["host"]


def test_sdk_build_date_given_as_version_selects_exact_build():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "old", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_version": "2026-09-23", "api": "downloadSdcardFile",
                        "feature": "sd_card_file_service"}},
    ])
    row = {"sdk_layer": "host_cpp", "version": "2.9.0", "build": "20260923",
           "platform": "Linux x86_64", "doc_type": "example",
           "symbols": ["downloadSdcardFile"], "usage_tasks": ["sdcard_file_service"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["old"]


def test_calibration_output_names_are_checked_against_source_record():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "yaml", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"export_files": ["align_calib.yaml", "imu_calib.yaml"]}},
        {"id": "wrong", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"export_files": ["secret.yaml"]}},
    ])
    row = {"sdk_layer": "host_cpp", "version": "2.9.0", "build": "20260929",
           "platform": "Linux x86_64", "doc_type": "example",
           "outputs": ["align_calib.yaml", "imu_calib.yaml"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["yaml"]


def test_calibration_yaml_evidence_survives_separate_unknown_json_goal():
    catalog = _candidate_catalog()
    question = 'EGO C++ 示例导出对齐和 IMU 标定 YAML 时，还能导出外参标定 JSON 吗？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
            'conditions': {'language': 'cpp', 'export_files': [
                'alignment_yaml', 'imu_calibration_yaml',
                'extrinsic_calibration_json']},
        }],
    }, sdk_catalog=catalog)

    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirements = [
        {'id': goal.id, 'capability': goal.capability,
         'entities': list(goal.entities), 'conditions': goal.condition_map(),
         'unmapped_condition_keys': list(goal.unmapped_condition_keys),
         'reason': ('unmapped_condition_unverified'
                    if goal.unmapped_condition_keys else '')}
        for goal in schema.goals
    ]
    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=requirements,
    ).dispatch('sdk_evidence', {'query': 'EGO C++ calibration export'})
    known = next(goal.id for goal in schema.goals
                 if goal.condition_map().get('task') == 'calibration_export')
    unknown = next(goal.id for goal in schema.goals
                   if 'extrinsic_calibration_json' in str(goal.condition_map()))
    assert result.status == 'ok'
    assert {row['kb_id'] for row in result.content['matches']} == {
        'daq-sdk-host-calib-002'}
    assert result.content['matched_requirement_ids'] == [known]
    assert unknown not in result.content['matched_requirement_ids']


def test_sdk_source_filename_matches_only_hashed_member_not_generated_output():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "sample", "capability": "sdk_evidence", "entities": [],
         "conditions": {"sdk": "OrbbecSDK", "sdk_release_date": "2026-09-29",
                        "source_files": ["magnetometer.cpp"]}},
    ])
    row = {"sdk_layer": "host_cpp", "version": "2.9.0", "build": "20260929",
           "platform": "Linux x86_64", "doc_type": "example", "outputs": [],
           "source_refs": [{"member": "pkg/examples/src/6.ego/magnetometer.cpp",
                            "member_sha256": "a" * 64}]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["sample"]
    assert toolbox._sdk_usage_requirement_ids([{**row, "source_refs": [
        {"member": "pkg/examples/src/6.ego/other.cpp", "member_sha256": "a" * 64}]}]) == []


def test_sdk_release_is_immutable_and_reversible(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    answer = "调用 setSyncMode 前检查位图。"
    import hashlib
    replay = tmp_path / "results.jsonl"
    replay.write_text(json.dumps({"case": {"id": "usage"},
        "identity": {"sdk_catalog_sha256": catalog.manifest["records_sha256"],
                     "suite_file_sha256": "c" * 64,
                     "publication_eligible": True,
                     "knowledge_binding": "source_bound_release",
                     "runtime_release": "daq-tested",
                     "upstream_revision": "b" * 40},
        "turns": [{"done": {"answer": answer, "outcome": "resolved",
                            "trace_id": "d" * 32, "fallback_used": False},
                    "integrity": {"stage_lifecycle_closed": True}}]}) + "\n")
    report = {"environment": "dev", "reviewer": "Codex",
              "catalog_sha256": catalog.manifest["records_sha256"],
              "results_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
              "suite_sha256": "c" * 64,
              "cases": [{"id": "usage", "passed": True,
                         "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                         "trace_id": "d" * 32, "outcome": "resolved",
                         "fallback_used": False, "review_note": "Source matched."}]}
    root = tmp_path / "published"
    with pytest.raises(ValueError, match="runtime identity"):
        stage_sdk_release(root, directory, archive, report, replay_results=replay,
                          runtime_release="daq-untested", upstream_sha="b" * 40)
    with pytest.raises(ValueError, match="runtime identity"):
        stage_sdk_release(root, directory, archive, report, replay_results=replay,
                          runtime_release="daq-tested", upstream_sha="a" * 40)
    original_replay = json.loads(replay.read_text())
    for identity_change in (
        {"publication_eligible": None},
        {"publication_eligible": False},
        {"knowledge_binding": None},
        {"knowledge_binding": "unknown_release_binding"},
        {"knowledge_binding": "dev_code_preview_of_immutable_release"},
    ):
        mutated = json.loads(json.dumps(original_replay))
        for key, value in identity_change.items():
            if value is None:
                mutated["identity"].pop(key)
            else:
                mutated["identity"][key] = value
        replay.write_text(json.dumps(mutated) + "\n")
        report["results_sha256"] = hashlib.sha256(replay.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="identity mismatch"):
            stage_sdk_release(root, directory, archive, report, replay_results=replay,
                              runtime_release="daq-tested", upstream_sha="b" * 40)
    replay.write_text(json.dumps(original_replay) + "\n")
    report["results_sha256"] = hashlib.sha256(replay.read_bytes()).hexdigest()
    release_id = stage_sdk_release(root, directory, archive, report, replay_results=replay,
                                   runtime_release="daq-tested", upstream_sha="b" * 40)
    assert load_active_sdk_release(root, runtime_release="daq-tested",
                                   upstream_sha="b" * 40) is None
    activate_sdk_release(root, release_id)
    published = load_active_sdk_release(root, runtime_release="daq-tested",
                                        upstream_sha="b" * 40)
    assert published.published is True
    assert published.release_id == release_id
    assert published.search("EGO 同步模式", "internal_fae")
    assert published.search("EGO 同步模式", "channel") == []
    with pytest.raises(ValueError, match="runtime"):
        load_active_sdk_release(root, runtime_release="wrong", upstream_sha="b" * 40)
    manifest = root / "releases" / release_id / "manifest.json"
    manifest.chmod(0o600)
    manifest.write_text(manifest.read_text().replace("调用", "直接"))
    with pytest.raises(ValueError, match="digest"):
        load_active_sdk_release(root, runtime_release="daq-tested", upstream_sha="b" * 40)


def test_published_sdk_source_satisfies_regular_evidence_policy(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    catalog.published = True
    catalog.release_id = "a" * 64

    class Knowledge:
        release_id = "main-release"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirement = {"id": "usage", "capability": "sdk_evidence",
                   "entities": ["EGO"], "conditions": {"sdk_version": "2.9.0"}}
    result = DaqToolBox(question="EGO 同步模式怎么设置", knowledge=Knowledge(),
                        role="internal_fae", requirements=[requirement]).dispatch(
                            "sdk_evidence", {"query": "EGO 同步模式"})
    assert result.sources[0]["type"] == "daq_governed_sdk"
    assert result.sources[0]["release_id"] == catalog.release_id
    session = DaqEvidencePolicy().begin({"requirements": [requirement]})
    session.observe("sdk_evidence", result)
    assert session.status["usage"] == "satisfied"


def test_device_side_package_aliases_can_cover_usage_without_image_support():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "usage", "capability": "sdk_evidence", "entities": ["四目 EGO"],
         "conditions": {"sdk": "libobkit", "platform": "device_side"}},
        {"id": "support", "capability": "check_software_support", "entities": ["四目 EGO"],
         "conditions": {"image_version": "any"}},
    ])
    matches = [{"sdk_layer": "device_c", "version": "20260909",
                "build": "20260909", "platform": "RK3576 AArch64 Linux",
                "doc_type": "build"}]
    assert toolbox._sdk_usage_requirement_ids(matches) == ["usage"]
    schema = DaqRequestSchema.from_candidate(
        "四目 EGO 设备端 libobkit 示例怎么编译？",
        {"confidence": "high", "answer_language": "zh", "request_kind": "product",
         "goals": [{"kind": "product", "capability": "sdk_evidence",
                    "entities": ["四目 EGO"],
                    "conditions": {"sdk": "libobkit", "platform": "device_side"}}]})
    assert schema.goals[0].unmapped_condition_keys == ()


def test_ios_framework_question_maps_only_source_checked_package_goals():
    catalog = _candidate_catalog()
    question = ('2026-09-29 OrbbecSDK 2.9.0 iOS 包应如何嵌入 framework？'
                '这个包是否包含 Simulator 产物、能否据此确认 EGO 固件兼容？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'sdk_release_date': '2026-09-29', 'platform': 'ios',
                            'task': 'embed_framework'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                            'sdk_release_date': '2026-09-29', 'platform': 'ios',
                            'feature': 'simulator_artifacts'}},
        ]}, sdk_catalog=catalog)
    assert [goal.unmapped_condition_keys for goal in schema.goals] == [(), ()]
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{'id': goal.id, 'capability': goal.capability,
                                    'entities': list(goal.entities),
                                    'conditions': dict(goal.conditions)}
                                   for goal in schema.goals])
    result = box.dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert set(result.content['matched_requirement_ids']) == {goal.id for goal in schema.goals}
    assert result.content['device_support'] == 'unknown'
    assert not catalog.grounds_selector('OrbbecSDK 2.9.0 iOS 如何嵌入 framework？',
                                       'feature', 'simulator_artifacts')
    assert not catalog.grounds_selector('Android OrbbecSDK 如何嵌入 framework？',
                                       'task', 'embed_framework')


def test_linux_x64_package_selector_keeps_source_platform_identity():
    catalog = _candidate_catalog()
    question = 'EgoLowBle 1.1.8 Linux x64 包提供什么 demo 和构建资料？预编译 demo 是否必定包含？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'linux_x64'}}]}, sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{'id': 'package', 'capability': 'sdk_evidence',
                                    'entities': [], 'conditions': dict(schema.goals[0].conditions)}])
    result = box.dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert 'package' in result.content['matched_requirement_ids']


def test_windows_ble_cpp_link_and_dll_usage_is_separate_from_device_support():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 包中，C++ 应用应怎样链接库和放置 DLL？'
                '这代表具体 EGO 设备通过测试吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64', 'language': 'cpp',
                            'feature': 'library_linking_and_dll_placement'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO'],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64'}},
        ]}, sdk_catalog=catalog)
    usage = next(goal for goal in schema.goals if goal.capability == 'sdk_evidence')
    support = next(goal for goal in schema.goals
                   if goal.capability == 'check_software_support')
    assert usage.unmapped_condition_keys == ()
    assert support.unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': dict(usage.conditions)},
                            {'id': 'support', 'capability': 'check_software_support',
                             'entities': ['EGO'], 'conditions': dict(support.conditions)},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']
    assert result.content['device_support'] == 'unknown'


def test_windows_ble_link_libraries_and_deploy_dlls_descriptor_is_grounded():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 包中，C++ 应用应怎样链接库和放置 DLL？'
                '这代表具体 EGO 设备通过测试吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64', 'language': 'c++',
                            'task': 'link_libraries_and_deploy_dlls'}},
        ],
    }, sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                        requirements=[{'id': 'usage', 'capability': 'sdk_evidence',
                                       'entities': [],
                                       'conditions': schema.goals[0].condition_map()}]).dispatch(
                                           'sdk_evidence', {'query': question})
    assert result.content['matched_requirement_ids'] == ['usage']


def test_windows_ble_dll_integration_descriptor_keeps_sdk_fact_covered():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 的 DLL 如何集成？'
                '我的 EGO 固件也叫 1.1.8，这两个版本号相同能说明蓝牙功能已兼容吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64', 'task': 'dll_integration'}},
        ],
    }, sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()


def test_windows_ble_singular_link_library_and_deploy_dll_is_grounded():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 包中，C++ 应用应怎样链接库和放置 DLL？'
                '这代表具体 EGO 设备通过测试吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64', 'language': 'c++',
                            'task': 'link_library_and_deploy_dll'}},
        ]}, sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()


def test_ble_firmware_mixed_goal_normalizes_bluetooth_and_windows_spelling():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 的 DLL 如何集成？我的 EGO 固件也叫 '
                '1.1.8，这两个版本号相同能说明蓝牙功能已兼容吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'feature': 'bluetooth', 'platform': 'windows-x64',
                            'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'task': 'dll_integration'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO'],
             'conditions': {'feature': 'bluetooth',
                            'firmware_version': '1.1.8',
                            'sdk_version': '1.1.8'}},
        ]}, sdk_catalog=catalog)
    usage, support = schema.goals
    assert usage.condition_map()['platform'] == 'windows_x64'
    assert usage.condition_map()['feature'] == 'ble'
    assert usage.unmapped_condition_keys == ()
    assert support.unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': usage.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.content['matched_requirement_ids'] == ['usage']


def test_real_ble_firmware_mixed_schema_keeps_dll_usage_evidence():
    catalog = _candidate_catalog()
    question = ('EgoLowBle 1.1.8 Windows x64 的 DLL 如何集成？我的 EGO 固件也叫 '
                '1.1.8，这两个版本号相同能说明蓝牙功能已兼容吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
             'conditions': {'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                            'platform': 'windows_x64', 'task': 'integration'}},
            {'kind': 'product', 'capability': 'check_software_support',
             'entities': ['EGO'], 'conditions': {'feature': 'bluetooth'}},
        ]}, sdk_catalog=catalog)
    usage = schema.goals[0]
    assert usage.unmapped_condition_keys == ()
    assert usage.condition_map()['task'] == 'dll_integration'
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': list(usage.entities),
                             'conditions': usage.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']
    assert result.content['device_support'] == 'unknown'
    assert {source['source_id'] for source in result.sources} == {
        'daq-sdk-ble-windows-install-020'}


def test_reviewed_entity_ids_preserve_sdk_package_usage_scope():
    catalog = _candidate_catalog()
    host = next(row for row in catalog.records if row['kb_id'] ==
                'daq-sdk-host-sync-001')
    device = next(row for row in catalog.records if row['kb_id'] ==
                  'daq-sdk-device-build-012')
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(knowledge=Knowledge(), role='internal_fae', requirements=[
        {'id': 'ego_host', 'capability': 'sdk_evidence',
         'entities': ['entity:ego-1600'],
         'conditions': {'sdk': 'OrbbecSDK', 'sdk_release_date': '20260929',
                        'feature': 'multi_device_sync_mode_check_and_set'}},
        {'id': 'pro_device', 'capability': 'sdk_evidence',
         'entities': ['entity:ego-pro'],
         'conditions': {'sdk': 'libobkit', 'task': 'compile_and_deploy'}},
    ])
    assert box._sdk_usage_requirement_ids([host]) == ['ego_host']
    assert box._sdk_usage_requirement_ids([device]) == ['pro_device']
    assert box._sdk_usage_requirement_ids([{**host, 'sdk_layer': 'device_c'}]) == []


def test_real_host_sync_english_tool_paraphrase_recovers_user_question_evidence():
    catalog = _candidate_catalog()
    question = ('用 2026-09-29 的 OrbbecSDK 2.9.0 C++ 示例，EGO '
                '多设备主从同步模式应该怎样检查并设置？')
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(question=question, knowledge=Knowledge(),
                     role='internal_fae', requirements=[
                         {'id': 'sync', 'capability': 'sdk_evidence',
                          'entities': ['EGO'], 'conditions': {
                              'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                              'sdk_release_date': '2026-09-29',
                              'language': 'c++',
                              'feature': 'multi_device_sync_mode_check_and_set'}},
                     ])
    query = 'OrbbecSDK C++ example multi-device master-slave sync mode check and set EGO'
    assert [row['kb_id'] for row in catalog.search(query, 'internal_fae')] != [
        'daq-sdk-host-sync-001']
    result = box.dispatch('sdk_evidence', {'query': query})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['sync']
    assert {source['source_id'] for source in result.sources} == {
        'daq-sdk-host-sync-001'}


def test_sdk_evidence_extra_device_fields_cannot_switch_to_support_lookup():
    catalog = _candidate_catalog()
    question = ('同是 2026-09-29，Android Java Wrapper 的 exportFirmwareLogs 与 '
                'Linux x86_64 C++ OrbbecSDK 的 startFirmwareLogExport/'
                'stopFirmwareLogExport，在调用和完成处理上分别有什么依据？')
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae')
    result = box.dispatch('sdk_evidence', {
        'query': 'exportFirmwareLogs', 'entity': 'orbbecsdk_java_wrapper',
        'software': 'orbbecsdk_java_wrapper', 'platform': 'android',
        'version': '2026-09-29', 'hardware_revision': 'unknown',
        'connection_mode': 'unknown', 'capability': 'sdk_evidence'})
    assert result.status == 'ok'
    assert result.content['evidence_scope'] == 'sdk_usage_only'
    assert result.content['device_support'] == 'unknown'


def test_libobkit_build_and_deploy_goal_uses_four_eye_identity():
    catalog = _candidate_catalog()
    question = ('四目 EGO 设备端 libobkit 示例怎么编译和部署？'
                '它能和任意整机镜像搭配吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
             'conditions': {'sdk': 'libobkit', 'task': 'build_and_deploy',
                            'platform': 'device_side'}}]}, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.entities == ('四目 EGO',)
    assert goal.unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': list(goal.entities),
                             'conditions': goal.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.content['matched_requirement_ids'] == ['usage']


def test_libobkit_compile_and_deploy_model_task_is_user_grounded():
    catalog = _candidate_catalog()
    question = ('四目 EGO 设备端 libobkit 示例怎么编译和部署？'
                '它能和任意整机镜像搭配吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': ['EGO'],
             'conditions': {'sdk': 'libobkit', 'task': 'compile_and_deploy',
                            'platform': 'device_side'}}]}, sdk_catalog=catalog)
    assert schema.goals[0].entities == ('四目 EGO',)
    assert schema.goals[0].unmapped_condition_keys == ()


def test_libobkit_build_and_run_sample_maps_to_reviewed_build_record():
    catalog = _candidate_catalog()
    question = ('EGO Pro 设备端 libobkit 示例在什么环境编译，'
                'TOOLCHAIN_DIR 和运行时 lib 路径怎么处理？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['EGO Pro'],
             'conditions': {'sdk': 'libobkit', 'platform': 'device_side',
                            'task': 'build_and_run_sample'}},
        ],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': list(goal.entities),
                             'conditions': goal.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']
    assert 'daq-sdk-device-build-012' in {
        source['source_id'] for source in result.sources
    }


def test_libobkit_single_compile_label_uses_exact_reviewed_symbol_anchor():
    catalog = _candidate_catalog()
    question = ('EGO Pro 设备端 libobkit 示例在什么环境编译，'
                'TOOLCHAIN_DIR 和运行时 lib 路径怎么处理？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence',
            'entities': ['EGO Pro'],
            'conditions': {'sdk': 'libobkit', 'platform': 'device_side',
                           'task': 'compile'},
        }],
    }, sdk_catalog=catalog)
    goal = schema.goals[0]
    assert goal.condition_map()['task'] == 'build'
    assert goal.unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': list(goal.entities),
                             'conditions': goal.condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert 'daq-sdk-device-build-012' in {
        source['source_id'] for source in result.sources
    }


def test_unspoken_api_in_comma_list_stays_unmapped():
    schema = DaqRequestSchema.from_candidate('Android Wrapper 的 SAVING 是什么？', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'api': 'SAVING, fabricatedDeviceApi'}}]})
    assert 'api' in schema.goals[0].unmapped_condition_keys


def test_ble_windows_model_task_matches_reviewed_link_and_dll_scope():
    question = ('EgoLowBle 1.1.8 Windows x64 包中，C++ 应用应怎样链接库和放置 DLL？'
                '这代表具体 EGO 设备通过测试吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [{
            'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
            'conditions': {'language': 'c++', 'platform': 'windows x64',
                           'sdk': 'EgoLowBle', 'sdk_version': '1.1.8',
                           'task': 'link_library_and_place_dll'},
        }],
    }, sdk_catalog=_candidate_catalog())
    assert schema.goals[0].unmapped_condition_keys == ()


def test_android_record_state_two_api_selectors_match_one_reviewed_record():
    catalog = _candidate_catalog()
    question = ('Android Wrapper 中 SAVING 和 RECORD_SAVED 有何区别？'
                'setEgoStateCallback 与 setEgoRecordingResultCallback 各适合什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {'api': ['setEgoStateCallback',
                                    'setEgoRecordingResultCallback'],
                            'feature': ['SAVING', 'RECORD_SAVED'],
                            'platform': 'android', 'sdk': 'android_wrapper'}}]},
        sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [],
                             'conditions': dict(schema.goals[0].conditions)},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']


def test_android_and_host_firmware_log_goals_keep_their_sdk_layers():
    catalog = _candidate_catalog()
    question = ('同是 2026-09-29，Android Java Wrapper 的 exportFirmwareLogs 与 '
                'Linux x86_64 C++ OrbbecSDK 的 startFirmwareLogExport/'
                'stopFirmwareLogExport，在调用和完成处理上分别有什么依据？')
    conditions = [
        {'api': 'exportFirmwareLogs', 'language': 'java', 'platform': 'android',
         'sdk': 'OrbbecSDK Android Java Wrapper',
         'sdk_release_date': '2026-09-29'},
        {'api': ['startFirmwareLogExport', 'stopFirmwareLogExport'],
         'language': 'cpp', 'platform': 'linux_x86_64', 'sdk': 'OrbbecSDK',
         'sdk_release_date': '2026-09-29'},
    ]
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': [], 'conditions': item} for item in conditions]},
        sdk_catalog=catalog)
    assert [goal.unmapped_condition_keys for goal in schema.goals] == [(), ()]
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'android', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': conditions[0]},
                            {'id': 'host', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': conditions[1]},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['android', 'host']


def test_sdk_call_and_completion_phrase_is_not_an_api_feature_selector():
    catalog = _candidate_catalog()
    question = ('同是 2026-09-29，Android Java Wrapper 的 exportFirmwareLogs 与 '
                'Linux x86_64 C++ OrbbecSDK 的 startFirmwareLogExport/'
                'stopFirmwareLogExport，在调用和完成处理上分别有什么依据？')
    common = {'feature': 'invocation_and_completion_handling',
              'sdk_release_date': '2026-09-29'}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'medium', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {**common, 'sdk': 'android_java_wrapper',
                            'platform': 'android', 'language': 'java',
                            'api': 'exportFirmwareLogs'}},
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {**common, 'sdk': 'orbbecsdk',
                            'platform': 'linux_x86_64', 'language': 'c++',
                            'api': ['startFirmwareLogExport',
                                    'stopFirmwareLogExport']}},
        ]}, sdk_catalog=catalog)
    assert all('feature' not in goal.condition_map() for goal in schema.goals)
    assert all(not goal.unmapped_condition_keys for goal in schema.goals)


def test_android_network_sdcard_usage_goal_can_match_network_source_only():
    catalog = _candidate_catalog()
    question = ('2026-09-29 Android Wrapper beta2.2.5 的 Java API，网络 EGO 的 '
                'SD 卡文件信息与本地下载该怎么调用？这证明具体固件支持吗？')
    conditions = {'connection': 'network',
                  'feature': ['sd_card_file_info', 'local_download'],
                  'language': 'java', 'platform': 'android',
                  'sdk': 'Android Wrapper', 'sdk_release_date': '2026-09-29',
                  'sdk_version': 'beta2.2.5'}
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': ['EGO'], 'conditions': conditions}]},
        sdk_catalog=catalog)
    assert schema.goals[0].unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': ['EGO'], 'conditions': conditions},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['usage']


def test_android_record_state_comma_separated_public_symbols_are_normalized():
    catalog = _candidate_catalog()
    question = ('Android Wrapper 中 SAVING 和 RECORD_SAVED 有何区别？'
                'setEgoStateCallback 与 setEgoRecordingResultCallback 各适合什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
             'conditions': {
                 'api': ('setEgoStateCallback, setEgoRecordingResultCallback, '
                         'SAVING, RECORD_SAVED'),
                 'platform': 'android', 'sdk': 'Android Wrapper'}}]},
        sdk_catalog=catalog)
    assert schema.goals[0].condition_map()['api'] == [
        'setEgoStateCallback', 'setEgoRecordingResultCallback',
        'SAVING', 'RECORD_SAVED']
    assert schema.goals[0].unmapped_condition_keys == ()
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': 'usage', 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': schema.goals[0].condition_map()},
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.content['matched_requirement_ids'] == ['usage']


def test_firmware_log_mixed_model_conditions_keep_both_sdk_goals():
    catalog = _candidate_catalog()
    question = ('同是 2026-09-29，Android Java Wrapper 的 exportFirmwareLogs 与 '
                'Linux x86_64 C++ OrbbecSDK 的 startFirmwareLogExport/'
                'stopFirmwareLogExport，在调用和完成处理上分别有什么依据？')
    common = {'sdk_release_date': '2026-09-29', 'steps': [],
              'task': 'firmware_log_export'}
    raw = [
        {**common, 'api': 'exportFirmwareLogs', 'language': 'java',
         'platform': 'android', 'sdk': 'android_java_wrapper'},
        {**common, 'api': 'startFirmwareLogExport', 'language': 'c++',
         'platform': 'linux_x86_64', 'sdk': 'orbbecsdk'},
        {**common, 'api': 'stopFirmwareLogExport', 'language': 'c++',
         'platform': 'linux_x86_64', 'sdk': 'orbbecsdk'},
    ]
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'sdk_evidence',
             'entities': [], 'conditions': item} for item in raw]},
        sdk_catalog=catalog)
    assert all('steps' not in goal.condition_map() for goal in schema.goals)
    assert all(goal.unmapped_condition_keys == () for goal in schema.goals)
    class Knowledge:
        release_id = 'dev-sdk'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=[
                            {'id': str(index), 'capability': 'sdk_evidence',
                             'entities': [], 'conditions': goal.condition_map()}
                            for index, goal in enumerate(schema.goals)
                        ]).dispatch('sdk_evidence', {'query': question})
    assert result.content['matched_requirement_ids'] == ['0', '1', '2']

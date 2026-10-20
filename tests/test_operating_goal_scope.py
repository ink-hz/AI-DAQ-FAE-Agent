"""Operating evidence must follow each user-authored request scope."""

from daq_fae.domain_tools import DaqToolBox


def _formal_knowledge():
    source = {'path': 'reviewed.md', 'sha256': 'a' * 64,
              'locator': {'kind': 'lines', 'start': 1, 'end': 3}}
    entities = [
        {'id': 'entity:ego-pro', 'kind': 'entity', 'status': 'verified',
         'scope': {'product': 'ego-pro'},
         'data': {'name': 'EGO Pro', 'entity_type': 'device'},
         'source_refs': [source]},
        {'id': 'entity:wristcam', 'kind': 'entity', 'status': 'verified',
         'scope': {'product': 'wristcam'},
         'data': {'name': 'WristCam', 'entity_type': 'device'},
         'source_refs': [source]},
        {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
         'scope': {'product': 'hub'},
         'data': {'name': 'HUB', 'entity_type': 'device'},
         'source_refs': [source]},
    ]
    topology = {
        'id': 'topology:pro-single', 'kind': 'topology', 'status': 'verified',
        'scope': {'product': 'ego-pro', 'mode': 'standalone'},
        'data': {'members': ['entity:ego-pro'], 'roles': {}, 'connections': [],
                 'power': {}, 'platform': 'standalone', 'sync_target': 'none',
                 'storage': 'TF card', 'retrieval_aliases': ['单机 TF 卡录制'],
                 'evidence_conditions': {'host_connection': 'none'}},
        'source_refs': [source],
    }
    procedure = {
        'id': 'procedure:pro-single', 'kind': 'procedure', 'status': 'verified',
        'scope': {'product': 'ego-pro', 'mode': 'standalone'},
        'data': {'topology_id': topology['id'], 'task': 'local_recording',
                 'task_intents': ['recording', 'local_recording'],
                 'retrieval_aliases': ['单机 TF 卡录制'], 'steps': ['开机', '停止'],
                 'evidence_conditions': {'host_connection': 'none',
                                         'standalone': True,
                                         'recording_mode': 'local_tf_card',
                                         'storage': 'tf_card',
                                         'steps': ['power_on', 'start_recording',
                                                   'stop_recording'],
                                         'indicators': ['voice_prompt', 'green_led']}},
        'source_refs': [source],
    }

    class Knowledge:
        is_operator_pro_v2 = True
        release_id = 'test-formal'

        def records_for(self, role):
            return [*entities, topology, procedure] if role == 'internal_fae' else []

        def sections_for(self, role):
            return []

        def conflict_notices_for(self, role):
            return []

        def operating_veto_entity_records(self):
            return []

    return Knowledge()


def test_stop_recording_step_retrieves_scoped_procedure_as_reference():
    result = DaqToolBox(
        question='图片能证明 EGO Pro 已停止录制且 TF 卡文件完整吗？',
        knowledge=_formal_knowledge(), role='internal_fae',
        requirements=[{'id': 'stop', 'capability': 'lookup_procedure',
                       'entities': ['EGO Pro'],
                       'conditions': {'steps': ['stop_recording']}}],
    ).dispatch('lookup_procedure', {'task': 'stop_recording',
                                    'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['stop']
    assert result.content['matches'][0]['data']['evidence_conditions']['standalone'] is True


def test_single_procedure_survives_separate_combination_question():
    question = ('EGO Pro 单机 TF 卡录制的开机和停止步骤是什么？'
                '如果再加两台 WristCam 经 HUB 连接电脑，这套步骤能保证三路同步吗？')
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae').dispatch('lookup_procedure', {
                            'task': 'local_recording', 'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'procedure:pro-single']


def test_combination_only_question_cannot_borrow_model_written_single_query():
    question = 'EGO Pro 加两台 WristCam 经 HUB 连接电脑，能按单机 TF 卡流程三路同步吗？'
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae').dispatch('lookup_procedure', {
                            'task': 'local_recording', 'entity': 'EGO Pro'})
    assert result.status == 'not_found'


def test_standalone_recording_failure_question_recognizes_single_procedure():
    question = ('EGO Pro 单机录制按停止键后绿灯常亮，但语音没说数据已保存；'
                '可以对客户说已保存吗？')
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae').dispatch('lookup_procedure', {
                            'task': '单机录制 停止录制 数据保存 语音提示 灯态',
                            'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'procedure:pro-single']


def test_english_standalone_recording_paraphrase_retrieves_reviewed_checks():
    question = ('客户说 EGO Pro 录制时看到了红灯。能把红灯解释成文件保存完成吗？'
                '已审单机流程里实际有哪些可核对的完成检查点？')
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae').dispatch('lookup_procedure', {
                            'task': 'standalone recording', 'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'procedure:pro-single']
    for task in ('standalone recording on SSD', 'standalone red LED meaning'):
        rejected = DaqToolBox(
            question=question, knowledge=_formal_knowledge(),
            role='internal_fae').dispatch('lookup_procedure', {
                'task': task, 'entity': 'EGO Pro'})
        assert rejected.status == 'not_found'


def test_generic_lamp_state_and_elliptical_recording_steps_are_source_covered():
    from daq_fae.request_schema import DaqRequestSchema
    from daq_fae.task_context import prepare_turn
    question = ('EGO Pro 不接电脑，插 TF 卡单机录制，开机、开始、停止各怎么操作？'
                '每步要看什么灯态和语音？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'], 'conditions': {
                'host_connection': 'none', 'standalone': True,
                'recording_mode': 'local_tf_card',
                'steps': ['power_on', 'start_recording', 'stop_recording'],
                'indicators': ['led_state', 'voice_prompt'],
            },
        }],
    })
    goal = schema.goals[0]
    assert not goal.unmapped_condition_keys
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'lookup_procedure', {'task': 'local_recording',
                                                 'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == [
        plan.requirements[0]['id']]


def test_procedure_button_action_is_covered_by_reviewed_steps_not_indicators():
    from daq_fae.request_schema import DaqRequestSchema
    from daq_fae.task_context import prepare_turn

    question = ('EGO Pro 不接电脑，用本机 TF 卡录制，'
                '开机到停止的按键和灯态是什么？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'], 'conditions': {
                'host_connection': 'none', 'recording_mode': 'local_tf_card',
                'steps': ['power_on', 'start_recording', 'stop_recording'],
                'indicators': ['button', 'led_state'],
            },
        }],
    })
    goal = schema.goals[0]
    assert goal.condition_map()['indicators'] == ['led_state']
    assert goal.unmapped_condition_keys == ()
    plan = prepare_turn(question, request_schema=schema)
    result = DaqToolBox(question=question, knowledge=_formal_knowledge(),
                        role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'lookup_procedure', {'task': 'local_recording',
                                                 'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == [
        plan.requirements[0]['id']]


def test_procedure_button_without_requested_steps_stays_unmapped():
    from daq_fae.request_schema import DaqRequestSchema

    schema = DaqRequestSchema.from_candidate(
        'EGO Pro 单机录制时用哪个按键？', {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'],
                'conditions': {'indicators': ['button']},
            }],
        })
    assert schema.goals[0].unmapped_condition_keys == ('indicators',)


def test_recording_checkpoint_anomaly_keeps_independent_risk_obligation():
    from daq_fae.request_schema import DaqRequestSchema

    first_question = 'Android Java 录制状态回调显示 SAVING，是否表示文件已经保存完了？'
    observation = ('另外 EGO Pro 单机 TF 卡录制时，停止后绿灯常亮'
                   '但没听见“数据已保存”。')
    procedure = {
        'kind': 'product', 'capability': 'lookup_procedure',
        'entities': ['EGO Pro'],
        'conditions': {'standalone': True, 'recording_mode': 'local_tf_card',
                       'steps': ['stop_recording'],
                       'indicators': ['green_led', 'voice_prompt']},
    }
    observed = DaqRequestSchema.from_candidate(
        observation, {'confidence': 'high', 'goals': [procedure]},
        prior_user_context=first_question)
    assert [goal.capability for goal in observed.goals] == [
        'lookup_procedure', 'risk']
    assert observed.goals[1].entities == ('EGO Pro',)

    followup = ('综合这两条线索，我能向客户确认完整落盘吗？'
                '下一步最小核对是什么？')
    sdk = {'kind': 'product', 'capability': 'sdk_evidence', 'entities': [],
           'conditions': {'language': 'java', 'platform': 'android',
                          'task': 'recording_state'}}
    combined = DaqRequestSchema.from_candidate(
        followup, {'confidence': 'high', 'goals': [sdk, procedure]},
        prior_user_context=first_question + observation)
    assert {goal.capability for goal in combined.goals} == {
        'sdk_evidence', 'lookup_procedure', 'risk'}
    assert next(goal for goal in combined.goals
                if goal.capability == 'risk').entities == ('EGO Pro',)


def test_plain_recording_steps_do_not_invent_risk_obligation():
    from daq_fae.request_schema import DaqRequestSchema

    question = 'EGO Pro 单机录制时，停止录制要按什么键？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'],
            'conditions': {'steps': ['stop_recording']},
        }],
    })
    assert [goal.capability for goal in schema.goals] == ['lookup_procedure']

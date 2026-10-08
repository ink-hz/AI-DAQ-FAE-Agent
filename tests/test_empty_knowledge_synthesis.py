from daq_fae.empty_knowledge_synthesis import refine_empty_release_answer


def test_missing_catalog_gets_direct_user_facing_abstention_with_visible_transform():
    done = {'answer': '本轮已核查 catalog 工具。相关建议：提供参数。',
            'outcome': 'safe_abstained', 'sources': [], 'fallback_used': False,
            'evidence_policy': {'requirement_status': {'catalog-1': 'missing'}}}
    refine_empty_release_answer(done, planned_capabilities=['catalog'],
                                knowledge_release='empty-dev-v0')
    assert done['answer'] == (
        '目前缺少已审核的数采产品目录，无法可靠列出设备清单。'
        '请提供或确认最新的正式产品目录及版本，核实后再给出清单。')
    assert done['fallback_used'] is True
    assert done['fallback_reason'] == 'empty_release_synthesis_template'
    assert done['synthesis_mode'] == 'deterministic_empty_release'
    assert len(done['synthesis_original_sha256']) == 64


def test_transform_never_rewrites_evidence_or_runtime_failure():
    for outcome, sources, states in (
        ('resolved', [], {'one': 'missing'}),
        ('safe_abstained', [{'source_id': 'verified'}], {'one': 'missing'}),
        ('safe_abstained', [], {'one': 'satisfied'}),
        ('provider_unavailable', [], {'one': 'missing'}),
    ):
        done = {'answer': 'original', 'outcome': outcome, 'sources': sources,
                'evidence_policy': {'requirement_status': states}}
        refine_empty_release_answer(done, planned_capabilities=['catalog'],
                                    knowledge_release='empty-dev-v0')
        assert done['answer'] == 'original'
        assert 'synthesis_mode' not in done
    attachment = {'answer': 'attachment missing', 'outcome': 'safe_abstained',
                  'sources': [], 'evidence_policy': {
                      'requirement_status': {'user_image': 'missing'}}}
    refine_empty_release_answer(attachment,
                                planned_capabilities=['analyze_image', 'lookup_spec'],
                                knowledge_release='empty-dev-v0')
    assert attachment['answer'] == 'attachment missing'


def test_spec_abstention_does_not_ask_for_an_already_named_model():
    done = {'answer': '资料不足', 'outcome': 'safe_abstained', 'sources': [],
            'evidence_policy': {'requirement_status': {'spec': 'missing'}}}
    refine_empty_release_answer(done, planned_capabilities=['lookup_spec'],
                                knowledge_release='empty-dev-v0')
    assert '请提供具体型号' not in done['answer']
    assert '正式规格书' in done['answer']


def test_multi_capability_abstention_keeps_each_missing_conclusion_visible():
    done = {'answer': '长篇内部检索过程', 'outcome': 'safe_abstained', 'sources': [],
            'evidence_policy': {'requirement_status': {
                'selection': 'missing', 'software': 'missing'}}}
    refine_empty_release_answer(
        done, planned_capabilities=['selection', 'check_software_support'],
        knowledge_release='empty-dev-v0',
    )
    assert '选型依据' in done['answer']
    assert '兼容资料' in done['answer']
    assert '推荐设备组合' in done['answer']
    assert '确认兼容关系' in done['answer']

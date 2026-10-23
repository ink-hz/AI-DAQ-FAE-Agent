"""One semantic correction must preserve independent inputs and combined debt."""
import json
from copy import deepcopy

import pytest

from daq_fae.request_schema import extract_request_schema, SchemaExtractionError
from daq_fae.task_context import prepare_turn


def goal(capability, entities, field=None, conditions=None):
    return {'kind':'product','capability':capability,'entities':entities,
            **({'field':field} if field else {}), 'conditions':conditions or {}}


class Sequence:
    def __init__(self, candidates): self.candidates,self.calls=candidates,[]
    def chat(self, messages, tools=None, required_tool=None):
        self.calls.append(deepcopy(messages))
        yield {'type':'tool_call','name':'submit_daq_request_schema',
               'arguments':{'confidence':'medium','goals':self.candidates[len(self.calls)-1]}}


QUESTION='NOVA 加 Probe 使用 10000mAh 充电宝，能一起录制多久？'
VALID=[goal('lookup_spec',['NOVA'],'power_consumption'),
       goal('lookup_spec',['Probe'],'power_consumption'),
       goal('inspect_topology',['NOVA','Probe'],conditions={
           'task':'recording_runtime','external_power_bank_capacity_mah':10000})]


@pytest.mark.parametrize('invalid',[
    [goal('lookup_spec',['NOVA'],'battery_life',{'power_bank_capacity_mah':10000})],
    [goal('lookup_spec',['NOVA','Probe'],'battery_life',{'power': '10000mAh'})],
])
def test_every_correction_revalidates_all_scopes_and_preserves_combined_gap(invalid):
    adapter=Sequence([invalid,VALID])
    schema=extract_request_schema(QUESTION,adapter=adapter)
    assert len(adapter.calls)==2
    # This is a model-facing structural contract, common to both actual failure
    # classes; the parser still independently validates the second candidate.
    retry=adapter.calls[1][0]['content']
    contract=json.loads(retry.split('SCHEMA_RETRY_CONTRACT\n')[-1])
    assert contract['revalidate_all_goals'] is True
    assert contract['single_entity_capabilities']==['lookup_spec','resolve_entity']
    assert contract['combined_result_is_not_per_device_fact'] is True
    requirements=prepare_turn(QUESTION,request_schema=schema).requirements
    inputs=[r for r in requirements if r['capability']=='lookup_spec']
    combined=next(r for r in requirements if r['capability']=='inspect_topology')
    assert {tuple(r['entities']) for r in inputs}=={('NOVA',),('Probe',)}
    assert all(r['conditions']=={} for r in inputs)
    assert combined['conditions']['external_power_bank_capacity_mah']==10000
    assert combined['reason']=='unmapped_condition_unverified'


def test_second_illegal_scope_is_explicit_failure_without_third_or_auto_split():
    invalid=[goal('lookup_spec',['NOVA','Probe'],'battery_life',{'power':'10000mAh'})]
    adapter=Sequence([invalid,invalid])
    with pytest.raises(SchemaExtractionError,match='schema_multi_entity_conditions_ambiguous'):
        extract_request_schema(QUESTION,adapter=adapter)
    assert len(adapter.calls)==2

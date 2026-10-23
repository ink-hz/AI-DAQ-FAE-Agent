"""A request for clarification cannot swallow an actionable product goal."""
import pytest
from daq_fae.workflow import prepare_workflow, context_clarification_done


class Adapter:
    def __init__(self, goal): self.goal=goal
    def chat(self, messages, tools=None, required_tool=None):
        yield {'type':'tool_call','name':required_tool,'arguments':{
            'confidence':'medium','goals':[self.goal,{'kind':'unknown','capability':'clarify'}]}}


@pytest.mark.parametrize('question,goal',[
    ('NOVA 和 Probe 不接 Adapter 可以照这个方案同步吗？',
     {'kind':'product','capability':'inspect_topology','entities':['NOVA','Probe'],
      'conditions':{'task':'sync','adapter_connection':'none'}}),
    ('当前有哪些数采产品？',{'kind':'product','capability':'catalog','entities':[]}),
])
def test_actionable_goal_with_ambiguous_context_still_reaches_evidence_loop(question,goal):
    prepared=prepare_workflow(question,adapter=Adapter(goal),runtime_release='daq-test')
    assert any(r['capability']==goal['capability'] for r in prepared.plan.requirements)
    assert context_clarification_done(prepared) is None

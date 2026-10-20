from daq_fae.request_schema import extract_request_schema


class Adapter:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        field = 'rgb_resolution' if self.calls == 1 else 'all_reviewed_specs'
        yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
               'arguments': {'confidence': 'high', 'goals': [{
                   'kind': 'product', 'capability': 'lookup_spec',
                   'entities': ['EG-DB'], 'field': field, 'conditions': {},
               }]}}


def test_broad_product_specs_retries_narrow_single_field_plan():
    adapter = Adapter()
    schema = extract_request_schema('介绍一下：EG-DB 规格 参数，越详细越好',
                                    adapter=adapter)
    assert adapter.calls == 2
    assert [goal.field for goal in schema.goals if goal.capability == 'lookup_spec'] == [
        'all_reviewed_specs']


def test_specific_field_does_not_expand_to_all_specs():
    adapter = Adapter()
    schema = extract_request_schema('EG-DB 的 RGB 分辨率是多少？', adapter=adapter)
    assert adapter.calls == 1
    assert [goal.field for goal in schema.goals if goal.capability == 'lookup_spec'] == [
        'rgb_resolution']

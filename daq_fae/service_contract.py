"""Versioned facts about this assistant and the currently bound knowledge view.

This contract describes the service. It never certifies product properties.
"""
from __future__ import annotations

from dataclasses import dataclass


_CAPABILITIES = (
    '产品与变体识别', '按条件查规格', '组合与连接咨询', '采集流程',
    '软件和 SDK 支持核对', '附件和现场问题排查', '来源与缺口说明',
)
_SERVICE_GOALS = frozenset({'service_identity', 'service_capabilities', 'service_coverage'})


@dataclass(frozen=True)
class ServiceContract:
    agent_id: str
    runtime_release: str
    knowledge_release: str
    role: str
    visible_entities: int
    visible_claims: int
    visible_sections: int
    capabilities: tuple[str, ...] = _CAPABILITIES

    @classmethod
    def for_release(cls, knowledge, *, role: str, runtime_release: str,
                    agent_id: str = 'ai-daq-fae-agent') -> 'ServiceContract':
        if not role or not runtime_release:
            raise ValueError('service_contract_identity_missing')
        rows = knowledge.records_for(role) if knowledge is not None else []
        sections = knowledge.sections_for(role) if knowledge is not None else []
        return cls(
            agent_id=agent_id, runtime_release=runtime_release,
            knowledge_release=knowledge.release_id if knowledge is not None else 'empty-dev-v0',
            role=role,
            visible_entities=sum(row['kind'] == 'entity' for row in rows),
            visible_claims=sum(row['kind'] == 'claim' for row in rows),
            visible_sections=len(sections),
        )

    def supports(self, capability: str) -> bool:
        return capability in _SERVICE_GOALS

    def prompt_note(self) -> str:
        return (
            'Versioned DAQ service contract (service facts only, never product evidence):\n'
            'Identity: Orbbec internal DAQ FAE assistant / 奥比中光内部数采 FAE 助手.\n'
            'Tasks: ' + '、'.join(self.capabilities) + '.\n'
            f'Current role-visible release {self.knowledge_release}: '
            f'{self.visible_entities} reviewed entity records, '
            f'{self.visible_claims} reviewed claim records, '
            f'{self.visible_sections} reviewed sections. '
            'These counts describe published coverage, not all product features or support. '
            'Answer a service-only question directly from this contract without product retrieval. '
            'For mixed requests, answer the service part and check every product claim separately.'
        )

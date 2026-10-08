"""User-facing safe abstention for a deliberately empty DAQ knowledge release."""
from __future__ import annotations

import hashlib
from collections.abc import MutableMapping, Sequence


EMPTY_KNOWLEDGE_RELEASE = 'empty-dev-v0'

_FOCUS = (
    ('resolve_entity', '已审核的设备与型号映射', '确认设备身份',
     '请提供正式型号或配置清单，核实后再确认设备身份。'),
    ('selection', '已审核的组合选型依据', '推荐设备组合',
     '请确认目标任务和对应的正式选型资料，核实后再给建议。'),
    ('catalog', '已审核的数采产品目录', '列出设备清单',
     '请提供或确认最新的正式产品目录及版本，核实后再给出清单。'),
    ('lookup_spec', '对应型号与版本的正式规格', '确认参数',
     '请提供或确认适用版本的正式规格书，核实后再确认参数。'),
    ('check_software_support', '设备与软件版本的兼容资料', '确认兼容关系',
     '请提供具体设备、软件版本和正式兼容清单，核实后再确认。'),
    ('inspect_topology', '设备组合的连接与同步资料', '给出连接结论',
     '请提供经确认的设备组合及适用的接线文档，核实后再给步骤。'),
    ('lookup_procedure', '适用的数采操作文档', '给出操作步骤',
     '请提供对应设备与版本的正式操作文档，核实后再给步骤。'),
    ('sdk_evidence', '经审核的数采 SDK 资料', '确认 SDK 用法',
     '请提供适用的 SDK 版本与正式文档，核实后再给实现建议。'),
    ('experience', '经审核的现场案例', '给出经验结论',
     '请提供可核验的案例记录，确认适用条件后再给建议。'),
    ('risk', '经审核的操作与数据风险资料', '判断风险',
     '请提供适用设备与任务的正式风险说明，核实后再给判断。'),
    ('official_links', '经核验且获准交付的官方入口', '提供链接',
     '请确认所需资产与交付权限，核验正式入口后再提供链接。'),
)


def refine_empty_release_answer(
    done: MutableMapping[str, object], *, planned_capabilities: Sequence[str],
    knowledge_release: str,
) -> bool:
    """Replace only a fully missing, source-free abstention; keep its audit trail."""
    if (knowledge_release != EMPTY_KNOWLEDGE_RELEASE
            or done.get('outcome') != 'safe_abstained'
            or done.get('sources')
            or done.get('fallback_used')):
        return False
    policy = done.get('evidence_policy')
    status = policy.get('requirement_status') if isinstance(policy, dict) else None
    if not isinstance(status, dict) or not status or set(status.values()) != {'missing'}:
        return False
    planned = set(planned_capabilities)
    if planned & {'search_attachments', 'analyze_image'}:
        return False
    focuses = [(subject, action, next_step)
               for capability, subject, action, next_step in _FOCUS
               if capability in planned]
    if not focuses:
        focuses = [('相关数采资料', '回答这个问题',
                    '请提供可核验的正式资料，确认适用条件后再给结论。')]
    if len(focuses) == 1:
        subject, action, next_step = focuses[0]
    else:
        subject = '、'.join(item[0] for item in focuses)
        action = '、'.join(item[1] for item in focuses)
        next_step = '请提供或确认各项适用的正式资料，核实后逐项回答。'
    original = str(done.get('answer') or '')
    done['answer'] = f'目前缺少{subject}，无法可靠{action}。{next_step}'
    done['fallback_used'] = True
    done['fallback_reason'] = 'empty_release_synthesis_template'
    done['synthesis_mode'] = 'deterministic_empty_release'
    done['synthesis_original_sha256'] = hashlib.sha256(original.encode()).hexdigest()
    done['synthesis_original_length'] = len(original)
    return True

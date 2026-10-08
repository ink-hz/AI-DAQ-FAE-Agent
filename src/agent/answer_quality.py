"""Shared answer-quality prompt wrapper for the launch channel.

This layer does not add facts. It gives every final synthesis prompt the same
FAE-style output contract so first-turn and multi-turn answers feel consistent.
"""

FAE_ANSWER_QUALITY_BLOCK = """上线回答质量要求:
- 第一屏必须先给直接结论,不要先铺资料。
- 按 FAE 咨询方式组织:结论 / 依据 / 注意 / 验证 / 下一步。
- 场景、选型、排障问题要给初步判断和工程假设,不要只追问。
- 追问最多 2 个,且必须服务于下一步选型、验证或排查。
- 不要把检索片段拼接给用户;用自然语言解释这些事实意味着什么。"""


def with_fae_answer_quality(prompt: str, consultation_context: str = "") -> str:
    context = consultation_context.strip()
    if context:
        return f"{context}\n\n{FAE_ANSWER_QUALITY_BLOCK}\n\n{prompt}"
    return f"{FAE_ANSWER_QUALITY_BLOCK}\n\n{prompt}"

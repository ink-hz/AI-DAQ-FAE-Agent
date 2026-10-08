"""Capability runners 注册表 — Phase B 骨架版,空 dict。

Phase B 的 B2/B3/B4 子任务会逐个把 runner 注册进 REGISTRY。
"""
from __future__ import annotations

from typing import Protocol

from src.agent.evidence import Evidence
from src.agent.planner import Capability


class CapabilityRunner(Protocol):
    """每个 capability 必须实现此协议。"""
    name: Capability

    def run(self, query: str, *, schema, channel, **kwargs) -> Evidence:
        ...


REGISTRY: dict[Capability, CapabilityRunner] = {}


def register(runner: CapabilityRunner) -> None:
    """B2/B3/B4 在自己的模块底部调用,注册到 REGISTRY。"""
    REGISTRY[runner.name] = runner


# Trigger self-registration of all capability modules.
# Each module calls register() at import time.
from src.agent.capabilities import capability_catalog  # noqa: E402, F401
from src.agent.capabilities import capability_experience  # noqa: E402, F401
from src.agent.capabilities import capability_selection  # noqa: E402, F401
from src.agent.capabilities import capability_spec  # noqa: E402, F401
from src.agent.capabilities import capability_sdk   # noqa: E402, F401
from src.agent.capabilities import capability_stub  # noqa: E402, F401
from src.agent.capabilities import capability_troubleshoot  # noqa: E402, F401

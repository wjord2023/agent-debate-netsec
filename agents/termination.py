"""Custom termination: Critic must issue the marker in an actual TextMessage.

Avoids false-positives when the model's reasoning (ThoughtEvent) echoes the marker
from its own system prompt.
"""
from __future__ import annotations

from typing import Sequence

from autogen_agentchat.base import TerminatedException, TerminationCondition
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    StopMessage,
    TextMessage,
)


class CriticApprovalTermination(TerminationCondition):
    def __init__(self, marker: str, source: str = "Critic") -> None:
        self._marker = marker
        self._source = source
        self._terminated = False

    @property
    def terminated(self) -> bool:
        return self._terminated

    async def __call__(
        self, messages: Sequence[BaseAgentEvent | BaseChatMessage]
    ) -> StopMessage | None:
        if self._terminated:
            raise TerminatedException("already terminated")
        for m in messages:
            if not isinstance(m, TextMessage):
                continue
            if getattr(m, "source", None) != self._source:
                continue
            if isinstance(m.content, str) and self._marker in m.content:
                self._terminated = True
                return StopMessage(
                    content=f"{self._source} approved with {self._marker}",
                    source="termination",
                )
        return None

    async def reset(self) -> None:
        self._terminated = False

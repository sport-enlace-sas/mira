"""Fail over between independently authenticated Mira LLM providers."""

from __future__ import annotations

import logging
from typing import Any

from mira.exceptions import LLMError, NonRetriableLLMError
from mira.llm.base import LLMProviderProtocol

logger = logging.getLogger(__name__)


class ProviderChain:
    """Use the secondary provider only for a transient primary failure.

    Invalid credentials and malformed configuration deliberately stay visible:
    silently consuming another provider in those cases would hide an incident.
    """

    supports_json_mode = True
    supports_tool_calling = False

    def __init__(self, primary: LLMProviderProtocol, fallback: LLMProviderProtocol) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_provider = "primary"

    @property
    def total_prompt_tokens(self) -> int:
        return self.primary.usage["prompt_tokens"] + self.fallback.usage["prompt_tokens"]

    @property
    def total_completion_tokens(self) -> int:
        return self.primary.usage["completion_tokens"] + self.fallback.usage["completion_tokens"]

    @property
    def usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
        }

    def count_tokens(self, text: str) -> int:
        return self.primary.count_tokens(text)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            self.last_provider = "primary"
            return await getattr(self.primary, method)(*args, **kwargs)
        except NonRetriableLLMError:
            raise
        except LLMError:
            # Providers deliberately raise LLMError only for transient
            # transport/provider conditions here (timeouts, 429 and 5xx).
            # Configuration, auth and parsing failures are non-retriable and
            # must remain visible instead of consuming the fallback account.
            logger.warning("Primary review provider failed transiently; using configured fallback")
            try:
                self.last_provider = "fallback"
                return await getattr(self.fallback, method)(*args, **kwargs)
            except Exception as fallback_error:
                raise LLMError("both_models_failed", primary_model="primary", fallback_model="fallback", error=fallback_error) from fallback_error

    async def complete(self, messages: list[dict[str, str]], json_mode: bool = True,
                       temperature: float | None = None, max_tokens: int | None = None) -> str:
        return await self._call("complete", messages, json_mode=json_mode,
                                temperature=temperature, max_tokens=max_tokens)

    async def complete_with_tools(self, messages: list[dict[str, str]], tools: list[dict],
                                  temperature: float | None = None) -> str:
        return await self._call("complete_with_tools", messages, tools, temperature=temperature)

    async def complete_agentic(self, messages: list, tools: list[dict],
                               temperature: float | None = None) -> dict:
        return await self._call("complete_agentic", messages, tools, temperature=temperature)

    async def review(self, messages: list[dict[str, str]], temperature: float | None = None) -> str:
        return await self._call("review", messages, temperature=temperature)

    async def walkthrough(self, messages: list[dict[str, str]]) -> str:
        return await self._call("walkthrough", messages)

"""LLM provider package — factory entry point."""

from __future__ import annotations

from mira.config import LLMConfig
from mira.llm.base import LLMProviderProtocol


def _create_single_llm(config: LLMConfig) -> LLMProviderProtocol:
    """Create the appropriate LLM provider based on config.provider.

    Returns an instance satisfying LLMProviderProtocol.
    """
    if config.provider == "bedrock":
        from mira.llm.bedrock import BedrockProvider

        return BedrockProvider(config)

    if config.provider in {"codex-cli", "codex_cli", "codex"}:
        from mira.llm.codex_cli import CodexCLIProvider

        return CodexCLIProvider(config)

    if config.provider in {"claude-cli", "claude_cli", "claude"}:
        from mira.llm.claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(config)

    if config.api_style == "responses":
        from mira.llm.responses import ResponsesProvider

        return ResponsesProvider(config)

    # Default: OpenAI-compatible endpoint (OpenRouter, vLLM, Ollama, etc.)
    from mira.llm.provider import LLMProvider

    return LLMProvider(config)


def create_llm(config: LLMConfig) -> LLMProviderProtocol:
    """Create one provider or an explicit cross-provider fallback chain."""
    if not config.fallback_provider:
        return _create_single_llm(config)

    from mira.llm.provider_chain import ProviderChain

    primary_config = config.model_copy(update={"fallback_provider": None, "fallback_model": None})
    fallback_config = config.model_copy(
        update={
            "provider": config.fallback_provider,
            "model": config.fallback_model or "codex-default",
            "fallback_provider": None,
            "fallback_model": None,
        }
    )
    return ProviderChain(_create_single_llm(primary_config), _create_single_llm(fallback_config))

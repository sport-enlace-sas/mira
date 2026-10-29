"""Explicit, idempotent preparation. Never activates imported credentials."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path

from mira.ai.models import SCENARIOS, ConnectionInput, FlowInput
from mira.ai.store import AIStore, Vault


async def prepare(store: AIStore, actor: int) -> dict:
    from mira.config import load_config

    async with store.session_lock("legacy-migration"):
        previous = store.state("migration")
        if previous:
            return previous
        config = load_config().llm
        aliases = {
            "codex": "codex-cli",
            "codex_cli": "codex-cli",
            "claude": "claude-cli",
            "claude_cli": "claude-cli",
        }
        providers = [aliases.get(config.provider, config.provider)]
        if config.fallback_provider:
            providers.append(aliases.get(config.fallback_provider, config.fallback_provider))
        providers = list(dict.fromkeys([*providers, "claude-cli", "codex-cli"]))
        connections = {}
        for provider in providers:
            provider = (
                "responses"
                if provider == "openai" and config.api_style == "responses"
                else provider
            )
            connections[provider] = store.save_connection(
                ConnectionInput.model_validate(
                    {
                        "name": f"{provider} · migración",
                        "provider": provider,
                        "base_url": config.base_url,
                        "region": config.region,
                    }
                ).model_dump(),
                actor,
            )
            secret, kind = None, "session"
            if provider == "codex-cli":
                raw = os.environ.get("MIRA_CODEX_AUTH_JSON", "")
                if raw:
                    with suppress(ValueError):
                        secret = json.loads(raw)
                elif config.codex_home:
                    with suppress(OSError, ValueError):
                        secret = json.loads((Path(config.codex_home) / "auth.json").read_text())
            elif provider == "claude-cli":
                secret, kind = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "oauth_token"
            elif provider in {"openai", "responses"}:
                secret, kind = os.environ.get(config.api_key_env), "api_key"
            elif provider == "bedrock":
                if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
                    secret = {
                        "access_key_id": os.environ["AWS_ACCESS_KEY_ID"],
                        "secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
                        "session_token": os.environ.get("AWS_SESSION_TOKEN", ""),
                    }
                    kind = "aws"
            if secret:
                try:
                    Vault(store).stage(connections[provider], kind, secret, actor)
                except ValueError:
                    store.audit(actor, "migration.reconnect_required", connections[provider])
        assignments = {}
        flow_ids = {}
        for purpose in ("review", "security", "indexing"):
            primary = providers[0]
            if primary == "openai" and config.api_style == "responses":
                primary = "responses"
            model = (
                store.db.get_setting(purpose + "_model")
                or getattr(config, purpose + "_model")
                or config.model
            )
            nodes = [
                {
                    "id": "primary",
                    "name": "Principal actual",
                    "connection_id": connections[primary],
                    "model": model,
                    "reasoning_effort": config.reasoning_effort or "off",
                }
            ]
            if config.fallback_provider:
                fallback = aliases.get(config.fallback_provider, config.fallback_provider)
                if fallback == "openai" and config.api_style == "responses":
                    fallback = "responses"
                nodes.append(
                    {
                        "id": "fallback",
                        "name": "Fallback actual",
                        "connection_id": connections[fallback],
                        "model": config.fallback_model or "gpt-6.1-sol",
                    }
                )
            flow_id = store.publish(
                FlowInput.model_validate(
                    {
                        "name": f"Migrado · {purpose}",
                        "nodes": nodes,
                        "edges": [{"source": "primary", "target": "fallback"}]
                        if len(nodes) == 2
                        else [],
                    }
                ),
                actor,
            )
            flow_ids[purpose] = flow_id
        for scenario in SCENARIOS:
            assignments[scenario] = flow_ids.get(
                scenario,
                flow_ids["indexing"]
                if scenario
                in {"summary", "self_critique", "learning", "thread_reply", "dependencies"}
                else flow_ids["review"],
            )
        store.assign(assignments, actor)
        template = store.publish(
            FlowInput.model_validate(
                {
                    "name": "Fable → Claude alternativo → Codex Sol/max",
                    "nodes": [
                        {
                            "id": "fable",
                            "name": "Fable 5.1",
                            "connection_id": connections["claude-cli"],
                            "model": "claude-fable-5-1",
                        },
                        {
                            "id": "claude",
                            "name": "Claude alternativo · confirmar en catálogo",
                            "connection_id": connections["claude-cli"],
                            "model": "claude-opus-4-8",
                        },
                        {
                            "id": "codex",
                            "name": "Codex · GPT 6.1 Sol · max",
                            "connection_id": connections["codex-cli"],
                            "model": "gpt-6.1-sol",
                            "reasoning_effort": "max",
                        },
                    ],
                    "edges": [
                        {"source": "fable", "target": "claude"},
                        {"source": "claude", "target": "codex"},
                    ],
                }
            ),
            actor,
        )
        result = {
            "connections": connections,
            "flows": flow_ids,
            "template": template,
            "status": "prepared",
            "exclusive_login_required": True,
        }
        store.set_state("migration", result)
        store.audit(actor, "migration.prepared", "legacy")
        return result

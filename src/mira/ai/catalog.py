"""Per-credential discovery. Listed models are never labelled access-verified."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from mira.ai.store import AIStore, Vault
from mira.llm.codex_cli import _SAFE_ENV_KEYS, CodexCLIProvider


async def codex_models(secret: dict, persist: Callable[[dict], None]) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="mira-catalog-") as temporary:
        home = Path(temporary)
        CodexCLIProvider._write_auth(
            home / "auth.json", secret.get("auth") or {"OPENAI_API_KEY": secret["api_key"]}
        )
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env.update(HOME=temporary, CODEX_HOME=temporary)
        process = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=temporary,
            env=env,
            start_new_session=True,
        )

        stdin, stdout = process.stdin, process.stdout
        assert stdin is not None and stdout is not None

        async def request(identifier: int, method: str, params: dict) -> dict:
            stdin.write(
                (json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode()
            )
            await stdin.drain()
            while line := await stdout.readline():
                response = json.loads(line)
                if response.get("id") == identifier:
                    if "error" in response:
                        raise ValueError("No se pudo consultar el catálogo Codex")
                    return response.get("result", {})
            raise ValueError("El cliente Codex terminó antes de responder")

        try:
            async with asyncio.timeout(45):
                await request(
                    1,
                    "initialize",
                    {
                        "clientInfo": {"name": "mira", "version": "0.8.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                stdin.write(b'{"method":"initialized","params":{}}\n')
                await stdin.drain()
                models, cursor, identifier = [], None, 2
                while True:
                    result = await request(
                        identifier,
                        "model/list",
                        {"cursor": cursor, "limit": 100, "includeHidden": False},
                    )
                    for item in result.get("data", []):
                        model_id = item.get("model") or item.get("id")
                        if model_id:
                            models.append(
                                {
                                    "id": model_id,
                                    "name": item.get("displayName", model_id),
                                    "reasoning_efforts": [
                                        e["reasoningEffort"]
                                        for e in item.get("supportedReasoningEfforts", [])
                                    ],
                                    "source": "codex:model/list",
                                    "status": "listed",
                                }
                            )
                    cursor = result.get("nextCursor")
                    identifier += 1
                    if not cursor:
                        break
        finally:
            await CodexCLIProvider._terminate_process_tree(process)
            refreshed = CodexCLIProvider._read_auth(home / "auth.json")
            if refreshed and secret.get("auth") and refreshed != secret["auth"]:
                persist(refreshed)
        return models


def curated(provider: str) -> list[dict]:
    from mira.llm import registry

    ids = list(registry.all_models())
    if provider == "claude-cli":
        ids = [m for m in ids if m.startswith("anthropic/")]
        ids = list(
            dict.fromkeys(
                [
                    *ids,
                    "anthropic/claude-fable-5-1",
                    "anthropic/claude-opus-5-5",
                    "anthropic/claude-sonnet-5-5",
                    "anthropic/claude-haiku-5-5",
                ]
            )
        )
    elif provider == "codex-cli":
        ids = ["gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-terra", "gpt-5.6-sol"]
    elif provider == "bedrock":
        # OpenRouter IDs are not valid Bedrock IDs. Discovery remains available
        # with AWS credentials; imported flows preserve their original IDs.
        ids = []
    models = [
        {
            "id": m.split("/", 1)[-1] if provider in {"codex-cli", "claude-cli"} else m,
            "name": m,
            "source": "registry",
            "status": "unverified",
        }
        for m in ids
    ]
    for model in models:
        identifier = model["id"].replace(".", "-")
        if provider == "claude-cli":
            if identifier.startswith(
                (
                    "claude-fable-5",
                    "claude-opus-4-7",
                    "claude-opus-4-8",
                    "claude-opus-5",
                    "claude-sonnet-5",
                    "claude-haiku-5-5",
                )
            ):
                model["context_window"] = 1_000_000
            elif identifier.startswith("claude-haiku-4-5"):
                model["context_window"] = 200_000
        elif provider == "codex-cli" and identifier == "gpt-6-1-sol":
            # Keep the supported ceiling separate from the native default.
            model["context_window"] = 1_050_000
            model["default_context_window"] = 500_000
    return models


def catalog_view(store: AIStore, connection_id: str) -> dict:
    """Keep discovery and proven access separate, including unlisted working models."""
    from mira.ai.runtime import quota_key

    connection = store.connection(connection_id)
    credential_id = connection["active_credential"]
    provider = connection["provider"]
    result = store.state(
        f"catalog:{credential_id}",
        {"models": curated(provider), "status": "unverified", "checked_at": None},
    )

    def model_id(value: str) -> str:
        if provider == "claude-cli":
            value = value.removeprefix("anthropic/")
            return re.sub(r"^(claude-[a-z0-9-]+-\d+)\.(\d+)$", r"\1-\2", value)
        return value

    # Older native clients may omit a model that accepts real requests. Registry
    # entries remain explicitly unverified; discovery never erases probe evidence.
    registry = curated(provider) if provider in {"claude-cli", "codex-cli"} else []
    models = {}
    for item in [*registry, *result["models"]]:
        identifier = model_id(item["id"])
        models[identifier] = {**models.get(identifier, {}), **item, "id": identifier, "access": {}}
    if credential_id:
        prefix = f"access:{credential_id}:"
        for key, value in store.rows(
            "SELECT id,data FROM ai_state WHERE id LIKE ?", (prefix + "%",)
        ):
            raw_model, effort = key[len(prefix) :].rsplit(":", 1)
            access = json.loads(value)
            if effort not in {"off", "low", "medium", "high", "xhigh", "max"}:
                continue
            identifier = model_id(raw_model)
            item = models.setdefault(
                identifier,
                {
                    "id": identifier,
                    "name": identifier,
                    "source": "diagnostic",
                    "status": "unverified",
                    "access": {},
                },
            )
            previous = item["access"].get(effort, {})
            if access.get("checked_at", 0) >= previous.get("checked_at", 0):
                item["access"][effort] = access
    for item in models.values():
        item["quota"] = store.state(f"quota:{credential_id}:{quota_key(provider, item['id'])}", {})
    return {**result, "models": list(models.values())}


async def discover(store: AIStore, connection_id: str) -> dict:
    connection = store.connection(connection_id)
    credential_id = connection["active_credential"]
    if not credential_id:
        return {
            "models": curated(connection["provider"]),
            "checked_at": None,
            "status": "unverified",
        }
    vault = Vault(store)
    async with store.session_lock(credential_id):
        secret, revision = vault.read(credential_id)
        provider = connection["provider"]
        try:
            if provider == "codex-cli":

                def persist(refreshed: dict) -> None:
                    nonlocal revision
                    secret["auth"] = refreshed
                    vault.update(credential_id, revision, secret)
                    revision += 1

                models = await codex_models(secret, persist)
            elif provider in {"openai", "responses"}:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(
                        connection["base_url"].rstrip("/") + "/models",
                        headers={"Authorization": "Bearer " + secret["api_key"]},
                    )
                    response.raise_for_status()
                    models = [
                        {
                            "id": m["id"],
                            "name": m.get("name", m["id"]),
                            "source": "api:models",
                            "status": "listed",
                            "context_window": m.get("context_length"),
                            "tools": "tools" in m["supported_parameters"]
                            if "supported_parameters" in m
                            else None,
                        }
                        for m in response.json().get("data", [])
                    ]
            elif provider == "bedrock":
                import boto3

                aws = secret["aws"]
                client = boto3.Session(
                    aws_access_key_id=aws["access_key_id"],
                    aws_secret_access_key=aws["secret_access_key"],
                    aws_session_token=aws.get("session_token"),
                    region_name=connection["region"],
                ).client("bedrock")
                result = await asyncio.to_thread(client.list_foundation_models)
                models = [
                    {
                        "id": m["modelId"],
                        "name": m["modelName"],
                        "source": "bedrock:models",
                        "status": "listed",
                    }
                    for m in result["modelSummaries"]
                    if "TEXT" in m.get("outputModalities", [])
                ]
            else:
                models = curated(provider)
            result = {
                "models": models,
                "checked_at": time.time(),
                "status": "listed" if provider != "claude-cli" else "unverified",
            }
        except Exception:
            # Discovery can be unsupported despite working inference. Do not quarantine.
            result = {
                "models": curated(provider),
                "checked_at": time.time(),
                "status": "unverified",
                "cause": "catalog_unavailable",
            }
        store.set_state(f"catalog:{credential_id}", result)
        return result

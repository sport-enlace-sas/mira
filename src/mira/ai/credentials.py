"""Credential boundary and restricted native clients.

Only this module and the vault decrypt credentials. No credential is inserted
into the service environment. Native clients see a private temporary home;
model tools cannot read that home or the vault directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mira.ai.store import AIStore, Vault
from mira.config import LLMConfig
from mira.exceptions import LLMError, NonRetriableLLMError
from mira.llm.claude_cli import ClaudeCLIProvider
from mira.llm.codex_cli import _SAFE_ENV_KEYS, CodexCLIProvider


def normalize_secret(provider: str, kind: str, secret: dict | str) -> dict:
    if kind == "api_key" and isinstance(secret, str) and secret.strip():
        if provider == "bedrock":
            raise ValueError("Bedrock requiere credenciales AWS")
        return {"api_key": secret.strip(), "renewable": False}
    if (
        kind == "oauth_token"
        and provider == "claude-cli"
        and isinstance(secret, str)
        and secret.strip()
    ):
        return {"oauth_token": secret.strip(), "renewable": False}
    if not isinstance(secret, dict):
        raise ValueError("Importa un archivo de sesión JSON compatible")
    if kind == "session" and provider == "codex-cli":
        if secret.get("OPENAI_API_KEY"):
            return {"api_key": secret["OPENAI_API_KEY"], "renewable": False}
        tokens = secret.get("tokens", {})
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise ValueError("El archivo Codex no contiene una sesión compatible")
        return {"auth": secret, "renewable": bool(tokens.get("refresh_token"))}
    if kind == "session" and provider == "claude-cli":
        oauth = secret.get("claudeAiOauth", {})
        if not isinstance(oauth, dict) or not oauth.get("accessToken"):
            raise ValueError("El archivo Claude no contiene una sesión compatible")
        return {"auth": secret, "renewable": bool(oauth.get("refreshToken"))}
    if kind == "aws" and provider == "bedrock":
        if not secret.get("access_key_id") or not secret.get("secret_access_key"):
            raise ValueError("Faltan las claves AWS requeridas")
        if set(secret) - {"access_key_id", "secret_access_key", "session_token"}:
            raise ValueError("Campos AWS desconocidos")
        return {"aws": secret, "renewable": False}
    raise ValueError("Tipo de credencial incompatible con el proveedor")


class ManagedCodex(CodexCLIProvider):
    def __init__(
        self, config: LLMConfig, secret: dict, persist: Any, private_root: Path | None = None
    ) -> None:
        super().__init__(config.model_copy(update={"ai_managed": True}))
        self.secret = secret
        self.persist = persist
        self.private_root = private_root
        self.usage_available = False

    def _account_cli_output(self, output: str) -> None:
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "turn.completed"
                and isinstance(event.get("usage"), dict)
            ):
                self.total_prompt_tokens += event["usage"].get("input_tokens", 0)
                self.total_completion_tokens += event["usage"].get("output_tokens", 0)
                self.usage_available = True

    def _prepare_codex_home(self, invocation_root: str) -> str:
        destination = Path(invocation_root) / "codex-home"
        auth = self.secret.get("auth") or {"OPENAI_API_KEY": self.secret.get("api_key")}
        self._write_auth(destination / "auth.json", auth)
        return str(destination)

    def _persist_codex_auth(self, runtime_codex_home: str) -> None:
        auth = self._read_auth(Path(runtime_codex_home) / "auth.json")
        if auth and self.secret.get("auth") and auth != self.secret["auth"]:
            self.secret["auth"] = auth
            self.persist(self.secret)

    def _command(self, output_path: str) -> list[str]:
        command = super()._command(output_path)
        index = command.index("--sandbox")
        del command[index : index + 2]  # Legacy --sandbox overrides filesystem deny rules.
        home = str(Path(output_path).parent / "codex-home")
        filesystem = {
            ":root": "deny",
            ":minimal": "read",
            ":workspace_roots": "read",
            "/proc": "deny",
            home: "deny",
        }
        executable = shutil.which(self.config.codex_command or "codex")
        if executable:
            resolved = Path(executable).resolve()
            # npm's launcher executes its bundled native sandbox helper. An
            # installation outside /usr is not part of :minimal automatically.
            runtime_root = (
                resolved.parent.parent if resolved.name == "codex.js" else resolved.parent
            )
            filesystem[str(runtime_root)] = "read"
        if self.private_root:
            filesystem[str(self.private_root)] = "deny"
        # CLI dotted overrides split on dots without TOML unquoting path keys.
        # Pass the filesystem table as one TOML value to preserve absolute paths.
        policy = (
            "{"
            + ",".join(
                f"{json.dumps(key)}={json.dumps(value)}" for key, value in filesystem.items()
            )
            + "}"
        )
        command[-1:-1] = [
            "--json",
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'default_permissions="mira"',
            "-c",
            f"permissions.mira.filesystem={policy}",
            "-c",
            "permissions.mira.network.enabled=false",
            "-c",
            'approval_policy="never"',
            "-c",
            "features.shell_tool=false",
            "-c",
            'web_search="disabled"',
            "-c",
            'model_provider="mira"',
            "-c",
            'model_providers.mira={name="OpenAI (MIRA)",requires_openai_auth=true,wire_api="responses",request_max_retries=0,stream_max_retries=0,supports_websockets=true}',
            "-c",
            f"model_context_window={self.config.max_context_tokens}",
            "-c",
            "model_auto_compact_token_limit=9223372036854775807",
        ]
        return command

    async def _run_codex(self, prompt: str) -> str:
        # The flow owns retries and the broker holds the cross-process lock.
        return await self._run_codex_once(prompt)


class ManagedClaude(ClaudeCLIProvider):
    def __init__(self, config: LLMConfig, secret: dict, persist: Any) -> None:
        super().__init__(config.model_copy(update={"ai_managed": True}))
        self.secret = secret
        self.persist = persist
        self.usage_available = False

    def _command(self, output_path: str = "") -> list[str]:
        command = super()._command()
        command[command.index("--output-format") + 1] = "json"
        if self.config.reasoning_effort and self.config.reasoning_effort != "off":
            command.extend(["--effort", self.config.reasoning_effort])
        return command

    def _env(self, runtime_home: str, runtime_codex_home: str = "") -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env.update(
            HOME=runtime_home,
            CLAUDE_CONFIG_DIR=runtime_home,
            CLAUDE_CODE_MAX_RETRIES="0",
            CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES="0",
            DISABLE_AUTO_COMPACT="1",
        )
        if self.secret.get("api_key"):
            env["ANTHROPIC_API_KEY"] = self.secret["api_key"]
        elif self.secret.get("oauth_token"):
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.secret["oauth_token"]
        return env

    async def _run_codex(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="mira-claude-session-") as temporary:
            home = Path(temporary)
            auth_path = home / ".credentials.json"
            if self.secret.get("auth"):
                self._write_auth(auth_path, self.secret["auth"])
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env(temporary),
                    cwd=temporary,
                    start_new_session=True,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()), self.config.claude_timeout_seconds
                )
                detail = (stderr + stdout).decode(errors="replace").lower()
                try:
                    payload = json.loads(stdout)
                except ValueError:
                    payload = {}
                usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
                if usage:
                    self.total_prompt_tokens += sum(
                        usage.get(key, 0)
                        for key in (
                            "input_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                        )
                    )
                    self.total_completion_tokens += usage.get("output_tokens", 0)
                    self.usage_available = True
                if process.returncode or (isinstance(payload, dict) and payload.get("is_error")):
                    if any(
                        s in detail
                        for s in (
                            "unauthorized",
                            "authentication",
                            "invalid token",
                            "not logged in",
                            "401",
                            "403",
                        )
                    ):
                        raise NonRetriableLLMError("claude_auth_failed")
                    if any(s in detail for s in ("limit", "quota", "429")):
                        raise LLMError("claude_usage_limit", model=self.effective_model)
                    raise LLMError("ai_provider_failed")
                return str(payload.get("result", "")) if isinstance(payload, dict) else ""
            finally:
                if process is not None:
                    await self._terminate_process_tree(process)
                refreshed = self._read_auth(auth_path)
                if refreshed and self.secret.get("auth") and refreshed != self.secret["auth"]:
                    self.secret["auth"] = refreshed
                    self.persist(self.secret)


class CredentialBroker:
    def __init__(self, store: AIStore) -> None:
        self.store = store
        self.vault = Vault(store)

    @asynccontextmanager
    async def provider(
        self, connection: dict, config: LLMConfig, credential_id: str | None = None
    ) -> AsyncIterator[Any]:
        credential_id = credential_id or connection["active_credential"]
        if not credential_id:
            raise NonRetriableLLMError("ai_auth_required")
        async with self.store.session_lock(credential_id):
            metadata = self.store.credential_metadata(credential_id)
            if metadata["connection_id"] != connection["id"]:
                raise ValueError("La credencial no pertenece a esta conexión")
            secret, revision = self.vault.read(credential_id)

            def persist(updated: dict) -> None:
                nonlocal revision
                self.vault.update(credential_id, revision, updated)
                revision += 1

            kind = connection["provider"]
            provider: Any
            if kind == "codex-cli":
                provider = ManagedCodex(config, secret, persist, self.store.root)
            elif kind == "claude-cli":
                provider = ManagedClaude(config, secret, persist)
            elif kind == "bedrock":
                import boto3
                from botocore.config import Config

                from mira.llm.bedrock import BedrockProvider

                # Explicit credentials prevent boto's ambient/instance-profile fallback.
                aws = secret["aws"]
                client = boto3.Session(
                    aws_access_key_id=aws["access_key_id"],
                    aws_secret_access_key=aws["secret_access_key"],
                    aws_session_token=aws.get("session_token"),
                    region_name=connection["region"],
                ).client(
                    "bedrock-runtime",
                    config=Config(
                        retries={"total_max_attempts": 1, "mode": "standard"},
                        connect_timeout=config.request_timeout,
                        read_timeout=config.request_timeout,
                    ),
                )
                provider = BedrockProvider(config, client=client)
            else:
                from mira.llm.provider import LLMProvider
                from mira.llm.responses import ResponsesProvider

                cls = ResponsesProvider if kind == "responses" else LLMProvider
                provider = cls(config)
                provider._cached_headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + secret["api_key"],
                }
            yield provider

    async def health(self, credential_id: str, status: str) -> None:
        async with self.store.session_lock(credential_id):
            secret, revision = self.vault.read(credential_id)
            self.vault.update(
                credential_id,
                revision,
                secret,
                {"status": status, "checked_at": time.time()},
            )

"""Immutable scenario snapshots and the single retry/fallback authority."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from jsonschema import validate
from jsonschema.exceptions import ValidationError as SchemaValidationError

from mira.ai.credentials import CredentialBroker
from mira.ai.models import SCENARIOS, FlowInput
from mira.ai.store import AIStore, new_id
from mira.config import LLMConfig
from mira.exceptions import LLMError

_runtime: AIRuntime | None = None


class FlowExhaustedError(LLMError):
    def __init__(self, run: dict) -> None:
        self.run_id = run["id"]
        self.causes = [
            {"node_id": s["node_id"], "cause": s["cause"], "action": s["action"]}
            for s in run["steps"]
        ]
        super().__init__("ai_flow_exhausted", run_id=self.run_id)


def configure(store: AIStore) -> AIRuntime:
    global _runtime
    if _runtime is None or _runtime.store.db is not store.db:
        _runtime = AIRuntime(store)
    return _runtime


def current() -> AIRuntime | None:
    return _runtime


def for_scenario(provider: Any, scenario: str) -> Any:
    return provider.for_scenario(scenario) if isinstance(provider, FlowProvider) else provider


def quota_key(provider: str, model: str) -> str:
    if provider == "claude-cli" and "fable" in model.lower():
        return "claude-fable"
    return model


def classify(error: Exception) -> tuple[str, str]:
    """Return only fixed codes/actions; upstream text can contain tokens."""
    if isinstance(error, TimeoutError):
        return "timeout", "Revisar timeout o proveedor"
    if isinstance(error, (json.JSONDecodeError, SchemaValidationError)) or str(error).startswith(
        "invalid_response"
    ):
        return "invalid", "Elegir un modelo compatible con el contrato"
    chain: list[str] = []
    seen: set[int] = set()
    cursor: BaseException | None = error
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        if getattr(cursor, "status", 0) == 403 or getattr(cursor, "code", "") in {
            "bedrock_access_denied",
            "bedrock_model_not_found",
        }:
            return "access", "Elegir un modelo disponible para esta cuenta"
        if getattr(cursor, "code", "") in {
            "codex_malformed_json",
            "codex_non_object_json",
            "codex_no_json_object",
            "codex_empty_response",
            "no_tool_call",
        }:
            return "invalid", "Elegir un modelo compatible con el contrato"
        chain.extend(
            (str(cursor).lower(), str(getattr(cursor, "code", "")), type(cursor).__name__.lower())
        )
        cursor = cursor.__cause__ or cursor.__context__
    text = " ".join(chain)
    if any(word in text for word in ("almacén", "recuperación", "vault")):
        return "vault", "Restaurar la clave y el respaldo del almacén"
    if any(
        code in text
        for code in (
            "expiredtoken",
            "invalidclienttokenid",
            "token is expired",
            "signaturedoesnotmatch",
        )
    ):
        return "auth", "Reconectar o rotar la credencial"
    if any(x in text for x in ("auth", "401", "403", "token_expired", "credential", "no_api_key")):
        return "auth", "Reconectar o rotar la credencial"
    if any(
        x in text
        for x in (
            "quota",
            "429",
            "usage_limit",
            "rate limit",
            "hit your",
            "reached your",
            "throttl",
        )
    ):
        return "quota", "Esperar el reinicio de cuota o elegir otro modelo"
    if "timeout" in text or "timed out" in text:
        return "timeout", "Revisar timeout o proveedor"
    if any(x in text for x in ("json", "schema", "invalid_response", "validationerror")):
        return "invalid", "Elegir un modelo compatible con el contrato"
    return "provider", "Revisar el proveedor y la configuración"


class AIRuntime:
    def __init__(self, store: AIStore) -> None:
        self.store = store
        self.broker = CredentialBroker(store)

    def managed(self) -> bool:
        latched = (self.store.root / "managed").exists()
        mode = self.store.state("mode", "legacy")  # Fail closed on DB errors.
        if latched and mode != "managed":
            raise ValueError("MIRA está en modo BD; restaura su configuración")
        return mode == "managed" or latched

    def snapshot(self) -> dict[str, dict]:
        return {
            scenario: self.store.flow(flow_id)
            for scenario, flow_id in self.store.assignments().items()
        }

    def provider(self, config: LLMConfig) -> FlowProvider:
        return FlowProvider(self, config, config.ai_scenario, self.snapshot())

    def activate(self, actor: int) -> None:
        snapshot = self.snapshot()
        if set(snapshot) != set(SCENARIOS):
            raise ValueError("Asigna un flujo para cada escenario")
        for flow in snapshot.values():
            available = False
            for node in flow["nodes"]:
                connection = self.store.connection(node["connection_id"])
                if not connection["enabled"] or not connection["active_credential"]:
                    raise ValueError("Hay conexiones sin una credencial activa")
                credential = self.store.credential_metadata(connection["active_credential"])
                access = self.store.state(
                    f"access:{credential['id']}:{node['model']}:{node['reasoning_effort']}", {}
                )
                if credential["status"] != "ready" or access.get("status") not in {
                    "verified",
                    "quota",
                }:
                    raise ValueError("Comprueba todos los modelos y esfuerzos antes de activar")
                available |= access.get("status") == "verified"
            if not available:
                raise ValueError("Cada flujo debe tener al menos un modelo con acceso comprobado")
        self.store._private_dir()
        path = self.store.root / "managed"
        with path.open("w") as handle:
            handle.write("managed\n")
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
        self.store.set_state("mode", "managed")
        self.store.audit(actor, "runtime.activated", "managed")


class FlowProvider:
    supports_json_mode = True
    supports_tool_calling = False

    def __init__(
        self,
        runtime: AIRuntime,
        config: LLMConfig,
        scenario: str,
        snapshot: dict[str, dict],
        shared: dict | None = None,
    ) -> None:
        self.runtime, self.scenario, self.snapshot = runtime, scenario, snapshot
        node_connections = [
            runtime.store.connection(n["connection_id"])
            for n in snapshot.get(scenario, {}).get("nodes", [])
        ]
        api_only = bool(node_connections) and all(
            c["provider"] not in {"claude-cli", "codex-cli"} for c in node_connections
        )
        self.supports_tool_calling = api_only
        self.supports_temperature = api_only
        self.supports_max_tokens = api_only
        self.config = config.model_copy(update={"ai_managed": True, "ai_scenario": scenario})
        if snapshot.get(scenario, {}).get("nodes"):
            first = FlowInput.model_validate(
                {k: v for k, v in snapshot[scenario].items() if k in {"name", "nodes", "edges"}}
            ).ordered_nodes()[0]
            self.config = self.config.model_copy(
                update={"model": first.model, "max_context_tokens": first.context_window}
            )
        self.shared: dict[str, Any] = (
            shared
            if shared is not None
            else {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "models": [],
                "providers": [],
                "fallback": False,
                "job_id": "",
            }
        )
        self.last_provider = "primary"

    def for_scenario(self, scenario: str) -> FlowProvider:
        return FlowProvider(self.runtime, self.config, scenario, self.snapshot, self.shared)

    @property
    def attempted_models(self) -> list[str]:
        return self.shared["models"]

    @property
    def attempted_providers(self) -> list[str]:
        return self.shared["providers"]

    @property
    def fallback_attempted(self) -> bool:
        return self.shared["fallback"]

    @property
    def usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.shared["prompt_tokens"],
            "completion_tokens": self.shared["completion_tokens"],
            "total_tokens": self.shared["prompt_tokens"] + self.shared["completion_tokens"],
        }

    @property
    def total_prompt_tokens(self) -> int:
        return self.shared["prompt_tokens"]

    @property
    def total_completion_tokens(self) -> int:
        return self.shared["completion_tokens"]

    def count_tokens(self, text: str) -> int:
        import tiktoken

        return (
            len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))
            if text
            else 0
        )

    @staticmethod
    def _validate(method: str, value: Any, kwargs: dict) -> None:
        from mira.llm.tool_schemas import SUBMIT_REVIEW_TOOL, SUBMIT_WALKTHROUGH_TOOL

        if method == "complete_agentic":
            if not isinstance(value, dict) or not (value.get("content") or value.get("tool_calls")):
                raise ValueError("invalid_response")
            tools = {t["function"]["name"]: t["function"] for t in kwargs.get("tools", [])}
            for call in value.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") not in tools:
                    raise ValueError("invalid_response: unknown tool")
                arguments = function.get("arguments", "{}")
                validate(
                    json.loads(arguments) if isinstance(arguments, str) else arguments,
                    tools[function["name"]]["parameters"],
                )
            return
        if not isinstance(value, str) or not value.strip():
            raise ValueError("invalid_response")
        tool: Any = (
            SUBMIT_REVIEW_TOOL
            if method == "review"
            else SUBMIT_WALKTHROUGH_TOOL
            if method == "walkthrough"
            else (kwargs.get("tools") or [None])[0]
        )
        if tool is not None:
            validate(json.loads(value), tool["function"]["parameters"])
        elif kwargs.get("json_mode", True) and not isinstance(json.loads(value), dict):
            raise ValueError("invalid_response: expected JSON object")

    async def _execute_node(
        self,
        connection: dict,
        node: Any,
        method: str,
        messages: list,
        kwargs: dict,
        active_id: str,
        metadata: dict,
        step: dict,
    ) -> Any:
        config = self.config.model_copy(
            update={
                "provider": connection["provider"],
                "base_url": connection["base_url"],
                "api_style": "responses" if connection["provider"] == "responses" else "chat",
                "region": connection["region"],
                "model": node.model,
                "reasoning_effort": None
                if node.reasoning_effort == "off"
                else node.reasoning_effort,
                "fallback_model": None,
                "fallback_provider": None,
                "max_retries": 1,
                "request_timeout": node.timeout,
                "codex_timeout_seconds": node.timeout,
                "claude_timeout_seconds": node.timeout,
                "max_context_tokens": node.context_window,
            }
        )
        for renewal in range(2):
            try:
                async with self.runtime.broker.provider(connection, config, active_id) as provider:
                    try:
                        async with asyncio.timeout(node.timeout):
                            result = await getattr(provider, method)(messages, **kwargs)
                    finally:
                        usage = provider.usage
                        step["usage"] = usage if getattr(provider, "usage_available", True) else {}
                        self.shared["prompt_tokens"] += usage.get("prompt_tokens", 0)
                        self.shared["completion_tokens"] += usage.get("completion_tokens", 0)
                self._validate(method, result, kwargs)
                return result
            except Exception as error:
                if renewal == 0 and metadata.get("renewable") and classify(error)[0] == "auth":
                    step["refresh_attempted"] = True
                    continue  # Native client refreshes once on a freshly loaded snapshot.
                raise

        raise LLMError("ai_provider_failed")

    async def _call(
        self,
        method: str,
        messages: list,
        *,
        flow: dict | None = None,
        mode: str = "real",
        faults: dict | None = None,
        credential_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        flow = flow or self.snapshot.get(self.scenario)
        if not flow:
            raise LLMError("ai_scenario_unassigned", scenario=self.scenario)
        nodes = FlowInput.model_validate(
            {k: v for k, v in flow.items() if k in {"name", "nodes", "edges"}}
        ).ordered_nodes()
        run = {
            "id": new_id(),
            "created_at": time.time(),
            "job_id": self.shared["job_id"],
            "scenario": self.scenario,
            "flow_id": flow.get("id", "diagnostic"),
            "flow_version": flow.get("version", 0),
            "mode": mode,
            "status": "running",
            "steps": [],
        }
        self.last_run_id = run["id"]
        self.runtime.store.save_run(run)
        request_size = self.count_tokens(
            json.dumps(messages, ensure_ascii=False) + json.dumps(kwargs.get("tools", []))
        )
        try:
            for index, node in enumerate(nodes):
                connection = self.runtime.store.connection(node.connection_id)
                active_id = credential_id or connection["active_credential"]
                metadata = self.runtime.store.credential_metadata(active_id) if active_id else {}
                catalog = self.runtime.store.state(f"catalog:{active_id}", {})
                info: dict = next(
                    (m for m in catalog.get("models", []) if m["id"] == node.model), {}
                )
                incompatible = ""
                output_budget = kwargs.get("max_tokens") or self.config.max_tokens
                context_limit = min(
                    node.context_window, info.get("context_window") or node.context_window
                )
                if request_size + output_budget > context_limit:
                    incompatible = "context_incompatible"
                if (
                    info.get("reasoning_efforts")
                    and node.reasoning_effort != "off"
                    and node.reasoning_effort not in info["reasoning_efforts"]
                ):
                    incompatible = "reasoning_incompatible"
                if (
                    info.get("tools") is False
                    and method
                    in {"complete_with_tools", "review", "walkthrough", "complete_agentic"}
                    and connection["provider"] not in {"codex-cli", "claude-cli"}
                ):
                    incompatible = "tools_incompatible"
                for attempt in range(node.retries + 1):
                    step = {
                        "scenario": self.scenario,
                        "node_id": node.id,
                        "node_name": node.name,
                        "provider": connection["provider"],
                        "connection_id": node.connection_id,
                        "credential_id": active_id,
                        "credential_revision": metadata.get("revision", 0),
                        "connection_name": connection["name"],
                        "model": node.model,
                        "reasoning_effort": node.reasoning_effort,
                        "attempt": attempt + 1,
                        "result": "failed",
                        "cause": "",
                        "action": "",
                        "duration_ms": 0,
                        "usage": {},
                        "refresh_attempted": False,
                        "input_tokens_estimated": request_size,
                        "output_tokens_reserved": output_budget,
                        "context_window": context_limit,
                    }
                    run["steps"].append(step)
                    started = time.monotonic()
                    if incompatible or not connection["enabled"]:
                        step.update(
                            result="skipped",
                            cause=incompatible or "connection_disabled",
                            action=(
                                f"La solicitud requiere aproximadamente {request_size + output_budget} "
                                f"tokens y el nodo admite {context_limit}. Ajusta el contexto o elige otro modelo"
                                if incompatible == "context_incompatible"
                                else "Editar el nodo o habilitar la conexión"
                            ),
                        )
                        break
                    if mode == "real" and (
                        not active_id
                        or (metadata.get("status") == "auth_blocked" and not credential_id)
                    ):
                        step.update(
                            result="skipped",
                            cause="auth",
                            action="Reconectar o rotar la credencial",
                        )
                        break
                    quota_scope = quota_key(connection["provider"], node.model)
                    cooldown = self.runtime.store.state(f"quota:{active_id}:{quota_scope}", {})
                    if mode == "real" and cooldown.get("until", 0) > time.time():
                        step.update(
                            result="skipped",
                            cause="quota",
                            action="Esperar el reinicio de cuota",
                            retry_at=cooldown["until"],
                            reset_at=cooldown.get("reset_at"),
                        )
                        break
                    self.last_provider = "primary" if index == 0 else "fallback"
                    self.shared["fallback"] |= index > 0
                    for key, value in (
                        ("models", node.model),
                        ("providers", connection["provider"]),
                    ):
                        if value not in self.shared[key]:
                            self.shared[key].append(value)
                    try:
                        if mode == "simulation":
                            fault = (faults or {}).get(node.id, "success")
                            if fault != "success":
                                raise (
                                    TimeoutError()
                                    if fault == "timeout"
                                    else ValueError(
                                        {
                                            "quota": "quota",
                                            "auth": "auth",
                                            "invalid": "invalid_response",
                                        }[fault]
                                    )
                                )
                            result = '{"ok":true}'
                        else:
                            result = await self._execute_node(
                                connection,
                                node,
                                method,
                                messages,
                                kwargs,
                                active_id,
                                metadata,
                                step,
                            )
                            await self.runtime.broker.health(active_id, "ready")
                            self.runtime.store.set_state(
                                f"access:{active_id}:{node.model}:{node.reasoning_effort}",
                                {"status": "verified", "checked_at": time.time()},
                            )
                        step.update(
                            result="success", duration_ms=int((time.monotonic() - started) * 1000)
                        )
                        self.shared["last_success_provider"] = connection["provider"]
                        run["status"] = "success"
                        return result
                    except asyncio.CancelledError:
                        step.update(result="cancelled", cause="cancelled")
                        raise
                    except Exception as error:
                        cause, action = classify(error)
                        step.update(cause=cause, action=action)
                        if mode == "real":
                            if cause == "auth":
                                await self.runtime.broker.health(active_id, "auth_blocked")
                            elif cause == "quota":
                                self.runtime.store.set_state(
                                    f"access:{active_id}:{node.model}:{node.reasoning_effort}",
                                    {"status": "quota", "checked_at": time.time()},
                                )
                                cursor: BaseException | None = error
                                retry_at, reset_at = time.time() + 30, None
                                visited: set[int] = set()
                                while cursor is not None and id(cursor) not in visited:
                                    visited.add(id(cursor))
                                    retry_at = getattr(cursor, "retry_at", retry_at)
                                    reset_at = getattr(cursor, "reset_at", reset_at)
                                    cursor = cursor.__cause__ or cursor.__context__
                                self.runtime.store.set_state(
                                    f"quota:{active_id}:{quota_scope}",
                                    {
                                        "until": reset_at or retry_at,
                                        "reset_at": reset_at,
                                        "cause": "quota",
                                    },
                                )
                        if cause in {"auth", "quota", "access"}:
                            break
                    finally:
                        step["duration_ms"] = int((time.monotonic() - started) * 1000)
                        self.runtime.store.save_run(run)
            run["status"] = "failed"
            raise FlowExhaustedError(run) from None
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            raise
        finally:
            run["finished_at"] = time.time()
            self.runtime.store.save_run(run)

    async def complete(
        self,
        messages: list,
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return await self._call(
            "complete",
            messages,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def complete_with_tools(
        self, messages: list, tools: list, temperature: float | None = None
    ) -> str:
        return await self._call(
            "complete_with_tools", messages, tools=tools, temperature=temperature
        )

    async def complete_agentic(
        self, messages: list, tools: list, temperature: float | None = None
    ) -> dict:
        return await self._call("complete_agentic", messages, tools=tools, temperature=temperature)

    async def review(self, messages: list, temperature: float | None = None) -> str:
        return await self._call("review", messages, temperature=temperature)

    async def walkthrough(self, messages: list) -> str:
        return await self.for_scenario("walkthrough")._call("walkthrough", messages)

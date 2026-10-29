"""Permission-checked dashboard API. All responses are secret-free projections."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from mira.ai.catalog import curated, discover
from mira.ai.login import LoginManager
from mira.ai.models import (
    SCENARIOS,
    ConnectionInput,
    CredentialInput,
    DiagnosticInput,
    FlowInput,
    FlowNode,
)
from mira.ai.runtime import FlowExhaustedError, FlowProvider, configure, quota_key
from mira.ai.store import AIStore, ConflictError, Vault
from mira.config import LLMConfig


def get_store(db: Any) -> AIStore:
    if not hasattr(db, "_ai_store"):
        db._ai_store = AIStore(db)
    return db._ai_store


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                # FastAPI's default includes `input`, potentially the entire secret.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {"loc": e["loc"], "type": e["type"], "msg": "Valor inválido"}
                            for e in error.errors()
                        ]
                    },
                )
            except ConflictError as error:
                return JSONResponse(status_code=409, content={"detail": str(error)})
            except ValueError as error:
                # Only our fixed validation messages cross this boundary.
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": str(error)
                        if not error.__cause__ and len(str(error)) < 200
                        else "Configuración de IA inválida"
                    },
                )

        return handler


class LoginCode(BaseModel):
    code: str = Field(repr=False)


class PermissionsInput(BaseModel):
    permissions: list[str]


def create_ai_router(db: Any) -> APIRouter:
    store = get_store(db)
    runtime = configure(store)
    logins = LoginManager(store)
    tasks: dict[str, asyncio.Task] = {}
    task_results: dict[str, dict] = {}
    router = APIRouter(prefix="/api/ai", tags=["ai"], route_class=PrivateRoute)

    def require(permission: str):
        def check(request: Request) -> Any:
            user = getattr(request.state, "user", None)
            if user is None or permission not in store.permissions(user):
                raise HTTPException(403, "Permiso de IA requerido")
            if request.method not in {"GET", "HEAD"} and request.headers.get("origin"):
                origin = urlsplit(request.headers["origin"])
                if origin.netloc != request.url.netloc and request.headers["origin"] not in {
                    "http://localhost:5173",
                    "http://localhost:3000",
                }:
                    raise HTTPException(403, "Origen no permitido")
            return user

        return check

    view = Depends(require("ai.view"))
    manage_credentials = Depends(require("ai.credentials.manage"))
    manage_flows = Depends(require("ai.flows.manage"))
    diagnostics = Depends(require("ai.diagnostics.run"))

    @router.get("/status")
    def status(user: Any = view) -> dict:
        return {
            "mode": "managed" if runtime.managed() else "legacy",
            "permissions": store.permissions(user),
            "scenarios": SCENARIOS,
            "key_present": (store.root / "vault.key").is_file(),
        }

    @router.get("/connections")
    def connections(user: Any = view) -> list[dict]:
        return [{**c, "credentials": store.credentials(c["id"])} for c in store.connections()]

    @router.post("/connections")
    def create_connection(body: ConnectionInput, user: Any = manage_credentials) -> dict:
        return {"id": store.save_connection(body.model_dump(), user.id)}

    @router.put("/connections/{connection_id}")
    def update_connection(
        connection_id: str, body: ConnectionInput, user: Any = manage_credentials
    ) -> dict:
        store.connection(connection_id)
        return {"id": store.save_connection(body.model_dump(), user.id, connection_id)}

    @router.post("/connections/{connection_id}/credentials")
    def stage(connection_id: str, body: CredentialInput, user: Any = manage_credentials) -> dict:
        return {"id": Vault(store).stage(connection_id, body.kind, body.secret, user.id)}

    @router.post("/connections/{connection_id}/credentials/{credential_id}/activate")
    async def activate_credential(
        connection_id: str, credential_id: str, user: Any = manage_credentials
    ) -> dict:
        async with store.session_lock(credential_id):
            Vault(store).activate(connection_id, credential_id, user.id)
        return {"ok": True}

    @router.get("/connections/{connection_id}/models")
    def models(connection_id: str, user: Any = view) -> dict:
        connection = store.connection(connection_id)
        result = store.state(
            f"catalog:{connection['active_credential']}",
            {"models": curated(connection["provider"]), "status": "unverified", "checked_at": None},
        )
        for model in result["models"]:
            model["access"] = {
                effort: store.state(
                    f"access:{connection['active_credential']}:{model['id']}:{effort}", {}
                )
                for effort in ("off", "low", "medium", "high", "xhigh", "max")
            }
            model["quota"] = store.state(
                f"quota:{connection['active_credential']}:{quota_key(connection['provider'], model['id'])}",
                {},
            )
        return result

    @router.post("/connections/{connection_id}/models/refresh")
    async def refresh_models(connection_id: str, user: Any = diagnostics) -> dict:
        result = await discover(store, connection_id)
        store.audit(user.id, "catalog.refreshed", connection_id)
        return result

    @router.post("/connections/{connection_id}/login")
    async def start_login(connection_id: str, user: Any = manage_credentials) -> dict:
        return await logins.start(connection_id, user.id)

    @router.get("/logins/{login_id}")
    def login_status(login_id: str, user: Any = manage_credentials) -> dict:
        return logins.public(login_id, user.id)

    @router.get("/connections/{connection_id}/login")
    def resume_login(connection_id: str, user: Any = manage_credentials) -> dict | None:
        store.connection(connection_id)
        return logins.latest(connection_id, user.id)

    @router.post("/logins/{login_id}/code")
    async def login_code(login_id: str, body: LoginCode, user: Any = manage_credentials) -> dict:
        return await logins.submit(login_id, user.id, body.code)

    @router.delete("/logins/{login_id}")
    async def cancel_login(login_id: str, user: Any = manage_credentials) -> dict:
        await logins.cancel(login_id, user.id)
        return {"ok": True}

    @router.get("/flows")
    def flows(user: Any = view) -> list[dict]:
        return store.flows()

    @router.get("/flows/{flow_id}")
    def flow(flow_id: str, version: int | None = None, user: Any = view) -> dict:
        return store.flow(flow_id, version)

    @router.get("/flows/{flow_id}/versions")
    def flow_versions(flow_id: str, user: Any = view) -> list[int]:
        return [
            r[0]
            for r in store.rows(
                "SELECT version FROM ai_flow_versions WHERE flow_id=? ORDER BY version DESC",
                (flow_id,),
            )
        ]

    @router.post("/flows")
    def create_flow(body: FlowInput, user: Any = manage_flows) -> dict:
        return {"id": store.publish(body, user.id)}

    @router.put("/flows/{flow_id}")
    def publish_flow(flow_id: str, body: FlowInput, user: Any = manage_flows) -> dict:
        return {"id": store.publish(body, user.id, flow_id)}

    @router.get("/assignments")
    def assignments(user: Any = view) -> dict:
        return store.assignments()

    @router.put("/assignments")
    def assign(body: dict[str, str], user: Any = manage_flows) -> dict:
        store.assign(body, user.id)
        return {"ok": True}

    async def run_diagnostic(body: DiagnosticInput, actor: int) -> dict:
        credential_id = body.credential_id
        expected_active = (
            store.connection(body.connection_id)["active_credential"]
            if body.connection_id
            else None
        )
        if body.flow_id:
            target = store.flow(body.flow_id)
        elif body.connection_id:
            connection = store.connection(body.connection_id)
            credential_id = credential_id or connection["active_credential"]
            model = body.model or (
                "gpt-6.1-sol"
                if connection["provider"] == "codex-cli"
                else "claude-haiku-4-5"
                if connection["provider"] == "claude-cli"
                else ""
            )
            if not model:
                raise ValueError("Selecciona un modelo para comprobar la conexión")
            target = FlowInput(
                name="Prueba de conexión",
                nodes=[
                    FlowNode.model_validate(
                        {
                            "id": "connection-test",
                            "name": "Prueba de conexión",
                            "connection_id": connection["id"],
                            "model": model,
                            "reasoning_effort": body.reasoning_effort,
                            "timeout": 120,
                        }
                    )
                ],
                edges=[],
            ).model_dump()
        else:
            raise ValueError("Selecciona una conexión o un flujo")
        probe = FlowProvider(runtime, LLMConfig(max_tokens=4096), "diagnostic", {})
        try:
            await probe._call(
                "complete_with_tools",
                [{"role": "user", "content": 'Return only this JSON: {"ok": true}'}],
                flow=target,
                mode=body.mode,
                faults=body.faults,
                credential_id=credential_id,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "mira_connection_probe",
                            "description": "Return a valid health result",
                            "parameters": {
                                "type": "object",
                                "properties": {"ok": {"type": "boolean", "const": True}},
                                "required": ["ok"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            )
        except FlowExhaustedError as error:
            result = json.loads(
                store.rows("SELECT data FROM ai_runs WHERE id=?", (error.run_id,))[0][0]
            )
        else:
            result = json.loads(
                store.rows("SELECT data FROM ai_runs WHERE id=?", (probe.last_run_id,))[0][0]
            )
        if (
            body.action == "rotation"
            and result["status"] == "success"
            and body.mode == "real"
            and body.connection_id
            and credential_id
        ):
            async with store.session_lock(credential_id):
                Vault(store).activate(body.connection_id, credential_id, actor, expected_active)
        store.audit(actor, "diagnostic." + body.mode, result["id"])
        return result

    @router.post("/diagnostics")
    async def diagnostic(body: DiagnosticInput, user: Any = diagnostics) -> dict:
        if body.action == "rotation" and "ai.credentials.manage" not in store.permissions(user):
            raise HTTPException(403, "Permiso para rotar credenciales requerido")
        return await run_diagnostic(body, user.id)

    @router.post("/diagnostics/start")
    async def start_diagnostic(body: DiagnosticInput, user: Any = diagnostics) -> dict:
        from mira.ai.store import new_id

        if body.action == "rotation" and "ai.credentials.manage" not in store.permissions(user):
            raise HTTPException(403)
        identifier = new_id()
        task_results[identifier] = {"id": identifier, "status": "running", "actor": user.id}

        async def perform() -> None:
            try:
                result = await run_diagnostic(body, user.id)
                task_results[identifier].update(status=result["status"], result=result)
            except asyncio.CancelledError:
                task_results[identifier]["status"] = "cancelled"
                raise
            except Exception:
                task_results[identifier].update(
                    status="failed", error="No se pudo completar el diagnóstico"
                )

        tasks[identifier] = asyncio.create_task(perform())
        return task_results[identifier]

    @router.get("/diagnostics/{diagnostic_id}")
    def diagnostic_status(diagnostic_id: str, user: Any = diagnostics) -> dict:
        result = task_results.get(diagnostic_id)
        if not result or result["actor"] != user.id:
            raise HTTPException(404)
        return result

    @router.delete("/diagnostics/{diagnostic_id}")
    async def cancel_diagnostic(diagnostic_id: str, user: Any = diagnostics) -> dict:
        diagnostic_status(diagnostic_id, user)
        tasks[diagnostic_id].cancel()
        return {"ok": True}

    @router.get("/runs")
    def runs(job_id: str | None = None, user: Any = view) -> list[dict]:
        return store.runs(job_id)

    @router.get("/runs/{run_id}")
    def run(run_id: str, user: Any = view) -> dict:
        rows = store.rows("SELECT data FROM ai_runs WHERE id=?", (run_id,))
        if not rows:
            raise HTTPException(404)
        return json.loads(rows[0][0])

    @router.post("/activate")
    async def activate(user: Any = manage_flows) -> dict:
        if not user.is_admin:
            raise HTTPException(403, "Solo el administrador puede activar el modo BD")
        async with store.session_lock("runtime-activation"):
            runtime.activate(user.id)
        return {"ok": True}

    @router.post("/migration/prepare")
    async def migrate(user: Any = manage_credentials) -> dict:
        from mira.ai.migration import prepare

        if not user.is_admin:
            raise HTTPException(403)
        if runtime.managed():
            raise HTTPException(409, "La configuración de BD ya está activa")
        return await prepare(store, user.id)

    @router.put("/users/{user_id}/permissions")
    def permissions(user_id: int, body: PermissionsInput, user: Any = view) -> dict:
        if not user.is_admin:
            raise HTTPException(403, "Solo el administrador puede delegar permisos")
        store.grant(user_id, body.permissions, user.id)
        return {"ok": True, "permissions": body.permissions}

    @router.get("/audit")
    def audit(user: Any = view) -> list[dict]:
        return [
            {"id": r[0], "created_at": r[1], "actor": r[2], "action": r[3], "resource": r[4]}
            for r in store.rows(
                "SELECT id,created_at,actor,action,resource FROM ai_audit ORDER BY created_at DESC LIMIT 200"
            )
        ]

    return router

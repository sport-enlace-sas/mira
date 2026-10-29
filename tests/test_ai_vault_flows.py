"""Contracts for the managed AI boundary, without provider/network consumption."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import tomllib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mira.ai.api import create_ai_router
from mira.ai.credentials import ManagedClaude, ManagedCodex
from mira.ai.models import SCENARIOS, ConnectionInput, FlowInput
from mira.ai.runtime import AIRuntime, quota_key
from mira.ai.store import AIStore, ConflictError, Vault
from mira.config import LLMConfig
from mira.dashboard.db import AppDatabase
from mira.exceptions import NonRetriableLLMError


@pytest.fixture
def store(tmp_path):
    import mira.ai.runtime as runtime_module

    previous_runtime = runtime_module._runtime
    db = AppDatabase(str(tmp_path / "app.db"), admin_password="only-a-test-password")
    value = AIStore(db, tmp_path / "private")
    db._ai_store = value
    yield value
    runtime_module._runtime = previous_runtime
    db._sqlite_conn.close()


def connection(store, provider="claude-cli", secret="test-secret-never-returned"):
    cid = store.save_connection(ConnectionInput(name=provider, provider=provider).model_dump(), 1)
    kind = "oauth_token" if provider == "claude-cli" else "api_key"
    credential = Vault(store).stage(cid, kind, secret, 1)
    data, revision = Vault(store).read(credential)
    Vault(store).update(credential, revision, data, {"status": "ready"})
    Vault(store).activate(cid, credential, 1)
    return cid, credential


def chain(store, cid, models=("fable", "opus", "sol", "last"), retries=0):
    flow = FlowInput(
        name="Review",
        nodes=[
            {"id": str(i), "name": model, "connection_id": cid, "model": model, "retries": retries}
            for i, model in enumerate(models)
        ],
        edges=[{"source": str(i), "target": str(i + 1)} for i in range(len(models) - 1)],
    )
    identifier = store.publish(flow, 1)
    store.assign(dict.fromkeys(SCENARIOS, identifier), 1)
    return identifier


class FakeProvider:
    def __init__(self, invoke):
        self.complete = invoke
        self.complete_with_tools = invoke
        self.usage = {"prompt_tokens": 2, "completion_tokens": 1}


def mock_broker(runtime, invoke):
    @asynccontextmanager
    async def provider(connection, config, credential_id=None):
        async def call(*args, **kwargs):
            return await invoke(config)

        yield FakeProvider(call)

    runtime.broker.provider = provider


def test_vault_encrypts_and_authenticates_identity(store):
    cid, credential = connection(store)
    second = Vault(store).stage(cid, "oauth_token", "second-secret", 1)
    ciphertext = store.rows("SELECT ciphertext FROM ai_credentials WHERE id=?", (credential,))[0][0]
    assert "test-secret" not in ciphertext
    assert "test-secret" not in json.dumps(store.connections() + store.credentials(cid))
    store.execute("UPDATE ai_credentials SET ciphertext=? WHERE id=?", (ciphertext, second))
    with pytest.raises(ValueError, match="No se puede abrir"):
        Vault(store).read(second)
    assert (store.root / "vault.key").stat().st_mode & 0o777 == 0o600


def test_missing_key_never_replaces_existing_ciphertext(store):
    cid, credential = connection(store)
    (store.root / "vault.key").unlink()
    with pytest.raises(ValueError, match="respaldo|recuperación"):
        Vault(store).read(credential)
    assert not (store.root / "vault.key").exists()


def test_linear_graph_has_no_functional_node_limit(store):
    cid, _ = connection(store)
    fid = chain(store, cid, tuple(f"model-{i}" for i in range(250)))
    assert len(store.flow(fid)["nodes"]) == 250
    bad = store.flow(fid)
    bad["edges"].append({"source": "0", "target": "2"})
    with pytest.raises(ValidationError):
        FlowInput.model_validate({k: bad[k] for k in ("name", "nodes", "edges")})
    with pytest.raises(ValidationError):
        FlowInput(name="Cycle", nodes=bad["nodes"][:2], edges=[{"source": "0", "target": "0"}])


async def test_four_fallbacks_restart_at_first_and_simulation_does_not_change_health(store):
    cid, credential = connection(store)
    fid = chain(store, cid)
    runtime = AIRuntime(store)
    provider = runtime.provider(LLMConfig())
    before = store.credential_metadata(credential)
    faults = {"0": "quota", "1": "auth", "2": "invalid"}
    for _ in range(2):
        assert (
            await provider._call("complete", [], mode="simulation", faults=faults) == '{"ok":true}'
        )
    assert [[s["node_id"] for s in r["steps"]] for r in store.runs()] == [["0", "1", "2", "3"]] * 2
    assert store.credential_metadata(credential) == before
    assert store.state(f"quota:{credential}:fable") is None


async def test_fable_quota_does_not_quarantine_other_claude_models(store):
    cid, credential = connection(store)
    chain(store, cid, ("fable", "haiku"))
    runtime = AIRuntime(store)
    seen = []

    async def invoke(config):
        seen.append(config.model)
        if config.model == "fable":
            raise ValueError("quota exhausted")
        return '{"ok":true}'

    mock_broker(runtime, invoke)
    await runtime.provider(LLMConfig()).complete([])
    assert seen == ["fable", "haiku"]
    assert store.credential_metadata(credential)["status"] == "ready"
    assert store.state(f"quota:{credential}:{quota_key('claude-cli', 'fable')}")
    assert store.state(f"quota:{credential}:haiku") is None


async def test_auth_failure_continues_to_another_connection(store):
    first, bad = connection(store)
    second, good = connection(store, "codex-cli")
    fid = chain(store, first, ("claude", "gpt-6.1-sol"))
    flow = store.flow(fid)
    flow["nodes"][1].update(connection_id=second, reasoning_effort="max")
    store.publish(
        FlowInput(
            name=flow["name"],
            nodes=flow["nodes"],
            edges=flow["edges"],
            expected_version=flow["version"],
        ),
        1,
        fid,
    )
    runtime = AIRuntime(store)
    seen = []

    async def invoke(config):
        seen.append((config.model, config.reasoning_effort))
        if config.model == "claude":
            raise NonRetriableLLMError("claude_auth_failed")
        return '{"ok":true}'

    mock_broker(runtime, invoke)
    assert await runtime.provider(LLMConfig()).complete([])
    assert seen == [("claude", None), ("gpt-6.1-sol", "max")]
    assert store.credential_metadata(bad)["status"] == "auth_blocked"
    assert store.credential_metadata(good)["status"] == "ready"


async def test_native_refreshes_are_serialized_and_persisted(store, monkeypatch):
    cid = store.save_connection(
        ConnectionInput(name="exclusive", provider="codex-cli").model_dump(), 1
    )
    credential = Vault(store).stage(
        cid, "session", {"tokens": {"access_token": "access", "refresh_token": "old"}}, 1
    )
    runtime = AIRuntime(store)
    started, release = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def execute(self, prompt):
        snapshots.append(self.secret["auth"]["tokens"]["refresh_token"])
        if len(snapshots) == 1:
            started.set()
            await release.wait()
            self.secret["auth"]["tokens"]["refresh_token"] = "new"
            self.persist(self.secret)
        return '{"ok":true}'

    monkeypatch.setattr(ManagedCodex, "_run_codex_once", execute)

    async def invoke():
        async with runtime.broker.provider(
            store.connection(cid), LLMConfig(), credential
        ) as provider:
            return await provider.complete([])

    first = asyncio.create_task(invoke())
    await started.wait()
    second = asyncio.create_task(invoke())
    await asyncio.sleep(0.01)
    assert snapshots == ["old"]
    release.set()
    await asyncio.gather(first, second)
    assert snapshots == ["old", "new"]
    assert Vault(store).read(credential)[0]["auth"]["tokens"]["refresh_token"] == "new"


def test_stale_credential_updates_and_old_rotations_are_rejected(store):
    cid, old = connection(store)
    secret, revision = Vault(store).read(old)
    Vault(store).update(old, revision, secret)
    with pytest.raises(ConflictError):
        Vault(store).update(old, revision, {"oauth_token": "stale"})
    fresh = Vault(store).stage(cid, "oauth_token", "new-token", 1)
    data, rev = Vault(store).read(fresh)
    Vault(store).update(fresh, rev, data, {"status": "ready"})
    Vault(store).activate(cid, fresh, 1)
    with pytest.raises(ConflictError):
        Vault(store).activate(cid, old, 1)
    assert store.connection(cid)["active_credential"] == fresh
    fid = chain(store, cid, ("model",))
    flow = store.flow(fid)
    update = FlowInput(name=flow["name"], nodes=flow["nodes"], edges=[], expected_version=1)
    store.publish(update, 1, fid)
    with pytest.raises(ConflictError):
        store.publish(update, 1, fid)
    assert store.flow(fid)["version"] == 2
    assert store.rows("SELECT COUNT(*) FROM ai_flow_versions WHERE flow_id=?", (fid,))[0][0] == 2


async def test_cancellation_stops_chain_and_releases_session_lock(store):
    cid, credential = connection(store)
    chain(store, cid, ("first", "second"))
    runtime = AIRuntime(store)
    started = asyncio.Event()
    seen = []

    async def invoke(config):
        seen.append(config.model)
        started.set()
        await asyncio.Event().wait()

    mock_broker(runtime, invoke)
    task = asyncio.create_task(runtime.provider(LLMConfig()).complete([]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == ["first"]
    assert store.runs()[0]["status"] == "cancelled"
    assert store.runs()[0]["steps"][0]["result"] == "cancelled"
    async with asyncio.timeout(1), store.session_lock(credential):
        pass


async def test_timeout_invalid_response_and_node_retries_are_bounded(store):
    cid, _ = connection(store)
    chain(store, cid, ("invalid", "valid"), retries=1)
    runtime = AIRuntime(store)
    seen = []

    async def invoke(config):
        seen.append(config.model)
        if config.model == "invalid":
            return "not-json-with-a-secret-that-must-not-leak"
        return '{"ok":true}'

    mock_broker(runtime, invoke)
    await runtime.provider(LLMConfig()).complete([])
    assert seen == ["invalid", "invalid", "valid"]
    assert "secret" not in json.dumps(store.runs())
    assert [s["cause"] for s in store.runs()[0]["steps"]] == ["invalid", "invalid", ""]


async def test_snapshot_and_scenario_bindings_are_independent(store):
    cid, _ = connection(store)
    fid = chain(store, cid, ("old-model",))
    runtime = AIRuntime(store)
    old = runtime.provider(LLMConfig())
    existing = store.flow(fid)
    existing["nodes"][0]["model"] = "new-model"
    store.publish(
        FlowInput(name="Updated", nodes=existing["nodes"], edges=[], expected_version=1), 1, fid
    )
    other = chain(store, cid, ("summary-model",))
    store.assign({s: fid for s in SCENARIOS if s != "summary"}, 1)
    store.assign({"summary": other}, 1)
    fresh = runtime.provider(LLMConfig())
    assert old.snapshot["review"]["nodes"][0]["model"] == "old-model"
    assert fresh.snapshot["review"]["nodes"][0]["model"] == "new-model"
    assert fresh.for_scenario("summary").snapshot["summary"]["nodes"][0]["model"] == "summary-model"


async def test_incompatible_context_is_skipped_without_truncation(store):
    cid, _ = connection(store)
    fid = chain(store, cid, ("small", "large"))
    flow = store.flow(fid)
    flow["nodes"][0]["context_window"] = 1024
    store.publish(
        FlowInput(name="Context", nodes=flow["nodes"], edges=flow["edges"], expected_version=1),
        1,
        fid,
    )
    runtime = AIRuntime(store)
    invoke = AsyncMock(return_value='{"ok":true}')
    mock_broker(runtime, invoke)
    await runtime.provider(LLMConfig()).complete([{"role": "user", "content": "whole request"}])
    assert invoke.await_count == 1
    assert store.runs()[0]["steps"][0]["cause"] == "context_incompatible"


def test_cli_model_effort_and_filesystem_restrictions_are_explicit(tmp_path):
    provider = ManagedCodex(
        LLMConfig(model="gpt-6.1-sol", reasoning_effort="max"), {"auth": {}}, lambda value: None
    )
    command = provider._command(str(tmp_path / "result.txt"))
    assert command[command.index("-m") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="max"' in command
    assert "--sandbox" not in command
    assert "project_doc_max_bytes=0" in command
    policy = next(value for value in command if value.startswith("permissions.mira.filesystem="))
    filesystem = tomllib.loads("filesystem=" + policy.split("=", 1)[1])["filesystem"]
    assert filesystem[":root"] == "deny"
    assert filesystem[":minimal"] == "read"
    assert filesystem["/proc"] == "deny"
    assert "permissions.mira.network.enabled=false" in command
    assert "MIRA_CODEX_AUTH_JSON" not in provider._env(str(tmp_path), str(tmp_path))
    claude = ManagedClaude(LLMConfig(reasoning_effort="max"), {"auth": {}}, lambda value: None)
    assert claude._command()[-2:] == ["--effort", "max"]


@pytest.mark.skipif(
    not shutil.which("codex"), reason="Native Codex CLI is optional outside the image"
)
def test_native_codex_profile_denies_vault_other_sessions_and_process_environment(tmp_path):
    private = tmp_path / "vault"
    private.mkdir()
    (private / "vault.key").write_bytes(b"test-canary")
    other = tmp_path / "other-connection-auth.json"
    other.write_text('{"token":"test-canary"}')
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    provider = ManagedCodex(
        LLMConfig(model="gpt-6.1-sol", reasoning_effort="max"),
        {"auth": {}},
        lambda value: None,
        private,
    )
    configured = provider._command(str(tmp_path / "result.txt"))
    overrides = []
    for index, argument in enumerate(configured[:-1]):
        if argument == "-c":
            overrides += ["-c", configured[index + 1]]
    verifier = "import pathlib,sys; denied=0\nfor name in sys.argv[1:]:\n try: pathlib.Path(name).read_bytes()\n except (PermissionError,FileNotFoundError): denied+=1\nsys.exit(0 if denied==len(sys.argv)-1 else 1)"
    result = subprocess.run(
        [
            "codex",
            "sandbox",
            "-P",
            "mira",
            *overrides,
            "-C",
            str(workspace),
            "--",
            "/usr/bin/python3",
            "-c",
            verifier,
            str(private / "vault.key"),
            str(other),
            f"/proc/{os.getpid()}/environ",
        ],
        env=provider._env(str(home), str(home)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_persistence_survives_restart_without_provider_env(store, monkeypatch):
    cid, credential = connection(store)
    chain(store, cid)
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "MIRA_CODEX_AUTH_JSON", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    restarted = AIStore(AppDatabase(store.db._url), store.root)
    assert Vault(restarted).read(credential)[0]["oauth_token"] == "test-secret-never-returned"
    assert restarted.assignments()["review"]
    restarted.db._sqlite_conn.close()


def test_activation_requires_verified_models_and_database_failure_is_closed(store, monkeypatch):
    cid, credential = connection(store)
    chain(store, cid, ("gpt-6.1-sol",))
    runtime = AIRuntime(store)
    with pytest.raises(ValueError, match="Comprueba"):
        runtime.activate(1)
    store.set_state(f"access:{credential}:gpt-6.1-sol:off", {"status": "verified"})
    runtime.activate(1)
    assert runtime.managed()
    monkeypatch.setattr(store, "state", lambda *args: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError):
        runtime.managed()
    assert (store.root / "managed").exists()


def api_client(store, user):
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request, call_next):
        request.state.user = user
        return await call_next(request)

    app.include_router(create_ai_router(store.db))
    return TestClient(app)


def test_permissions_and_validation_errors_do_not_disclose_secrets(store):
    user = SimpleNamespace(id=2, is_admin=False)
    with api_client(store, user) as client:
        assert client.get("/api/ai/connections").status_code == 403
        store.grant(user.id, ["ai.view"], 1)
        assert client.get("/api/ai/connections").status_code == 200
        assert (
            client.post(
                "/api/ai/connections", json={"name": "test", "provider": "codex-cli"}
            ).status_code
            == 403
        )
    cid, credential = connection(store)
    with api_client(store, SimpleNamespace(id=1, is_admin=True)) as client:
        response = client.post(
            f"/api/ai/connections/{cid}/credentials",
            json={"kind": "not-a-kind", "secret": "DO-NOT-LEAK"},
        )
        assert response.status_code == 422
        assert "DO-NOT-LEAK" not in response.text
        for path in ("connections", "audit", "runs"):
            assert "test-secret" not in client.get("/api/ai/" + path).text
        assert (
            client.post(
                "/api/ai/connections",
                headers={"Origin": "https://attacker.invalid"},
                json={"name": "test", "provider": "codex-cli"},
            ).status_code
            == 403
        )


def test_failed_rotation_preserves_active_then_valid_rotation_activates(store, monkeypatch):
    cid, active = connection(store)
    candidate = Vault(store).stage(cid, "oauth_token", "replacement-secret", 1)
    from mira.ai.runtime import current

    with api_client(store, SimpleNamespace(id=1, is_admin=True)) as client:
        runtime = current()
        invoke = AsyncMock(side_effect=ValueError("authentication failed replacement-secret"))
        mock_broker(runtime, invoke)
        body = {
            "mode": "real",
            "action": "rotation",
            "connection_id": cid,
            "credential_id": candidate,
            "model": "haiku",
        }
        response = client.post("/api/ai/diagnostics", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert store.connection(cid)["active_credential"] == active
        assert "replacement-secret" not in response.text
        mock_broker(runtime, AsyncMock(return_value='{"ok":true}'))
        response = client.post("/api/ai/diagnostics", json=body)
        assert response.json()["status"] == "success"
        assert store.connection(cid)["active_credential"] == candidate


async def test_explicit_api_key_and_sol_max_are_used_without_ambient_credentials(
    store, monkeypatch
):
    cid, credential = connection(store, "responses", "database-only-key")
    monkeypatch.setenv("OPENAI_API_KEY", "obsolete-environment-key")
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"ok":true}'}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    runtime = AIRuntime(store)
    async with runtime.broker.provider(
        store.connection(cid),
        LLMConfig(model="gpt-6.1-sol", reasoning_effort="max", ai_managed=True, max_retries=1),
        credential,
    ) as provider:
        assert await provider.complete([]) == '{"ok":true}'
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer database-only-key"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["reasoning"] == {"effort": "max"}


async def test_managed_adapter_does_not_retry_without_selected_reasoning(store, monkeypatch):
    cid, credential = connection(store, "responses")
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(400, json={"error": "reasoning not supported"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    runtime = AIRuntime(store)
    tool = {"type": "function", "function": {"name": "probe", "parameters": {"type": "object"}}}
    async with runtime.broker.provider(
        store.connection(cid),
        LLMConfig(reasoning_effort="max", ai_managed=True, max_retries=1),
        credential,
    ) as provider:
        with pytest.raises(NonRetriableLLMError):
            await provider.complete_with_tools([], [tool])
    assert len(requests) == 1
    assert json.loads(requests[0].content)["reasoning"]["effort"] == "max"


async def test_renewable_auth_is_refreshed_once_then_chain_continues(store):
    cid, credential = connection(store)
    data, revision = Vault(store).read(credential)
    Vault(store).update(credential, revision, data, {"renewable": True})
    fallback, _ = connection(store, "codex-cli")
    fid = chain(store, cid, ("first", "last"))
    flow = store.flow(fid)
    flow["nodes"][1]["connection_id"] = fallback
    store.publish(
        FlowInput(name="Auth", nodes=flow["nodes"], edges=flow["edges"], expected_version=1), 1, fid
    )
    runtime = AIRuntime(store)
    seen = []

    async def invoke(config):
        seen.append(config.model)
        if config.model == "first":
            raise NonRetriableLLMError("claude_auth_failed")
        return '{"ok":true}'

    mock_broker(runtime, invoke)
    await runtime.provider(LLMConfig()).complete([])
    assert seen == ["first", "first", "last"]
    assert store.runs()[0]["steps"][0]["refresh_attempted"]


async def test_catalog_failure_still_persists_native_session_renewal(store, monkeypatch):
    from mira.ai.catalog import discover
    from mira.llm.codex_cli import CodexCLIProvider

    cid = store.save_connection(
        ConnectionInput(name="exclusive", provider="codex-cli").model_dump(), 1
    )
    old = {"tokens": {"access_token": "old-access", "refresh_token": "old-refresh"}}
    fresh = {"tokens": {"access_token": "new-access", "refresh_token": "new-refresh"}}
    credential = Vault(store).stage(cid, "session", old, 1)
    data, revision = Vault(store).read(credential)
    Vault(store).update(credential, revision, data, {"status": "ready"})
    Vault(store).activate(cid, credential, 1)
    process = SimpleNamespace(
        stdin=MagicMock(),
        stdout=SimpleNamespace(
            readline=AsyncMock(
                side_effect=[
                    b'{"id":1,"result":{}}\n',
                    b'{"id":2,"error":{"message":"failure after renewal"}}\n',
                ]
            )
        ),
    )
    process.stdin.drain = AsyncMock()

    async def spawn(*args, **kwargs):
        CodexCLIProvider._write_auth(
            __import__("pathlib").Path(kwargs["env"]["CODEX_HOME"]) / "auth.json", fresh
        )
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(CodexCLIProvider, "_terminate_process_tree", AsyncMock())
    assert (await discover(store, cid))["status"] == "unverified"
    assert Vault(store).read(credential)[0]["auth"] == fresh


async def test_claude_structured_result_preserves_contract_usage_and_retry_policy(monkeypatch):
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "result": '{"ok":true}',
                        "usage": {
                            "input_tokens": 2,
                            "cache_read_input_tokens": 5,
                            "cache_creation_input_tokens": 1,
                            "output_tokens": 3,
                        },
                    }
                ).encode(),
                b"",
            )
        ),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(ManagedClaude, "_terminate_process_tree", AsyncMock())
    provider = ManagedClaude(
        LLMConfig(reasoning_effort="max"), {"oauth_token": "test-token"}, lambda data: None
    )
    assert await provider.complete([]) == '{"ok":true}'
    assert provider.usage["prompt_tokens"] == 8
    assert provider.usage["completion_tokens"] == 3
    assert provider.usage_available
    env = spawn.call_args.kwargs["env"]
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert env["DISABLE_AUTO_COMPACT"] == "1"
    command = spawn.call_args.args
    assert command[command.index("--output-format") + 1] == "json"


async def test_migration_is_staged_idempotent_and_contains_sol_max_template(store, monkeypatch):
    from mira.ai.migration import prepare

    monkeypatch.setattr(
        "mira.config.load_config",
        lambda: SimpleNamespace(
            llm=LLMConfig(
                provider="claude-cli", fallback_provider="codex-cli", fallback_model="gpt-5.6-sol"
            )
        ),
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "legacy-token")
    monkeypatch.setenv(
        "MIRA_CODEX_AUTH_JSON",
        json.dumps(
            {"tokens": {"access_token": "legacy-access", "refresh_token": "legacy-refresh"}}
        ),
    )
    result = await prepare(store, 1)
    assert await prepare(store, 1) == result
    assert len(store.connections()) == 2
    assert set(store.assignments()) == set(SCENARIOS)
    assert not AIRuntime(store).managed()
    assert all(not c["active_credential"] for c in store.connections())
    node = store.flow(result["template"])["nodes"][-1]
    assert (node["model"], node["reasoning_effort"]) == ("gpt-6.1-sol", "max")
    assert "legacy-token" not in json.dumps(store.connections())


def test_imported_renewable_sessions_cannot_be_forked_or_reintroduced_after_refresh(store):
    cid = store.save_connection(ConnectionInput(name="Codex", provider="codex-cli").model_dump(), 1)
    old = {"tokens": {"access_token": "old-access", "refresh_token": "old-refresh"}}
    fresh = {"tokens": {"access_token": "new-access", "refresh_token": "new-refresh"}}
    credential = Vault(store).stage(cid, "session", old, 1)
    secret, revision = Vault(store).read(credential)
    secret["auth"] = fresh
    Vault(store).update(credential, revision, secret)
    for duplicate in (old, fresh):
        with pytest.raises(ConflictError, match="sesión ya existe"):
            Vault(store).stage(cid, "session", duplicate, 1)
    assert len(store.credentials(cid)) == 1
    assert Vault(store).read(credential)[0]["auth"] == fresh
    other = {"tokens": {"access_token": "other-access", "refresh_token": "other-refresh"}}
    assert Vault(store).stage(cid, "session", other, 1)
    assert "fingerprint" not in json.dumps(store.credentials(cid))


@pytest.mark.parametrize("status", [400, 429, 503])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"retry-after": "not-a-date", "anthropic-ratelimit-requests-reset": "invalid"},
        MagicMock(),
    ],
)
def test_provider_error_classification_survives_missing_or_malformed_rate_headers(status, headers):
    from mira.exceptions import LLMError
    from mira.llm.base import OpenAICompatibleProvider

    response = SimpleNamespace(status_code=status, text="provider failure", headers=headers)
    with pytest.raises(LLMError) as caught:
        OpenAICompatibleProvider._handle_error(response)
    assert isinstance(caught.value, NonRetriableLLMError) == (status == 400)
    assert caught.value.status == status


def test_bedrock_throttling_history_and_logs_do_not_contain_provider_secrets(caplog):
    from mira.ai.runtime import classify
    from mira.llm.bedrock import BedrockProvider, _BedrockThrottlingError

    provider = BedrockProvider(LLMConfig(ai_managed=True), client=SimpleNamespace())
    error = type("ThrottlingException", (Exception,), {})("SENSITIVE_PROVIDER_TEST_VALUE")
    with pytest.raises(_BedrockThrottlingError) as caught:
        provider._handle_api_error(error, "model")
    assert "SENSITIVE_PROVIDER_TEST_VALUE" not in caplog.text
    assert classify(caught.value)[0] == "quota"


async def test_schema_error_mentioning_auth_does_not_quarantine_healthy_credential(store):
    cid, credential = connection(store)
    chain(store, cid, ("invalid", "valid"))
    runtime = AIRuntime(store)

    async def invoke(config):
        return (
            '{"detail":"authentication failure described in the PR"}'
            if config.model == "invalid"
            else '{"answer":"valid"}'
        )

    mock_broker(runtime, invoke)
    tool = {
        "type": "function",
        "function": {
            "name": "probe",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }
    assert (
        await runtime.provider(LLMConfig()).complete_with_tools([], [tool]) == '{"answer":"valid"}'
    )
    assert store.runs()[0]["steps"][0]["cause"] == "invalid"
    assert store.credential_metadata(credential)["status"] == "ready"


def test_resuming_login_is_owner_bound_and_does_not_return_credentials(store):
    from mira.ai.login import LoginManager

    manager = LoginManager(store)
    manager.sessions["login"] = {
        "id": "login",
        "connection_id": "connection",
        "actor": 1,
        "status": "pending",
        "expires_at": time.time() + 600,
        "secret": "PRIVATE_TEST_VALUE",
    }
    assert manager.latest("connection", 1)["id"] == "login"
    assert manager.latest("connection", 2) is None
    assert "PRIVATE_TEST_VALUE" not in json.dumps(manager.latest("connection", 1))
    with pytest.raises(ValueError):
        manager.public("login", 2)
    manager.sessions["login"]["expires_at"] = 0
    assert manager.latest("connection", 1) is None


def test_api_flows_preserve_agentic_tools_and_cli_flows_preserve_prompt_contract(store):
    api_id, _ = connection(store, "responses")
    chain(store, api_id, ("model",))
    api = AIRuntime(store).provider(LLMConfig())
    assert api.supports_tool_calling and api.supports_temperature and api.supports_max_tokens
    cli_id, _ = connection(store)
    chain(store, cli_id, ("claude",))
    cli = AIRuntime(store).provider(LLMConfig())
    assert (
        not cli.supports_tool_calling
        and not cli.supports_temperature
        and not cli.supports_max_tokens
    )


@pytest.mark.skipif(
    not os.environ.get("AI_TEST_DATABASE_URL"), reason="Postgres coverage runs in GitHub Actions"
)
async def test_postgres_versions_encryption_and_cross_process_locks(tmp_path):
    db = AppDatabase(os.environ["AI_TEST_DATABASE_URL"], admin_password="test-only")
    store = AIStore(db, tmp_path / "private")
    cid, credential = connection(store)
    fid = chain(store, cid)
    assert store.flow(fid)["version"] == 1
    secret, rev = Vault(store).read(credential)
    Vault(store).update(credential, rev, secret)
    with pytest.raises(ConflictError):
        Vault(store).update(credential, rev, secret)
    acquired = asyncio.Event()

    async def acquire():
        async with store.session_lock(credential):
            acquired.set()

    async with store.session_lock(credential):
        task = asyncio.create_task(acquire())
        await asyncio.sleep(0.05)
        assert not acquired.is_set()
    await asyncio.wait_for(task, 2)
    assert acquired.is_set()


def test_connection_transport_cannot_redirect_opaque_credentials_but_metadata_can_change(store):
    cid, credential = connection(store, "responses", "opaque-provider-key")
    original = {key: store.connection(cid)[key] for key in ConnectionInput.model_fields}
    with api_client(store, SimpleNamespace(id=1, is_admin=True)) as client:
        for field, value in (
            ("base_url", "https://untrusted.invalid/v1"),
            ("region", "eu-west-1"),
            ("provider", "openai"),
        ):
            response = client.put(f"/api/ai/connections/{cid}", json={**original, field: value})
            assert response.status_code == 400
            assert "opaque-provider-key" not in response.text
            assert store.connection(cid)[field] == original[field]
            assert store.connection(cid)["active_credential"] == credential
        response = client.put(
            f"/api/ai/connections/{cid}", json={**original, "name": "Renamed", "enabled": False}
        )
        assert response.status_code == 200
        assert store.connection(cid)["name"] == "Renamed"
        assert store.connection(cid)["enabled"] is False

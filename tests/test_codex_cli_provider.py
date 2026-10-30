from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mira.config import LLMConfig
from mira.exceptions import LLMError, NonRetriableLLMError
from mira.llm import create_llm
from mira.llm.claude_cli import ClaudeCLIProvider
from mira.llm.codex_cli import CodexCLIProvider
from mira.llm.provider_chain import ProviderChain


class TestCodexCLIProvider:
    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    @pytest.mark.asyncio
    async def test_expired_refresh_token_is_not_retried(self, monkeypatch, stream):
        message = (
            b"Your access token could not be refreshed because your refresh token was already "
            b"used. Please log out and sign in again."
        )
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate.return_value = (
            message if stream == "stdout" else b"",
            message if stream == "stderr" else b"Warning: optional update check failed",
        )
        spawn = AsyncMock(return_value=proc)
        monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))

        with pytest.raises(NonRetriableLLMError) as error:
            await provider._run_codex("return JSON")

        assert error.value.code == "codex_auth_failed"
        spawn.assert_awaited_once()
        assert "refresh token was already used" not in str(error.value)

    @pytest.mark.asyncio
    async def test_concurrent_invocations_read_the_latest_refreshed_auth(
        self, monkeypatch, tmp_path
    ):
        persistent_home = tmp_path / "codex-home"
        persistent_home.mkdir()
        old = {"last_refresh": "2026-10-21T10:00:00Z", "tokens": {"refresh_token": "old"}}
        fresh = {"last_refresh": "2026-10-22T10:00:00Z", "tokens": {"refresh_token": "fresh"}}
        (persistent_home / "auth.json").write_text(json.dumps(old))
        config = LLMConfig(provider="codex-cli", codex_home=str(persistent_home))
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        snapshots = []

        def spawn(*args, **kwargs):
            auth_path = Path(kwargs["env"]["CODEX_HOME"]) / "auth.json"
            position = len(snapshots)
            snapshots.append(json.loads(auth_path.read_text()))
            proc = AsyncMock()
            proc.returncode = 0

            async def communicate(prompt):
                if position == 0:
                    first_started.set()
                    await release_first.wait()
                    auth_path.write_text(json.dumps(fresh))
                return b'{"ok": true}', b""

            proc.communicate.side_effect = communicate
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(side_effect=spawn))
        first = asyncio.create_task(CodexCLIProvider(config)._run_codex("first"))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(CodexCLIProvider(config)._run_codex("second"))
        try:
            await asyncio.sleep(0)
            assert len(snapshots) == 1
        finally:
            release_first.set()
            results = await asyncio.gather(first, second)

        assert results == ['{"ok": true}', '{"ok": true}']
        assert snapshots == [old, fresh]
        assert json.loads((persistent_home / "auth.json").read_text()) == fresh

    @pytest.mark.asyncio
    async def test_cancelled_invocations_release_the_auth_session(self, monkeypatch):
        started = asyncio.Event()
        never_finishes = asyncio.Event()
        blocked = AsyncMock()
        blocked.returncode = None

        async def communicate(prompt):
            started.set()
            await never_finishes.wait()

        blocked.communicate.side_effect = communicate
        healthy = AsyncMock()
        healthy.returncode = 0
        healthy.communicate.return_value = (b'{"ok": true}', b"")
        spawn = AsyncMock(side_effect=[blocked, healthy])
        monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider._terminate_process_tree = AsyncMock()

        running = asyncio.create_task(provider._run_codex("running"))
        await asyncio.wait_for(started.wait(), timeout=1)
        waiting = asyncio.create_task(provider._run_codex("waiting"))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert await asyncio.wait_for(provider._run_codex("healthy"), timeout=1) == '{"ok": true}'
        provider._terminate_process_tree.assert_awaited_once_with(blocked)
        assert spawn.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["review", "walkthrough"])
    async def test_schema_import_entrypoints(self, method):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider.complete_with_tools = AsyncMock(return_value="{}")

        assert await getattr(provider, method)([{"role": "user", "content": "test"}]) == "{}"

        provider.complete_with_tools.assert_awaited_once()
        assert provider.complete_with_tools.call_args.kwargs["tools"][0]["type"] == "function"

    def test_factory_selects_codex_cli_provider(self):
        provider = create_llm(LLMConfig(provider="codex-cli", model="gpt-5-codex"))
        assert isinstance(provider, CodexCLIProvider)

    def test_command_uses_stdin_oauth_cli_not_http_api(self):
        provider = CodexCLIProvider(
            LLMConfig(
                provider="codex-cli",
                model="gpt-5-codex",
                codex_command="/usr/local/bin/codex",
                codex_sandbox="read-only",
            )
        )
        cmd = provider._command("/tmp/out.txt")
        assert cmd == [
            "/usr/local/bin/codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-last-message",
            "/tmp/out.txt",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-m",
            "gpt-5-codex",
            "-",
        ]

    def test_command_passes_reasoning_effort(self):
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", model="gpt-5-codex", reasoning_effort="high")
        )
        cmd = provider._command("/tmp/out.txt")
        assert cmd.count('model_reasoning_effort="high"') == 1

    def test_command_omits_reasoning_effort_when_off(self):
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", model="gpt-5-codex", reasoning_effort="off")
        )
        cmd = provider._command("/tmp/out.txt")
        assert not any("model_reasoning_effort" in part for part in cmd)

    def test_command_omits_model_for_codex_default(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli", model="codex-default"))
        cmd = provider._command("/tmp/out.txt")
        assert "-m" not in cmd
        assert cmd[-1] == "-"

    def test_command_rejects_shell_metacharacters_or_arguments(self):
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", codex_command="codex --dangerous-flag")
        )

        with pytest.raises(ValueError, match="Invalid codex_command"):
            provider._command("/tmp/out.txt")

    def test_subprocess_environment_excludes_service_secrets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "do-not-inherit")
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        monkeypatch.setenv("MIRA_WEBHOOK_SECRET", "do-not-inherit")
        monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", codex_home="/trusted/codex-home")
        )

        env = provider._env(str(tmp_path), "/private/runtime-codex-home")

        assert env["PATH"] == "/usr/local/bin:/usr/bin"
        assert env["HOME"] == str(tmp_path)
        assert env["CODEX_HOME"] == "/private/runtime-codex-home"
        assert "GITHUB_TOKEN" not in env
        assert "DATABASE_URL" not in env
        assert "MIRA_WEBHOOK_SECRET" not in env

    def test_runtime_codex_home_copies_only_auth_file(self, tmp_path):
        source = tmp_path / "mounted-codex-home"
        source.mkdir()
        (source / "auth.json").write_text('{"tokens": "secret"}')
        (source / "config.toml").write_text("dangerous_user_config = true")
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli", codex_home=str(source)))

        runtime_home = Path(provider._prepare_codex_home(str(tmp_path / "invocation")))

        assert (runtime_home / "auth.json").read_text() == '{"tokens": "secret"}'
        assert not (runtime_home / "config.toml").exists()

    def test_runtime_codex_home_uses_sealed_json_without_a_host_mount(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MIRA_CODEX_AUTH_JSON", '{"tokens": "sealed"}')
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))

        runtime_home = Path(provider._prepare_codex_home(str(tmp_path / "invocation")))

        assert (runtime_home / "auth.json").read_text() == '{"tokens": "sealed"}'
        assert (runtime_home / "auth.json").stat().st_mode & 0o777 == 0o600

    def test_refreshed_auth_is_persisted_without_exposing_other_codex_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        persistent_home = tmp_path / "persistent-codex-home"
        monkeypatch.setenv(
            "MIRA_CODEX_AUTH_JSON",
            '{"last_refresh":"2026-09-23T10:00:00Z","tokens":{"refresh_token":"old"}}',
        )
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", codex_home=str(persistent_home))
        )
        invocation = tmp_path / "invocation"
        runtime_home = Path(provider._prepare_codex_home(str(invocation)))
        (runtime_home / "config.toml").write_text("must_not_persist = true")
        (runtime_home / "auth.json").write_text(
            '{"last_refresh":"2026-09-24T10:00:00Z","tokens":{"refresh_token":"new"}}'
        )

        provider._persist_codex_auth(str(runtime_home))

        persisted = (persistent_home / "auth.json").read_text()
        assert '"refresh_token": "new"' in persisted
        assert not (persistent_home / "config.toml").exists()
        assert (persistent_home / "auth.json").stat().st_mode & 0o777 == 0o600

    def test_stale_invocation_cannot_overwrite_newer_persistent_auth(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        persistent_home = tmp_path / "persistent-codex-home"
        persistent_home.mkdir()
        (persistent_home / "auth.json").write_text(
            '{"last_refresh":"2026-09-24T10:00:00Z","tokens":{"refresh_token":"new"}}'
        )
        monkeypatch.setenv(
            "MIRA_CODEX_AUTH_JSON",
            '{"last_refresh":"2026-09-23T10:00:00Z","tokens":{"refresh_token":"seed"}}',
        )
        provider = CodexCLIProvider(
            LLMConfig(provider="codex-cli", codex_home=str(persistent_home))
        )
        runtime_home = Path(provider._prepare_codex_home(str(tmp_path / "invocation")))
        (runtime_home / "auth.json").write_text(
            '{"last_refresh":"2026-09-22T10:00:00Z","tokens":{"refresh_token":"stale"}}'
        )

        provider._persist_codex_auth(str(runtime_home))

        assert '"refresh_token":"new"' in (persistent_home / "auth.json").read_text()

    def test_command_disables_user_rules_and_shell_environment_inheritance(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))

        cmd = provider._command("/tmp/out.txt")

        assert "--ignore-user-config" in cmd
        assert "--ignore-rules" in cmd
        assert 'shell_environment_policy.inherit="none"' in cmd

    def test_extracts_fenced_json(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        assert provider._extract_json_object('Here you go:\n```json\n{"comments": []}\n```') == (
            '{"comments": []}'
        )

    def test_rejects_non_object_json_output(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))

        with pytest.raises(LLMError, match="JSON object"):
            provider._extract_json_object('[{"comments": []}]')

    @pytest.mark.asyncio
    async def test_complete_json_mode_returns_json_only(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider._run_codex = AsyncMock(return_value='text before {"ok": true} text after')  # type: ignore[method-assign]

        result = await provider.complete([{"role": "user", "content": "return json"}])

        assert result == '{"ok": true}'
        provider._run_codex.assert_awaited_once()
        assert provider.total_prompt_tokens > 0
        assert provider.total_completion_tokens > 0

    @pytest.mark.asyncio
    async def test_complete_json_mode_counts_extracted_response_tokens(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider._run_codex = AsyncMock(return_value='text before {"ok": true} text after')  # type: ignore[method-assign]
        provider.count_tokens = lambda text: len(text)  # type: ignore[method-assign]

        result = await provider.complete([{"role": "user", "content": "return json"}])

        assert result == '{"ok": true}'
        assert provider.total_completion_tokens == len('{"ok": true}')

    @pytest.mark.asyncio
    async def test_complete_with_tools_prompts_for_tool_arguments_json(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider._run_codex = AsyncMock(return_value='{"comments": [], "summary": "ok"}')  # type: ignore[method-assign]
        tool = {
            "type": "function",
            "function": {
                "name": "submit_review",
                "parameters": {
                    "type": "object",
                    "properties": {"comments": {"type": "array"}, "summary": {"type": "string"}},
                    "required": ["comments", "summary"],
                },
            },
        }

        result = await provider.complete_with_tools(
            [{"role": "user", "content": "review this"}], tools=[tool]
        )

        await_args = provider._run_codex.await_args
        assert await_args is not None
        prompt = await_args.args[0]
        assert "Return ONLY a JSON object containing the arguments for `submit_review`" in prompt
        assert '"required": [' in prompt
        assert result == '{"comments": [], "summary": "ok"}'

    @pytest.mark.asyncio
    async def test_subprocess_starts_in_an_isolated_process_group(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b'{"ok": true}', b"")
        spawn = AsyncMock(return_value=proc)
        monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))

        result = await provider._run_codex("return JSON")

        assert result == '{"ok": true}'
        await_args = spawn.await_args
        assert await_args is not None
        assert await_args.kwargs["start_new_session"] is True

    @pytest.mark.asyncio
    async def test_process_group_exit_race_does_not_rekill_direct_child(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 123
        proc.wait = AsyncMock()
        monkeypatch.setattr("os.killpg", MagicMock(side_effect=ProcessLookupError))

        await provider._terminate_process_tree(proc)

        proc.kill.assert_not_called()
        proc.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exited_group_leader_still_triggers_descendant_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        proc = MagicMock()
        proc.returncode = 0
        proc.pid = 456
        proc.wait = AsyncMock()
        killpg = MagicMock()
        monkeypatch.setattr("os.killpg", killpg)

        await provider._terminate_process_tree(proc)

        killpg.assert_called_once_with(456, signal.SIGKILL)
        proc.kill.assert_not_called()
        proc.wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_agentic_defers_without_starting_a_second_cli_review(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider._run_codex = AsyncMock(return_value='{"comments": []}')  # type: ignore[method-assign]

        msg = await provider.complete_agentic([{"role": "user", "content": "hi"}], tools=[])

        assert msg == {"content": "", "tool_calls": []}
        provider._run_codex.assert_not_awaited()


class TestClaudeAndFallbackProvider:
    @pytest.mark.parametrize("method", ["complete", "review", "walkthrough"])
    @pytest.mark.asyncio
    async def test_next_call_starts_with_primary_after_fallback(self, method):
        chain = create_llm(
            LLMConfig(
                provider="claude-cli", fallback_provider="codex-cli", fallback_model="gpt-5.6-sol"
            )
        )
        assert isinstance(chain, ProviderChain)
        primary = AsyncMock(
            side_effect=[LLMError("claude_usage_limit", model="fable"), "recovered"]
        )
        fallback = AsyncMock(return_value="fallback response")
        setattr(chain.primary, method, primary)
        setattr(chain.fallback, method, fallback)
        messages = [{"role": "user", "content": "review"}]

        assert await getattr(chain, method)(messages) == "fallback response"
        assert await getattr(chain, method)(messages) == "recovered"

        assert primary.await_count == 2
        fallback.assert_awaited_once()
        assert chain.last_provider == "primary"

    @pytest.mark.asyncio
    async def test_fallback_auth_failure_keeps_its_non_retriable_error(self):
        chain = create_llm(
            LLMConfig(
                provider="claude-cli", fallback_provider="codex-cli", fallback_model="gpt-5.6-sol"
            )
        )
        assert isinstance(chain, ProviderChain)
        chain.primary.complete = AsyncMock(side_effect=LLMError("claude_timeout", seconds=1))
        chain.fallback.complete = AsyncMock(
            side_effect=NonRetriableLLMError("codex_auth_file_missing", path="test-auth.json")
        )

        with pytest.raises(NonRetriableLLMError) as error:
            await chain.complete([{"role": "user", "content": "review"}])

        assert error.value.code == "codex_auth_file_missing"
        chain.fallback.complete.assert_awaited_once()

    def test_factory_builds_claude_to_codex_chain(self):
        provider = create_llm(
            LLMConfig(
                provider="claude-cli",
                model="claude-sonnet-5",
                fallback_provider="codex-cli",
                fallback_model="codex-default",
            )
        )
        assert isinstance(provider, ProviderChain)
        assert isinstance(provider.primary, ClaudeCLIProvider)
        assert isinstance(provider.fallback, CodexCLIProvider)

    def test_claude_command_disables_tools_settings_and_mcp(self):
        provider = ClaudeCLIProvider(LLMConfig(provider="claude-cli"))
        command = provider._command()
        assert command[:2] == ["claude", "-p"]
        assert command[2:4] == ["--permission-mode", "dontAsk"]
        assert command[4:6] == ["--tools", ""]
        assert "--strict-mcp-config" in command
        assert command[7:9] == ["--setting-sources", ""]
        assert "--no-session-persistence" in command
        assert "--disable-slash-commands" in command
        assert "--bare" not in command

    def test_claude_command_uses_dashboard_selected_anthropic_model(self):
        provider = ClaudeCLIProvider(
            LLMConfig(provider="claude-cli", model="anthropic/claude-fable-5.1")
        )

        command = provider._command()

        model_index = command.index("--model")
        assert command[model_index + 1] == "claude-fable-5-1"
        assert provider.effective_model == "claude-fable-5-1"

    @pytest.mark.parametrize(
        "message",
        [
            "You've hit your limit",
            "You've reached your Fable limit. Switch to another model to continue.",
            "You've reached your Sonnet limit. Switch to another model to continue.",
        ],
    )
    @pytest.mark.asyncio
    async def test_claude_quota_exhaustion_is_eligible_for_codex_fallback(
        self, monkeypatch, message
    ):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate.return_value = (message.encode(), b"")
        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
        provider = ClaudeCLIProvider(LLMConfig(provider="claude-cli"))

        with pytest.raises(LLMError, match="usage limit reached") as error:
            await provider._run_codex("review")
        assert not isinstance(error.value, NonRetriableLLMError)
        assert error.value.code == "claude_usage_limit"
        assert error.value.safe_message == "Claude Code model usage limit reached"

    @pytest.mark.parametrize("stderr", [b"", b"Warning: optional update check failed"])
    @pytest.mark.asyncio
    async def test_fable_limit_calls_codex_fallback(self, monkeypatch, caplog, stderr):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate.return_value = (
            b"You've reached your Fable limit. Switch to another model to continue.",
            stderr,
        )
        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
        chain = create_llm(
            LLMConfig(
                provider="claude-cli",
                model="anthropic/claude-fable-5.1",
                fallback_provider="codex-cli",
                fallback_model="gpt-5.6-sol",
            )
        )
        assert isinstance(chain, ProviderChain)
        chain.fallback.complete = AsyncMock(return_value='{"comments": []}')

        assert await chain.complete([{"role": "user", "content": "review"}]) == '{"comments": []}'
        chain.fallback.complete.assert_awaited_once()
        assert chain.attempted_models == ["claude-fable-5-1", "gpt-5.6-sol"]
        assert chain.fallback_attempted is True
        assert "Claude Code model usage limit reached" in caplog.text

    @pytest.mark.parametrize("detail", [b"Invalid token", b"Error: unknown option '--unsupported'"])
    @pytest.mark.asyncio
    async def test_claude_auth_and_configuration_failures_do_not_call_codex(
        self, monkeypatch, detail
    ):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate.return_value = (b"", detail)
        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
        chain = create_llm(
            LLMConfig(
                provider="claude-cli",
                fallback_provider="codex-cli",
                fallback_model="gpt-5.6-sol",
            )
        )
        assert isinstance(chain, ProviderChain)
        chain.fallback.complete = AsyncMock()

        with pytest.raises(NonRetriableLLMError):
            await chain.complete([{"role": "user", "content": "review"}])
        chain.fallback.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cli_review_and_walkthrough_use_exported_tool_schemas(self):
        provider = CodexCLIProvider(LLMConfig(provider="codex-cli"))
        provider.complete_with_tools = AsyncMock(return_value='{"comments": []}')  # type: ignore[method-assign]

        assert await provider.review([{"role": "user", "content": "review"}]) == '{"comments": []}'
        assert (
            await provider.walkthrough([{"role": "user", "content": "walkthrough"}])
            == '{"comments": []}'
        )
        assert provider.complete_with_tools.await_count == 2

    def test_claude_environment_only_contains_its_oauth_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret")
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        provider = ClaudeCLIProvider(LLMConfig(provider="claude-cli"))
        env = provider._env(str(tmp_path))
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "secret"
        assert "DATABASE_URL" not in env

    @pytest.mark.asyncio
    async def test_transient_claude_failure_uses_codex_once(self):
        primary = MagicMock()
        fallback = MagicMock()
        primary.config = LLMConfig(provider="claude-cli", model="anthropic/claude-fable-5.1")
        fallback.config = LLMConfig(provider="codex-cli", model="gpt-5.6-sol")
        primary.effective_model = "claude-fable-5-1"
        fallback.effective_model = "gpt-5.6-sol"
        primary.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        fallback.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        primary.complete = AsyncMock(side_effect=LLMError("codex_timeout", seconds=1))
        fallback.complete = AsyncMock(return_value='{"comments": []}')
        chain = ProviderChain(primary, fallback)

        assert await chain.complete([{"role": "user", "content": "review"}]) == '{"comments": []}'
        fallback.complete.assert_awaited_once()
        assert chain.last_provider == "fallback"
        assert chain.attempted_models == ["claude-fable-5-1", "gpt-5.6-sol"]
        assert chain.attempted_providers == ["claude-cli", "codex-cli"]
        assert chain.fallback_attempted is True

    @pytest.mark.asyncio
    async def test_invalid_claude_credentials_do_not_use_codex(self):
        primary = MagicMock()
        fallback = MagicMock()
        primary.usage = fallback.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        primary.complete = AsyncMock(
            side_effect=NonRetriableLLMError("no_api_key", api_key_env="x")
        )
        fallback.complete = AsyncMock()
        chain = ProviderChain(primary, fallback)

        with pytest.raises(NonRetriableLLMError):
            await chain.complete([{"role": "user", "content": "review"}])
        fallback.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_configuration_error_does_not_use_codex(self):
        primary = MagicMock()
        fallback = MagicMock()
        primary.usage = fallback.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        primary.complete = AsyncMock(side_effect=ValueError("invalid Claude command"))
        fallback.complete = AsyncMock()
        chain = ProviderChain(primary, fallback)

        with pytest.raises(ValueError, match="invalid Claude command"):
            await chain.complete([{"role": "user", "content": "review"}])
        fallback.complete.assert_not_awaited()

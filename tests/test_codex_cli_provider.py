from __future__ import annotations

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

    def test_claude_command_is_restricted_and_not_bare(self):
        provider = ClaudeCLIProvider(LLMConfig(provider="claude-cli"))
        command = provider._command()
        assert command[:3] == ["claude", "-p", "--restricted"]
        assert "--bare" not in command

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
        primary.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        fallback.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        primary.complete = AsyncMock(side_effect=LLMError("codex_timeout", seconds=1))
        fallback.complete = AsyncMock(return_value='{"comments": []}')
        chain = ProviderChain(primary, fallback)

        assert await chain.complete([{"role": "user", "content": "review"}]) == '{"comments": []}'
        fallback.complete.assert_awaited_once()
        assert chain.last_provider == "fallback"

    @pytest.mark.asyncio
    async def test_invalid_claude_credentials_do_not_use_codex(self):
        primary = MagicMock()
        fallback = MagicMock()
        primary.usage = fallback.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        primary.complete = AsyncMock(side_effect=NonRetriableLLMError("no_api_key", api_key_env="x"))
        fallback.complete = AsyncMock()
        chain = ProviderChain(primary, fallback)

        with pytest.raises(NonRetriableLLMError):
            await chain.complete([{"role": "user", "content": "review"}])
        fallback.complete.assert_not_awaited()

"""Restricted Claude Code CLI provider for non-interactive PR review."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from mira.exceptions import LLMError, NonRetriableLLMError
from mira.llm.codex_cli import _SAFE_ENV_KEYS, CodexCLIProvider


class ClaudeCLIProvider(CodexCLIProvider):
    """Claude Code invocation with no repository checkout or inherited config."""

    def _env(self, runtime_home: str, runtime_codex_home: str = "") -> dict[str, str]:
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if not token:
            raise NonRetriableLLMError("no_api_key", api_key_env="CLAUDE_CODE_OAUTH_TOKEN")
        env = {key: value for key, value in os.environ.items() if key in _SAFE_ENV_KEYS}
        env["HOME"] = runtime_home
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return env

    def _command(self) -> list[str]:
        command = self.config.claude_command or "claude"
        if any(char in command for char in (" ", "\t", "\n", ";", "|", "&")):
            raise ValueError("Invalid claude_command: set a single executable path/name without arguments.")
        # `--restricted` is the Claude Code non-interactive restriction mode.
        # The working directory is empty and HOME is per-invocation, so neither
        # CLAUDE.md nor a user configuration can be discovered.
        return [command, "-p", "--restricted", "--output-format", "text"]

    async def _run_codex(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="mira-claude-") as tmpdir:
            runtime_home = str(Path(tmpdir) / "runtime")
            Path(runtime_home).mkdir(mode=0o700)
            cmd = self._command()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=self._env(runtime_home), cwd=runtime_home,
                    start_new_session=os.name == "posix",
                )
            except FileNotFoundError as exc:
                raise NonRetriableLLMError("codex_command_not_found", command=self.config.claude_command) from exc
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=self.config.claude_timeout_seconds)
            except TimeoutError as exc:
                await self._terminate_process_tree(proc)
                raise LLMError("codex_timeout", seconds=self.config.claude_timeout_seconds) from exc
            if proc.returncode != 0:
                detail = (stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")).strip()
                lowered = detail.lower()
                if any(text in lowered for text in ("unauthorized", "forbidden", "invalid token", "authentication")):
                    raise NonRetriableLLMError("codex_exit_failed", exit_code=proc.returncode, detail="authentication failed")
                # Only known service-side/transient conditions may cross the
                # Claude → Codex boundary.  Bad flags, unsupported models and
                # other client failures stay operationally visible.
                if any(text in lowered for text in (
                    "429", "rate limit", "timeout", "timed out", "temporarily",
                    "server error", "internal server", " 500", " 502", " 503", " 504",
                )):
                    raise LLMError("codex_exit_failed", exit_code=proc.returncode, detail=detail[-2000:])
                raise NonRetriableLLMError(
                    "codex_exit_failed", exit_code=proc.returncode, detail="non-transient Claude CLI failure"
                )
            return stdout.decode("utf-8", errors="replace").strip()

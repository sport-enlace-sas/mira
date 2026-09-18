"""Hardened OpenCodeReview delegation preprocessor.

OCR is deliberately used only for deterministic file/rule planning.  Models
remain owned by Mira's configured Claude -> Codex provider chain.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mira.models import PRInfo

logger = logging.getLogger(__name__)


@dataclass
class OCRPlan:
    status: str = "disabled"  # disabled | used | degraded
    version: str = ""
    reviewed_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    rule_groups: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""  # safe classification only; never subprocess output

    @property
    def rules_context(self) -> str:
        if not self.rule_groups:
            return ""
        blocks = ["## Corporate OpenCodeReview rules (deterministic)"]
        for group in self.rule_groups:
            text = group.get("rule")
            files = group.get("files", [])
            if isinstance(text, str) and text.strip():
                blocks.append(
                    f"Applies to: {', '.join(str(p) for p in files if isinstance(p, str))}\n{text}"
                )
        return "\n\n".join(blocks)


class OpenCodeReviewDelegation:
    """Run the JSON-only OCR delegation interface in a throw-away checkout."""

    def __init__(
        self, command: str, rule_path: str, timeout_seconds: int, max_rule_groups: int
    ) -> None:
        self.command = command
        self.rule_path = rule_path
        self.timeout_seconds = timeout_seconds
        self.max_rule_groups = max_rule_groups

    async def prepare(self, provider: Any, pr: PRInfo) -> OCRPlan:
        started = time.monotonic()
        plan = OCRPlan()
        root: Path | None = None
        try:
            token_supplier = getattr(provider, "_resolve_token", None)
            if not callable(token_supplier) or not pr.base_sha or not pr.head_sha:
                raise RuntimeError("unsupported_provider")
            token = await token_supplier()
            root = Path(tempfile.mkdtemp(prefix="mira-ocr-"))
            await self._checkout(root, pr, token)
            self._isolate_repository_configuration(root)
            plan.version = await self._version(root)
            preview = await self._ocr(
                root,
                "delegate",
                "preview",
                "--from",
                pr.base_sha,
                "--to",
                pr.head_sha,
                "--format",
                "json",
            )
            if preview.get("schema_version") != "1" or not isinstance(
                preview.get("reviewable_files"), list
            ):
                raise RuntimeError("invalid_preview")
            plan.reviewed_paths = self._paths(preview["reviewable_files"])
            plan.excluded_paths = self._paths(preview.get("excluded_files", []))
            if plan.reviewed_paths:
                rules = await self._ocr(
                    root,
                    "delegate",
                    "rule",
                    *plan.reviewed_paths,
                    "--from",
                    pr.base_sha,
                    "--to",
                    pr.head_sha,
                    "--format",
                    "json",
                )
                if rules.get("schema_version") != "1" or not isinstance(rules.get("groups"), list):
                    raise RuntimeError("invalid_rules")
                plan.rule_groups = self._corporate_rule_groups(
                    rules["groups"], set(plan.reviewed_paths)
                )[: self.max_rule_groups]
            plan.status = "used"
        except (FileNotFoundError, TimeoutError):
            plan.status, plan.error = "degraded", "ocr_unavailable"
        except RuntimeError as exc:
            plan.status, plan.error = (
                "degraded",
                str(exc)
                if str(exc) in {"unsupported_provider", "invalid_preview", "invalid_rules"}
                else "ocr_failed",
            )
        except Exception:
            logger.warning("OCR delegation failed", exc_info=True)
            plan.status, plan.error = "degraded", "ocr_failed"
        finally:
            if root is not None:
                self._make_writable(root)
                shutil.rmtree(root, ignore_errors=True)
            plan.duration_ms = int((time.monotonic() - started) * 1000)
        return plan

    async def _checkout(self, root: Path, pr: PRInfo, token: str) -> None:
        # The credential is passed as a process-local Git HTTP header, never as
        # a remote URL, command argument or persisted git config.
        (root / ".mira-ocr-home").mkdir(mode=0o700)
        env = self._env(token, root)
        await self._git(root, env, "init", "--quiet")
        await self._git(root, env, "config", "core.hooksPath", os.devnull)
        await self._git(root, env, "config", "protocol.file.allow", "never")
        await self._git(
            root, env, "remote", "add", "origin", f"https://github.com/{pr.owner}/{pr.repo}.git"
        )
        await self._git(
            root, env, "fetch", "--no-tags", "--depth=100", "origin", pr.base_sha, pr.head_sha
        )
        await self._git(root, env, "checkout", "--detach", "--quiet", pr.head_sha)

    def _isolate_repository_configuration(self, root: Path) -> None:
        """Remove untrusted OCR config and make the checkout read-only.

        OCR's upstream resolver deliberately reads ``.opencodereview/rule.json``
        from a repository. That is useful for standalone OCR, but a PR must
        never be able to alter the corporate review policy.
        """
        repo_config = root / ".opencodereview"
        if repo_config.is_symlink() or repo_config.is_file():
            repo_config.unlink(missing_ok=True)
        elif repo_config.is_dir():
            shutil.rmtree(repo_config)
        (root / ".mira-ocr-home").mkdir(mode=0o700, exist_ok=True)
        for directory, _, files in os.walk(root):
            os.chmod(directory, 0o500)
            for name in files:
                candidate = Path(directory, name)
                if not candidate.is_symlink():
                    os.chmod(candidate, 0o400)

    @staticmethod
    def _make_writable(root: Path) -> None:
        for directory, _, files in os.walk(root):
            try:
                os.chmod(directory, 0o700)
                for name in files:
                    candidate = Path(directory, name)
                    if not candidate.is_symlink():
                        os.chmod(candidate, 0o600)
            except OSError:
                pass

    def _env(self, token: str, root: Path | None = None) -> dict[str, str]:
        # Start from a small whitelist; do not inherit Git hooks/config or a
        # developer's HOME. Git supports repeated config through these vars.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root / ".mira-ocr-home") if root else tempfile.gettempdir(),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        if token:
            # Git's HTTPS transport authenticates installation access tokens
            # through HTTP Basic auth. The token stays in a process-local
            # environment value: never in the remote URL, command arguments,
            # repository config, logs, or OCR input.
            basic_auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic_auth}",
                }
            )
        return env

    async def _git(self, root: Path, env: dict[str, str], *args: str) -> None:
        await self._run(root, env, "git", *args)

    async def _version(self, root: Path) -> str:
        output = await self._run(root, self._env("", root), self.command, "--version")
        return output.strip()[:120]

    async def _ocr(self, root: Path, *args: str) -> dict[str, Any]:
        command = list(args)
        if self.rule_path:
            command.extend(("--rule", self.rule_path))
        output = await self._run(root, self._env("", root), self.command, *command)
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid_preview") from exc
        if not isinstance(result, dict):
            raise RuntimeError("invalid_preview")
        return result

    async def _run(self, root: Path, env: dict[str, str], executable: str, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            cwd=root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            raise RuntimeError("ocr_failed")
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _paths(entries: list[Any]) -> list[str]:
        paths: list[str] = []
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            if (
                isinstance(path, str)
                and path
                and not path.startswith(("/", "../"))
                and "\0" not in path
            ):
                paths.append(path)
        return paths

    @staticmethod
    def _corporate_rule_groups(
        entries: list[Any], reviewed_paths: set[str]
    ) -> list[dict[str, Any]]:
        """Keep only the small, validated contract passed to a model."""
        groups: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("rule"), str):
                continue
            files = [
                path
                for path in entry.get("files", [])
                if isinstance(path, str) and path in reviewed_paths
            ]
            groups.append(
                {
                    "files": files,
                    "rule": entry["rule"],
                    "source": str(entry.get("source", ""))[:48],
                    "pattern": str(entry.get("pattern", ""))[:256],
                }
            )
        return groups

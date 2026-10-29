"""Short-lived, owner-bound official CLI login sessions. No custom OAuth client."""

from __future__ import annotations

import asyncio
import os
import pty
import re
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mira.ai.store import AIStore, Vault, new_id
from mira.llm.codex_cli import _SAFE_ENV_KEYS, CodexCLIProvider

_ALLOWED = {
    "auth.openai.com",
    "chatgpt.com",
    "platform.openai.com",
    "claude.ai",
    "console.anthropic.com",
    "platform.claude.com",
    "auth.claude.com",
}


class LoginManager:
    def __init__(self, store: AIStore) -> None:
        self.store = store
        self.sessions: dict[str, dict] = {}

    def public(self, login_id: str, actor: int) -> dict:
        session = self.sessions.get(login_id)
        if not session or session["actor"] != actor:
            raise ValueError("Sesión de conexión inexistente o de otro usuario")
        return {
            k: session.get(k)
            for k in (
                "id",
                "connection_id",
                "status",
                "url",
                "user_code",
                "credential_id",
                "expires_at",
            )
        }

    async def start(self, connection_id: str, actor: int) -> dict:
        connection = self.store.connection(connection_id)
        provider = connection["provider"]
        if provider not in {"codex-cli", "claude-cli"}:
            raise ValueError("Este proveedor requiere importar una clave")
        if any(
            s["connection_id"] == connection_id and s["status"] == "pending"
            for s in self.sessions.values()
        ):
            raise ValueError("Ya hay una conexión en curso para esta cuenta")
        # Expired sessions contain no tokens and may be discarded.
        self.sessions = {
            key: value for key, value in self.sessions.items() if value["expires_at"] > time.time()
        }
        session: dict[str, Any] = {
            "id": new_id(),
            "actor": actor,
            "connection_id": connection_id,
            "status": "pending",
            "expires_at": time.time() + 600,
        }
        self.sessions[session["id"]] = session
        session["task"] = asyncio.create_task(self._run(session, provider))
        self.store.audit(actor, "login.started", connection_id)
        return self.public(session["id"], actor)

    async def submit(self, login_id: str, actor: int, code: str) -> dict:
        self.public(login_id, actor)
        session = self.sessions[login_id]
        if session["status"] != "pending" or session["expires_at"] < time.time():
            raise ValueError("La sesión de conexión expiró")
        if not code.strip() or len(code) > 4096 or "\n" in code or "\r" in code:
            raise ValueError("Código de autorización inválido")
        if "fd" not in session:
            raise ValueError("El cliente todavía no está listo")
        os.write(session["fd"], (code.strip() + "\n").encode())
        return self.public(login_id, actor)

    async def cancel(self, login_id: str, actor: int) -> None:
        self.public(login_id, actor)
        task = self.sessions[login_id]["task"]
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self, session: dict, provider: str) -> None:
        process, master, slave = None, None, None
        try:
            async with self.store.session_lock("login:" + session["connection_id"]):
                with tempfile.TemporaryDirectory(prefix="mira-login-") as temporary:
                    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
                    env.update(
                        HOME=temporary,
                        CODEX_HOME=temporary,
                        CLAUDE_CONFIG_DIR=temporary,
                        BROWSER="false",
                        NO_COLOR="1",
                        TERM="dumb",
                    )
                    command = (
                        ["codex", "login", "--device-auth"]
                        if provider == "codex-cli"
                        else ["claude", "auth", "login", "--claudeai"]
                    )
                    master, slave = pty.openpty()
                    os.set_blocking(master, False)
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdin=slave,
                        stdout=slave,
                        stderr=slave,
                        env=env,
                        cwd=temporary,
                        start_new_session=True,
                    )
                    os.close(slave)
                    slave = None
                    session["fd"] = master
                    buffer = ""
                    async with asyncio.timeout(600):
                        while process.returncode is None:
                            try:
                                chunk = os.read(master, 8192)
                            except (BlockingIOError, OSError):
                                chunk = b""
                            buffer = (buffer + chunk.decode(errors="replace"))[-32768:]
                            clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", buffer)
                            for url in re.findall(r"https://[^\s\x1b<>]+", clean):
                                parsed = urlsplit(url)
                                if parsed.hostname in _ALLOWED and not set(
                                    parse_qs(parsed.query)
                                ) & {"code", "access_token", "refresh_token", "token"}:
                                    session["url"] = url.rstrip(".,)")
                            if provider == "codex-cli":
                                match = re.search(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b", clean)
                                if match:
                                    session["user_code"] = match.group()
                            await asyncio.sleep(0.15)
                        await process.wait()
                    if process.returncode != 0:
                        session["status"] = "failed"
                        return
                    path = Path(temporary) / (
                        "auth.json" if provider == "codex-cli" else ".credentials.json"
                    )
                    auth = CodexCLIProvider._read_auth(path)
                    if auth is None:
                        session["status"] = "failed"
                        return
                    credential_id = Vault(self.store).stage(
                        session["connection_id"], "session", auth, session["actor"]
                    )
                    session.update(status="connected", credential_id=credential_id)
                    self.store.audit(session["actor"], "login.completed", session["connection_id"])
        except asyncio.CancelledError:
            session["status"] = "cancelled"
            raise
        except Exception:
            session["status"] = "failed"
        finally:
            if process is not None:
                await CodexCLIProvider._terminate_process_tree(process)
            session.pop("fd", None)
            for fd in (master, slave):
                if fd is not None:
                    os.close(fd)

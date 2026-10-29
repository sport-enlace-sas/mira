"""Portable SQL store. Secret reads are restricted to the credential broker.

Postgres operations use independent transactions; SQLite uses the application's
connection (including in-memory test databases). No provider call holds a DB
write transaction. Immutable versions use compare-and-swap publication.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import hmac
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mira.ai.models import PERMISSIONS, FlowInput

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_connections (
 id TEXT PRIMARY KEY, data TEXT NOT NULL, active_credential TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_credentials (
 id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES ai_connections(id),
 revision INTEGER NOT NULL, ciphertext TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_credentials_connection ON ai_credentials(connection_id);
CREATE TABLE IF NOT EXISTS ai_session_aliases (
 fingerprint TEXT PRIMARY KEY, credential_id TEXT NOT NULL REFERENCES ai_credentials(id)
);
CREATE TABLE IF NOT EXISTS ai_flows (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_flow_versions (
 flow_id TEXT NOT NULL REFERENCES ai_flows(id), version INTEGER NOT NULL,
 data TEXT NOT NULL, PRIMARY KEY (flow_id, version)
);
CREATE TABLE IF NOT EXISTS ai_assignments (scenario TEXT PRIMARY KEY, flow_id TEXT NOT NULL REFERENCES ai_flows(id));
CREATE TABLE IF NOT EXISTS ai_runs (
 id TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, job_id TEXT NOT NULL,
 data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_runs_job ON ai_runs(job_id, created_at);
CREATE TABLE IF NOT EXISTS ai_audit (
 id TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL, actor INTEGER NOT NULL,
 action TEXT NOT NULL, resource TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_permissions (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_state (id TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class ConflictError(ValueError):
    """A concurrent update superseded the version supplied by the caller."""


def new_id() -> str:
    return uuid.uuid4().hex


def reported_expiry(secret: dict) -> float | None:
    """Informational expiry only; a real request proves credential validity."""
    auth = secret.get("auth", {})
    expires = auth.get("claudeAiOauth", {}).get("expiresAt")
    if isinstance(expires, (int, float)) and expires > 0:
        return float(expires) / 1000
    token = auth.get("tokens", {}).get("access_token", "")
    try:
        payload = token.split(".")[1]
        expires = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get(
            "exp"
        )
        return float(expires) if expires else None
    except (ValueError, IndexError, TypeError, AttributeError):
        return None


class AIStore:
    def __init__(self, db: Any, root: Path | None = None) -> None:
        self.db = db
        self._mutex = threading.RLock()
        self._sqlite_path = (
            db._sqlite_conn.execute("PRAGMA database_list").fetchone()[2]
            if db._backend == "sqlite"
            else ""
        )
        if root is None:
            # Only a location is inferred; no encryption key comes from env.
            if db._backend == "sqlite":
                filename = db._sqlite_conn.execute("PRAGMA database_list").fetchone()[2]
                root = Path(filename).parent / "ai-private" if filename else Path("data/ai-private")
            else:
                root = Path("/data/mira/ai-private")
        self.root = root.resolve()
        with self.transaction() as cur:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    cur.execute(statement)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        if self.db._backend == "sqlite":
            with self._mutex:
                # File databases need their own transaction: unrelated dashboard
                # operations must never commit a half-published flow/version.
                conn = (
                    sqlite3.connect(self._sqlite_path)
                    if self._sqlite_path
                    else self.db._sqlite_conn
                )
                conn.execute("PRAGMA foreign_keys=ON")
                cursor = conn.cursor()
                try:
                    yield cursor
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    cursor.close()
                    if self._sqlite_path:
                        conn.close()
        else:
            from mira.db.postgres import connect

            conn = connect(self.db._url)
            try:
                with conn.transaction(), conn.cursor() as cursor:
                    yield cursor
            finally:
                conn.close()

    def sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.db._backend != "sqlite" else statement

    def rows(self, statement: str, args: tuple = ()) -> list:
        with self.transaction() as cur:
            cur.execute(self.sql(statement), args)
            return cur.fetchall()

    def execute(self, statement: str, args: tuple = ()) -> int:
        with self.transaction() as cur:
            cur.execute(self.sql(statement), args)
            return cur.rowcount

    def state(self, key: str, default: Any = None) -> Any:
        rows = self.rows("SELECT data FROM ai_state WHERE id=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO ai_state(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (key, json.dumps(value)),
        )

    def permissions(self, user: Any) -> list[str]:
        if user.is_admin:
            return list(PERMISSIONS)
        rows = self.rows("SELECT data FROM ai_permissions WHERE user_id=?", (user.id,))
        return json.loads(rows[0][0]) if rows else []

    def grant(self, user_id: int, permissions: list[str], actor: int) -> None:
        if not set(permissions) <= set(PERMISSIONS):
            raise ValueError("Permiso desconocido")
        self.execute(
            "INSERT INTO ai_permissions(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
            (user_id, json.dumps(sorted(set(permissions)))),
        )
        self.audit(actor, "permissions.updated", str(user_id))

    def audit(self, actor: int, action: str, resource: str) -> None:
        # Never accept arbitrary diagnostic/error/credential dictionaries here.
        self.execute(
            "INSERT INTO ai_audit VALUES(?,?,?,?,?)",
            (new_id(), time.time(), actor, action, resource),
        )

    def connections(self) -> list[dict]:
        return [
            {**json.loads(r[1]), "id": r[0], "active_credential": r[2]}
            for r in self.rows("SELECT id,data,active_credential FROM ai_connections ORDER BY id")
        ]

    def connection(self, connection_id: str) -> dict:
        rows = self.rows(
            "SELECT data,active_credential FROM ai_connections WHERE id=?", (connection_id,)
        )
        if not rows:
            raise ValueError("Conexión inexistente")
        return {**json.loads(rows[0][0]), "id": connection_id, "active_credential": rows[0][1]}

    def save_connection(self, data: dict, actor: int, connection_id: str | None = None) -> str:
        connection_id = connection_id or new_id()
        action = "connection.created"
        if self.rows("SELECT id FROM ai_connections WHERE id=?", (connection_id,)):
            previous = self.connection(connection_id)
            action = "connection.updated"
            if previous["enabled"] != data["enabled"]:
                action = "connection.enabled" if data["enabled"] else "connection.disabled"
            if previous["provider"] != data["provider"]:
                raise ValueError("El proveedor de una conexión existente no puede cambiar")
        self.execute(
            "INSERT INTO ai_connections(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (connection_id, json.dumps(data)),
        )
        self.audit(actor, action, connection_id)
        return connection_id

    def credentials(self, connection_id: str) -> list[dict]:
        # Explicit projection excludes ciphertext.
        return [
            {"id": r[0], "revision": r[1], **json.loads(r[2])}
            for r in self.rows(
                "SELECT id,revision,data FROM ai_credentials WHERE connection_id=? ORDER BY id",
                (connection_id,),
            )
        ]

    def credential_metadata(self, credential_id: str) -> dict:
        rows = self.rows(
            "SELECT connection_id,revision,data FROM ai_credentials WHERE id=?", (credential_id,)
        )
        if not rows:
            raise ValueError("Credencial inexistente")
        return {
            "id": credential_id,
            "connection_id": rows[0][0],
            "revision": rows[0][1],
            **json.loads(rows[0][2]),
        }

    def flows(self) -> list[dict]:
        return [
            {"id": r[0], "name": r[1], "version": r[2]}
            for r in self.rows("SELECT id,name,version FROM ai_flows ORDER BY name")
        ]

    def flow(self, flow_id: str, version: int | None = None) -> dict:
        if version is None:
            rows = self.rows("SELECT version FROM ai_flows WHERE id=?", (flow_id,))
            if not rows:
                raise ValueError("Flujo inexistente")
            version = rows[0][0]
        rows = self.rows(
            "SELECT data FROM ai_flow_versions WHERE flow_id=? AND version=?", (flow_id, version)
        )
        if not rows:
            raise ValueError("Versión de flujo inexistente")
        return {**json.loads(rows[0][0]), "id": flow_id, "version": version}

    def publish(self, flow: FlowInput, actor: int, flow_id: str | None = None) -> str:
        for node in flow.nodes:
            self.connection(node.connection_id)
        flow_id = flow_id or new_id()
        with self.transaction() as cur:
            if flow.expected_version == 0:
                cur.execute(
                    self.sql("INSERT INTO ai_flows VALUES(?,?,0) ON CONFLICT(id) DO NOTHING"),
                    (flow_id, flow.name),
                )
            cur.execute(
                self.sql("UPDATE ai_flows SET version=version+1,name=? WHERE id=? AND version=?"),
                (flow.name, flow_id, flow.expected_version),
            )
            if cur.rowcount != 1:
                raise ConflictError("El flujo cambió; recarga antes de publicar")
            cur.execute(
                self.sql("INSERT INTO ai_flow_versions VALUES(?,?,?)"),
                (
                    flow_id,
                    flow.expected_version + 1,
                    flow.model_dump_json(exclude={"expected_version"}),
                ),
            )
        self.audit(actor, "flow.published", flow_id)
        return flow_id

    def assignments(self) -> dict[str, str]:
        return dict(self.rows("SELECT scenario,flow_id FROM ai_assignments"))

    def assign(self, assignments: dict[str, str], actor: int) -> None:
        from mira.ai.models import SCENARIOS

        if not set(assignments) <= set(SCENARIOS):
            raise ValueError("Escenario desconocido")
        with self.transaction() as cur:
            for scenario, flow_id in assignments.items():
                cur.execute(
                    self.sql(
                        "INSERT INTO ai_assignments VALUES(?,?) ON CONFLICT(scenario) DO UPDATE SET flow_id=excluded.flow_id"
                    ),
                    (scenario, flow_id),
                )
        self.audit(actor, "assignments.updated", "scenarios")

    def save_run(self, run: dict) -> None:
        self.execute(
            "INSERT INTO ai_runs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (run["id"], run["created_at"], str(run.get("job_id", "")), json.dumps(run)),
        )

    def runs(self, job_id: str | None = None, limit: int = 100) -> list[dict]:
        query = "SELECT data FROM ai_runs"
        args: tuple = ()
        if job_id is not None:
            query += " WHERE job_id=?"
            args = (job_id,)
        return [
            json.loads(r[0])
            for r in self.rows(
                query + " ORDER BY created_at DESC LIMIT ?", (*args, min(limit, 200))
            )
        ]

    def _private_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("El almacén privado no puede ser un enlace")
        self.root.chmod(0o700)

    @asynccontextmanager
    async def session_lock(self, session: str) -> AsyncIterator[None]:
        """Lock snapshot → native refresh → CAS persistence across processes."""
        if self.db._backend != "sqlite":
            from mira.db.postgres import connect

            conn = connect(self.db._url)
            conn.autocommit = True
            key = int.from_bytes(hashlib.sha256(session.encode()).digest()[:8], "big", signed=True)
            try:
                with conn.cursor() as cur:
                    while True:
                        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                        if cur.fetchone()[0]:
                            break
                        await asyncio.sleep(0.1)
                yield
            finally:
                conn.close()  # Session-scoped advisory lock released on disconnect.
        else:
            self._private_dir()
            lock_dir = self.root / "locks"
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            path = lock_dir / hashlib.sha256(session.encode()).hexdigest()
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
                yield
            finally:
                os.close(fd)


class Vault:
    def __init__(self, store: AIStore) -> None:
        self.store = store

    def _key(self) -> bytes:
        self.store._private_dir()
        fd = os.open(self.store.root / "key.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return self._read_or_create_key()
        finally:
            os.close(fd)

    def _read_or_create_key(self) -> bytes:
        path = self.store.root / "vault.key"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if self.store.rows("SELECT id FROM ai_credentials LIMIT 1"):
                raise ValueError("Falta la clave del almacén: restaura su respaldo") from None
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                return self._read_or_create_key()
            with os.fdopen(fd, "wb") as handle:
                handle.write(os.urandom(32))
                handle.flush()
                os.fsync(handle.fileno())
            return self._read_or_create_key()
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("La clave del almacén requiere un archivo privado con modo 0600")
            key = handle.read(33)
        if len(key) != 32:
            raise ValueError("Clave de almacén inválida; restaura su respaldo")
        return key

    def seal(self, credential_id: str, secret: dict) -> str:
        nonce = os.urandom(12)
        encrypted = AESGCM(self._key()).encrypt(
            nonce, json.dumps(secret).encode(), credential_id.encode()
        )
        return base64.b64encode(nonce + encrypted).decode()

    def _session_alias(self, secret: dict) -> str | None:
        auth = secret.get("auth", {})
        token = auth.get("tokens", {}).get("refresh_token")
        provider = "codex"
        if not token:
            token = auth.get("claudeAiOauth", {}).get("refreshToken")
            provider = "claude"
        if not isinstance(token, str) or not token:
            return None
        return hmac.new(self._key(), (provider + ":" + token).encode(), hashlib.sha256).hexdigest()

    def _register_session(self, cursor: Any, alias: str | None, credential_id: str) -> None:
        if alias is None:
            return
        cursor.execute(
            self.store.sql(
                "INSERT INTO ai_session_aliases VALUES(?,?) ON CONFLICT(fingerprint) DO NOTHING"
            ),
            (alias, credential_id),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                self.store.sql("SELECT credential_id FROM ai_session_aliases WHERE fingerprint=?"),
                (alias,),
            )
            if cursor.fetchone()[0] != credential_id:
                raise ConflictError(
                    "Esta sesión ya existe en MIRA; reutiliza su conexión o inicia una sesión nueva"
                )

    def read(self, credential_id: str) -> tuple[dict, int]:
        rows = self.store.rows(
            "SELECT ciphertext,revision FROM ai_credentials WHERE id=?", (credential_id,)
        )
        if not rows:
            raise ValueError("Credencial inexistente")
        blob = base64.b64decode(rows[0][0])
        try:
            secret = json.loads(
                AESGCM(self._key()).decrypt(blob[:12], blob[12:], credential_id.encode())
            )
        except Exception:
            raise ValueError(
                "No se puede abrir la credencial; comprueba la clave de recuperación"
            ) from None
        return secret, rows[0][1]

    def stage(self, connection_id: str, kind: str, secret: dict | str, actor: int) -> str:
        self.store.connection(connection_id)
        from mira.ai.credentials import normalize_secret

        data = normalize_secret(self.store.connection(connection_id)["provider"], kind, secret)
        credential_id = new_id()
        ciphertext = self.seal(credential_id, data)
        metadata = {
            "kind": kind,
            "status": "pending",
            "created_at": time.time(),
            "created_by": actor,
            "renewable": bool(data.get("renewable")),
            "checked_at": None,
            "expires_at": reported_expiry(data),
        }
        alias = self._session_alias(data)
        with self.store.transaction() as cur:
            cur.execute(
                self.store.sql("INSERT INTO ai_credentials VALUES(?,?,1,?,?)"),
                (credential_id, connection_id, ciphertext, json.dumps(metadata)),
            )
            self._register_session(cur, alias, credential_id)
        self.store.audit(actor, "credential.staged", credential_id)
        return credential_id

    def update(
        self, credential_id: str, revision: int, secret: dict, metadata: dict | None = None
    ) -> None:
        current = self.store.credential_metadata(credential_id)
        data = {k: v for k, v in current.items() if k not in {"id", "revision", "connection_id"}}
        data.update(metadata or {})
        data["expires_at"] = reported_expiry(secret)
        ciphertext, alias = self.seal(credential_id, secret), self._session_alias(secret)
        with self.store.transaction() as cur:
            cur.execute(
                self.store.sql(
                    "UPDATE ai_credentials SET ciphertext=?,data=?,revision=revision+1 WHERE id=? AND revision=?"
                ),
                (ciphertext, json.dumps(data), credential_id, revision),
            )
            if cur.rowcount != 1:
                raise ConflictError(
                    "La credencial fue renovada por otra operación; se descartó la escritura antigua"
                )
            self._register_session(cur, alias, credential_id)

    def activate(
        self, connection_id: str, credential_id: str, actor: int, expected_active: str | None = None
    ) -> None:
        metadata = self.store.credential_metadata(credential_id)
        if metadata["connection_id"] != connection_id or metadata["status"] != "ready":
            raise ValueError("Comprueba la credencial antes de activarla")
        current = self.store.connection(connection_id)["active_credential"]
        if current and current != credential_id:
            previous = self.store.credential_metadata(current)
            if metadata["created_at"] < previous["created_at"]:
                raise ConflictError("Una credencial más reciente ya está activa")
        expected = current if expected_active is None else expected_active
        if (
            self.store.execute(
                "UPDATE ai_connections SET active_credential=? WHERE id=? AND active_credential=?",
                (credential_id, connection_id, expected),
            )
            != 1
        ):
            raise ConflictError(
                "La conexión cambió durante la prueba; se conservó la credencial activa"
            )
        self.store.audit(actor, "credential.activated", credential_id)

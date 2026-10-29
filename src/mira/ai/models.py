"""Public, secret-free contracts for AI administration."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

PERMISSIONS = ("ai.view", "ai.credentials.manage", "ai.flows.manage", "ai.diagnostics.run")
SCENARIOS = {
    "review": "Revisión de PR",
    "security": "Seguridad",
    "indexing": "Indexación",
    "walkthrough": "Walkthrough",
    "summary": "Resúmenes",
    "self_critique": "Autocrítica",
    "fix_verification": "Verificación de correcciones",
    "conversation": "Conversaciones",
    "thread_reply": "Respuestas a hilos",
    "learning": "Aprendizaje",
    "dependencies": "Dependencias",
    "overlap": "Análisis de solapamientos",
}


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    provider: Literal["claude-cli", "codex-cli", "openai", "responses", "bedrock"]
    base_url: str = "https://api.openai.com/v1"
    region: str = "us-east-1"
    enabled: bool = True

    @model_validator(mode="after")
    def safe_endpoint(self) -> ConnectionInput:
        from mira.config import LLMConfig

        parts = urlsplit(self.base_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("El endpoint no debe contener credenciales, query ni fragmento")
        LLMConfig(base_url=self.base_url)
        return self


class CredentialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["api_key", "session", "oauth_token", "aws"]
    # Only accepted in request bodies; never included in response DTOs or audit events.
    secret: dict | str = Field(repr=False)


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    connection_id: str
    model: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_./:@+-]+$")
    reasoning_effort: Literal["off", "low", "medium", "high", "xhigh", "max"] = "off"
    timeout: int = Field(default=120, ge=1, le=7200)
    retries: int = Field(default=0, ge=0, le=10)
    # Explicit capability budget, also validated against discovered limits.
    context_window: int = Field(default=120000, ge=1024)
    position: dict[str, float] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str


class FlowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    nodes: list[FlowNode] = Field(min_length=1)
    edges: list[FlowEdge]
    expected_version: int = Field(default=0, ge=0)

    def ordered_nodes(self) -> list[FlowNode]:
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or len(self.edges) != len(nodes) - 1:
            raise ValueError("El flujo debe ser una cadena lineal conectada")
        outgoing: dict[str, str] = {}
        incoming: set[str] = set()
        for edge in self.edges:
            if (
                edge.source not in nodes
                or edge.target not in nodes
                or edge.source in outgoing
                or edge.target in incoming
            ):
                raise ValueError("No se permiten bifurcaciones ni nodos desconectados")
            outgoing[edge.source] = edge.target
            incoming.add(edge.target)
        roots = set(nodes) - incoming
        if len(roots) != 1:
            raise ValueError("El flujo necesita un único inicio y no puede tener ciclos")
        current = roots.pop()
        ordered: list[FlowNode] = []
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            ordered.append(nodes[current])
            if current not in outgoing:
                break
            current = outgoing[current]
        if len(ordered) != len(nodes):
            raise ValueError("No se permiten ciclos ni nodos desconectados")
        return ordered

    @model_validator(mode="after")
    def linear(self) -> FlowInput:
        self.ordered_nodes()
        return self


class DiagnosticInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["simulation", "real"] = "simulation"
    flow_id: str | None = None
    connection_id: str | None = None
    credential_id: str | None = None
    model: str | None = None
    reasoning_effort: Literal["off", "low", "medium", "high", "xhigh", "max"] = "off"
    faults: dict[str, Literal["quota", "auth", "timeout", "invalid", "success"]] = Field(
        default_factory=dict
    )
    action: Literal["connection", "model", "rotation", "flow"] = "flow"

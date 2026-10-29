import { useCallback, useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import {
  Background,
  Controls,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection as GraphConnection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  aiApi,
  type Connection,
  type Diagnostic,
  type DiagnosticInput,
  type Flow,
  type FlowNode,
  type Login,
  type Model,
  type Run,
} from "@/lib/api/ai"
import { useAuth } from "@/lib/auth"
import { useDocumentTitle } from "@/lib/hooks"

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
function Label({
  children,
  className = "",
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <label className={`text-sm font-medium ${className}`}>{children}</label>
  )
}
const date = (value?: number | null) =>
  value ? new Date(value * 1000).toLocaleString() : "Sin comprobar"
const efforts = ["off", "low", "medium", "high", "xhigh", "max"]
const statusLabel: Record<string, string> = {
  pending: "Pendiente de prueba",
  ready: "Comprobada",
  auth_blocked: "Reconexión requerida",
  listed: "Listado por proveedor",
  unverified: "Sin verificar",
  verified: "Acceso comprobado",
  success: "Correcto",
  failed: "Falló",
  skipped: "Omitido",
  cancelled: "Cancelado",
  running: "En curso",
}

export function RunTrace({ run }: { run: Run }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {run.scenario} · {statusLabel[run.status] || run.status}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {run.mode === "simulation" ? "Simulación" : "Solicitud real"} ·
          versión {run.flow_version} · {date(run.created_at)} · {run.id}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {run.steps.map((step, index) => (
          <div
            key={`${step.node_id}-${index}`}
            className="rounded-md border p-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <b>
                {index + 1}. {step.node_name}
              </b>
              <Badge
                variant={step.result === "success" ? "secondary" : "outline"}
              >
                {statusLabel[step.result] || step.result}
              </Badge>
              <span>
                {step.connection_name || step.provider} · {step.model} ·{" "}
                {step.reasoning_effort}
              </span>
            </div>
            <p className="mt-1 text-muted-foreground">
              Intento {step.attempt} · {step.duration_ms} ms · entrada{" "}
              {step.usage.prompt_tokens ?? "no informada"} / salida{" "}
              {step.usage.completion_tokens ?? "no informada"}
              {step.refresh_attempted ? " · renovación intentada" : ""}
            </p>
            {step.cause && (
              <p>
                {step.cause}: {step.action}
                {step.reset_at
                  ? ` · reinicio informado ${date(step.reset_at)}`
                  : ""}
                {step.retry_at
                  ? ` · próximo intento ${date(step.retry_at)}`
                  : ""}
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function ConnectionCard({
  connection,
  canManage,
  canTest,
  reload,
  perform,
}: {
  connection: Connection
  canManage: boolean
  canTest: boolean
  reload: () => Promise<void>
  perform: (input: DiagnosticInput) => Promise<void>
}) {
  const [kind, setKind] = useState("api_key")
  const [secret, setSecret] = useState("")
  const [credential, setCredential] = useState(
    connection.active_credential || connection.credentials.at(-1)?.id || ""
  )
  const [model, setModel] = useState(
    connection.provider === "codex-cli"
      ? "gpt-6.1-sol"
      : connection.provider === "claude-cli"
        ? "claude-haiku-4-5"
        : ""
  )
  const [effort, setEffort] = useState(
    connection.provider === "codex-cli" ? "max" : "off"
  )
  const [models, setModels] = useState<Model[]>([])
  const [login, setLogin] = useState<Login | null>(null)
  const [code, setCode] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [now] = useState(() => Date.now() / 1000)
  useEffect(() => {
    aiApi
      .models(connection.id)
      .then((data) => setModels(data.models))
      .catch(() => {})
  }, [connection.id, connection.active_credential])
  useEffect(() => {
    if (!login || login.status !== "pending") return
    const timer = setInterval(() => {
      aiApi
        .loginStatus(login.id)
        .then(async (value) => {
          setLogin(value)
          if (value.status === "connected") {
            setCredential(value.credential_id || "")
            await reload()
          }
        })
        .catch((e) => setError(String(e)))
    }, 1500)
    return () => clearInterval(timer)
  }, [login, reload])
  const action = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError("")
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  const imported = async () => {
    let value: unknown = secret
    if (kind === "session" || kind === "aws") value = JSON.parse(secret)
    const result = await aiApi.importCredential(connection.id, kind, value)
    setSecret("")
    setCredential(result.id)
    await reload()
  }
  const probe = (rotation = false) =>
    perform({
      mode: "real",
      action: rotation ? "rotation" : "model",
      connection_id: connection.id,
      credential_id: credential || undefined,
      model,
      reasoning_effort: effort,
    })
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">{connection.name}</CardTitle>
          <Badge variant="outline">
            {connection.provider} ·{" "}
            {connection.enabled ? "Habilitada" : "Desactivada"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {["claude-cli", "codex-cli"].includes(connection.provider) && (
            <Button
              disabled={!canManage || busy}
              variant="outline"
              onClick={() =>
                void action(async () =>
                  setLogin(await aiApi.login(connection.id))
                )
              }
            >
              Conectar cuenta
            </Button>
          )}
          <Button
            variant="outline"
            disabled={!canTest || busy}
            onClick={() =>
              void action(async () => {
                const result = await aiApi.refreshModels(connection.id)
                setModels(result.models)
              })
            }
          >
            Consultar modelos
          </Button>
          <Button
            variant="ghost"
            disabled={!canManage || busy}
            onClick={() =>
              void action(async () => {
                await aiApi.saveConnection(
                  {
                    name: connection.name,
                    provider: connection.provider,
                    base_url: connection.base_url,
                    region: connection.region,
                    enabled: !connection.enabled,
                  },
                  connection.id
                )
                await reload()
              })
            }
          >
            {connection.enabled ? "Desactivar" : "Activar conexión"}
          </Button>
        </div>
        {login && (
          <div className="space-y-2 rounded-md border p-3 text-sm">
            <b>Sesión exclusiva de MIRA · {login.status}</b>
            <p>
              Completa la verificación en el proveedor. Después prueba y activa
              la credencial nueva.
            </p>
            {login.url && (
              <a
                className="block text-primary underline"
                href={login.url}
                target="_blank"
                rel="noreferrer"
              >
                Abrir acceso oficial
              </a>
            )}
            {login.user_code && (
              <p>
                Código de dispositivo: <code>{login.user_code}</code>
              </p>
            )}
            {connection.provider === "claude-cli" &&
              login.status === "pending" && (
                <div className="flex gap-2">
                  <Input
                    type="password"
                    autoComplete="off"
                    placeholder="Código devuelto por Claude, cuando lo solicite"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                  <Button
                    disabled={!code}
                    onClick={() =>
                      void action(async () => {
                        await aiApi.loginCode(login.id, code)
                        setCode("")
                      })
                    }
                  >
                    Enviar código
                  </Button>
                </div>
              )}
            {login.status === "pending" && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  void action(async () => {
                    await aiApi.cancelLogin(login.id)
                    setLogin(null)
                  })
                }
              >
                Cancelar conexión
              </Button>
            )}
          </div>
        )}
        {canManage && (
          <details>
            <summary className="cursor-pointer text-sm font-medium">
              Importar clave o archivo de sesión
            </summary>
            <div className="mt-3 space-y-3">
              <Label>Tipo</Label>
              <select
                className={selectClass}
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value)
                  setSecret("")
                }}
              >
                <option value="api_key">API key</option>
                {["claude-cli", "codex-cli"].includes(connection.provider) && (
                  <option value="session">Archivo de sesión JSON</option>
                )}
                {connection.provider === "claude-cli" && (
                  <option value="oauth_token">
                    Token manual sin renovación
                  </option>
                )}
                {connection.provider === "bedrock" && (
                  <option value="aws">Credenciales AWS JSON</option>
                )}
              </select>
              {["session", "aws"].includes(kind) ? (
                <Input
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) void file.text().then(setSecret)
                    e.target.value = ""
                  }}
                />
              ) : (
                <Input
                  type="password"
                  autoComplete="new-password"
                  placeholder="Secreto de escritura únicamente"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                />
              )}
              <Button
                disabled={!secret || busy}
                onClick={() => void action(imported)}
              >
                Importar como candidata
              </Button>
              <p className="text-xs text-muted-foreground">
                La credencial activa se conserva hasta que la nueva supere una
                prueba real.
              </p>
            </div>
          </details>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label>Versión de credencial</Label>
            <select
              className={selectClass}
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
            >
              <option value="">Seleccionar</option>
              {connection.credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id.slice(0, 8)} · {statusLabel[c.status] || c.status}
                  {c.id === connection.active_credential
                    ? " · activa"
                    : ""} · {c.renewable ? "renovable" : "reemplazo manual"}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label>Modelo para prueba real</Label>
            <Input
              list={`models-${connection.id}`}
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id={`models-${connection.id}`}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {statusLabel[m.status]} · {m.name}
                </option>
              ))}
            </datalist>
          </div>
          <div className="space-y-1">
            <Label>Esfuerzo</Label>
            <select
              className={selectClass}
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
            >
              {efforts.map((e) => (
                <option key={e}>{e}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={!canTest || !credential || !model || busy}
            onClick={() => void action(() => probe())}
          >
            Probar conexión / modelo
          </Button>
          <Button
            disabled={!canTest || !canManage || !credential || !model || busy}
            onClick={() => void action(() => probe(true))}
          >
            Validar / rotar credencial
          </Button>
        </div>
        {connection.credentials.map((c) => (
          <p key={c.id} className="text-xs text-muted-foreground">
            {c.id.slice(0, 8)} · revisión {c.revision} ·{" "}
            {statusLabel[c.status] || c.status} · {date(c.checked_at)}
            {c.expires_at
              ? ` · expiración informada: ${date(c.expires_at)}`
              : ""}
          </p>
        ))}
        {models.length > 0 && (
          <details>
            <summary className="cursor-pointer text-sm">
              Catálogo ({models.length})
            </summary>
            <div className="mt-2 max-h-60 overflow-auto text-xs">
              {models.map((m) => (
                <p key={m.id} className="border-b py-2">
                  {m.id} · {statusLabel[m.status] || m.status} · {m.source}
                  {m.reasoning_efforts?.length
                    ? ` · esfuerzos: ${m.reasoning_efforts.join(", ")}`
                    : ""}
                  {Object.entries(m.access || {})
                    .filter(([, access]) => access.status)
                    .map(
                      ([effort, access]) =>
                        ` · ${effort}: ${statusLabel[access.status!] || access.status} (${date(access.checked_at)})`
                    )}
                  {m.quota?.until && m.quota.until > now
                    ? ` · reintento desde ${date(m.quota.until)}`
                    : ""}
                </p>
              ))}
            </div>
          </details>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function FlowEditor({
  flow,
  connections,
  save,
  editable,
}: {
  flow: Flow
  connections: Connection[]
  save: (flow: Flow) => Promise<void>
  editable: boolean
}) {
  const [name, setName] = useState(flow.name)
  const [data, setData] = useState<FlowNode[]>(flow.nodes)
  const [nodes, setNodes] = useState<Node[]>(
    flow.nodes.map((n, i) => ({
      id: n.id,
      position:
        n.position?.x !== undefined ? n.position : { x: i * 290, y: 70 },
      data: { label: `${n.name}\n${n.model} · ${n.reasoning_effort}` },
      style: {
        width: 235,
        whiteSpace: "pre-line",
        background: "var(--card)",
        color: "var(--foreground)",
        borderColor: "var(--border)",
      },
    }))
  )
  const [edges, setEdges] = useState<Edge[]>(
    flow.edges.map((e, i) => ({
      ...e,
      id: `edge-${i}`,
      label: "Falla",
      animated: true,
    }))
  )
  const [selected, setSelected] = useState(data[0]?.id || "")
  const [models, setModels] = useState<Model[]>([])
  const [error, setError] = useState("")
  const node = data.find((n) => n.id === selected)
  useEffect(() => {
    if (node?.connection_id)
      aiApi
        .models(node.connection_id)
        .then((result) => setModels(result.models))
        .catch(() => setModels([]))
  }, [node?.connection_id])
  const update = (patch: Partial<FlowNode>) => {
    setData((previous) =>
      previous.map((n) => (n.id === selected ? { ...n, ...patch } : n))
    )
    if (node)
      setNodes((previous) =>
        previous.map((n) =>
          n.id === selected
            ? {
                ...n,
                data: {
                  label: `${patch.name ?? node.name}\n${patch.model ?? node.model} · ${patch.reasoning_effort ?? node.reasoning_effort}`,
                },
              }
            : n
        )
      )
  }
  const add = () => {
    const id = crypto.randomUUID()
    const connection = connections[0]
    const value: FlowNode = {
      id,
      name: `Paso ${data.length + 1}`,
      connection_id: connection?.id || "",
      model:
        connection?.provider === "codex-cli"
          ? "gpt-6.1-sol"
          : "claude-haiku-4-5",
      reasoning_effort: "off",
      timeout: 120,
      retries: 0,
      context_window: 120000,
    }
    const terminal = data.find((n) => !edges.some((e) => e.source === n.id))
    setData((previous) => [...previous, value])
    setNodes((previous) => [
      ...previous,
      {
        id,
        position: { x: previous.length * 290, y: 70 },
        data: { label: `${value.name}\n${value.model}` },
      },
    ])
    setSelected(id)
    if (terminal)
      setEdges((previous) => [
        ...previous,
        {
          id: crypto.randomUUID(),
          source: terminal.id,
          target: id,
          label: "Falla",
          animated: true,
        },
      ])
  }
  const publish = async () => {
    setError("")
    try {
      await save({
        ...flow,
        name,
        nodes: data.map((n) => ({
          ...n,
          position: nodes.find((g) => g.id === n.id)?.position,
        })),
        edges: edges.map((e) => ({ source: e.source, target: e.target })),
      })
    } catch (e) {
      setError(String(e))
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Editor de flujo · versión {flow.version || "nueva"}
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Conecta la salida de un nodo con la entrada del siguiente. Cada
          solicitud empieza en el primer nodo y termina con la primera respuesta
          válida.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Input
            aria-label="Nombre del flujo"
            disabled={!editable}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={!editable || !connections.length}
            onClick={add}
          >
            Añadir paso
          </Button>
          <Button disabled={!editable} onClick={() => void publish()}>
            Publicar versión
          </Button>
        </div>
        <div className="h-80 rounded-md border">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={(changes: NodeChange[]) => {
              setNodes((previous) => applyNodeChanges(changes, previous))
              setData((previous) =>
                previous.filter(
                  (n) =>
                    !changes.some((c) => c.type === "remove" && c.id === n.id)
                )
              )
            }}
            onEdgesChange={(changes: EdgeChange[]) =>
              setEdges((previous) => applyEdgeChanges(changes, previous))
            }
            onConnect={(connection: GraphConnection) =>
              setEdges((previous) =>
                addEdge(
                  { ...connection, label: "Falla", animated: true },
                  previous
                )
              )
            }
            onNodeClick={(_, n) => setSelected(n.id)}
            nodesDraggable={editable}
            nodesConnectable={editable}
            elementsSelectable={true}
            deleteKeyCode={editable ? ["Backspace", "Delete"] : null}
            fitView
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
        {node && (
          <fieldset
            disabled={!editable}
            className="grid gap-3 rounded-md border p-4 md:grid-cols-3"
          >
            <div className="space-y-1">
              <Label>Nombre del paso</Label>
              <Input
                value={node.name}
                onChange={(e) => update({ name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>Conexión / cuenta</Label>
              <select
                className={selectClass}
                value={node.connection_id}
                onChange={(e) =>
                  update({ connection_id: e.target.value, model: "" })
                }
              >
                <option value="">Seleccionar</option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.provider}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label>Modelo</Label>
              <Input
                list="flow-models"
                value={node.model}
                onChange={(e) => update({ model: e.target.value })}
              />
              <datalist id="flow-models">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {statusLabel[m.status]} · {m.name}
                  </option>
                ))}
              </datalist>
            </div>
            <div className="space-y-1">
              <Label>Esfuerzo de razonamiento</Label>
              <select
                className={selectClass}
                value={node.reasoning_effort}
                onChange={(e) => update({ reasoning_effort: e.target.value })}
              >
                {efforts.map((e) => (
                  <option key={e}>{e}</option>
                ))}
              </select>
            </div>
            {(
              [
                ["timeout", "Timeout (segundos)"],
                ["retries", "Reintentos del paso"],
                ["context_window", "Ventana de contexto"],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="space-y-1">
                <Label>{label}</Label>
                <Input
                  type="number"
                  min={key === "retries" ? 0 : 1}
                  value={node[key]}
                  onChange={(e) => update({ [key]: Number(e.target.value) })}
                />
              </div>
            ))}
            <Button
              variant="outline"
              onClick={() => {
                setData((previous) => previous.filter((n) => n.id !== selected))
                setNodes((previous) =>
                  previous.filter((n) => n.id !== selected)
                )
                setEdges((previous) =>
                  previous.filter(
                    (e) => e.source !== selected && e.target !== selected
                  )
                )
                setSelected("")
              }}
            >
              Eliminar paso
            </Button>
          </fieldset>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function AIPage() {
  useDocumentTitle("Configuración de IA")
  const { user } = useAuth()
  const [query] = useSearchParams()
  const [section, setSection] = useState(
    query.has("job") || query.has("run") ? "diagnostics" : "connections"
  )
  const [status, setStatus] = useState<Awaited<
    ReturnType<typeof aiApi.status>
  > | null>(null)
  const [connections, setConnections] = useState<Connection[]>([])
  const [flows, setFlows] = useState<Pick<Flow, "id" | "name" | "version">[]>(
    []
  )
  const [flow, setFlow] = useState<Flow | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [assignments, setAssignments] = useState<Record<string, string>>({})
  const [runs, setRuns] = useState<Run[]>([])
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof aiApi.audit>>>(
    []
  )
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const [busy, setBusy] = useState(false)
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null)
  const [mode, setMode] = useState<"simulation" | "real">("simulation")
  const [faults, setFaults] = useState<Record<string, string>>({})
  const [connectionName, setConnectionName] = useState("")
  const [provider, setProvider] = useState("claude-cli")
  const [endpoint, setEndpoint] = useState("https://api.openai.com/v1")
  const [region, setRegion] = useState("us-east-1")
  const permissions = status?.permissions || user?.permissions || []
  const can = (p: string) => !!user?.is_admin || permissions.includes(p)
  const reload = useCallback(async () => {
    const [s, c, f, a, r, history] = await Promise.all([
      aiApi.status(),
      aiApi.connections(),
      aiApi.flows(),
      aiApi.assignments(),
      aiApi.runs(query.get("job") || undefined),
      aiApi.audit(),
    ])
    setStatus(s)
    setConnections(c)
    setFlows(f)
    setAssignments(a)
    setRuns(r)
    setAudit(history)
  }, [query])
  useEffect(() => {
    void Promise.resolve()
      .then(reload)
      .catch((e) => setError(String(e)))
  }, [reload])
  useEffect(() => {
    if (!diagnostic || diagnostic.status !== "running") return
    const timer = setInterval(() => {
      aiApi
        .diagnostic(diagnostic.id)
        .then(async (result) => {
          setDiagnostic(result)
          if (result.status !== "running") await reload()
        })
        .catch((e) => setError(String(e)))
    }, 1500)
    return () => clearInterval(timer)
  }, [diagnostic, reload])
  const action = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError("")
    setMessage("")
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  const perform = async (input: DiagnosticInput) => {
    setDiagnostic(await aiApi.diagnose(input))
    setSection("diagnostics")
  }
  const chooseFlow = async (id: string) => {
    setVersions(id ? await aiApi.versions(id) : [])
    setFlow(
      id
        ? await aiApi.flow(id)
        : { id: "", name: "Nuevo flujo", version: 0, nodes: [], edges: [] }
    )
    setFaults({})
  }
  if (!can("ai.view"))
    return (
      <div className="p-6">
        Se requiere el permiso ai.view para consultar esta sección.
      </div>
    )
  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Configuración → IA
          </h1>
          <p className="text-sm text-muted-foreground">
            Cuentas, modelos y recorridos de fallback para cada uso de MIRA.
          </p>
        </div>
        <Badge variant="outline">
          {status?.mode === "managed"
            ? "Configuración activa en BD"
            : "Configuración anterior activa"}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-2">
        {[
          ["connections", "Proveedores y credenciales"],
          ["flows", "Flujos de fallback"],
          ["diagnostics", "Diagnósticos e historial"],
        ].map(([key, label]) => (
          <Button
            key={key}
            variant={section === key ? "default" : "outline"}
            onClick={() => setSection(key)}
          >
            {label}
          </Button>
        ))}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {section === "connections" && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Nueva conexión</CardTitle>
            </CardHeader>
            <CardContent>
              <form
                className="grid items-end gap-3 md:grid-cols-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  void action(async () => {
                    await aiApi.saveConnection({
                      name: connectionName,
                      provider,
                      base_url: endpoint,
                      region,
                      enabled: true,
                    })
                    setConnectionName("")
                    await reload()
                  })
                }}
              >
                <div className="space-y-1">
                  <Label>Nombre de la conexión</Label>
                  <Input
                    required
                    value={connectionName}
                    onChange={(e) => setConnectionName(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label>Proveedor</Label>
                  <select
                    className={selectClass}
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                  >
                    {[
                      "claude-cli",
                      "codex-cli",
                      "openai",
                      "responses",
                      "bedrock",
                    ].map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </select>
                </div>
                {["openai", "responses"].includes(provider) && (
                  <div className="space-y-1">
                    <Label>Endpoint</Label>
                    <Input
                      required
                      type="url"
                      value={endpoint}
                      onChange={(e) => setEndpoint(e.target.value)}
                    />
                  </div>
                )}
                {provider === "bedrock" && (
                  <div className="space-y-1">
                    <Label>Región AWS</Label>
                    <Input
                      value={region}
                      onChange={(e) => setRegion(e.target.value)}
                    />
                  </div>
                )}
                <Button disabled={!can("ai.credentials.manage") || busy}>
                  Crear conexión
                </Button>
              </form>
            </CardContent>
          </Card>
          {connections.map((c) => (
            <ConnectionCard
              key={c.id}
              connection={c}
              canManage={can("ai.credentials.manage")}
              canTest={can("ai.diagnostics.run")}
              reload={reload}
              perform={perform}
            />
          ))}
          {user?.is_admin && status?.mode !== "managed" && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Migración a configuración en BD
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Importa la configuración actual sin activarla. Conecta
                  sesiones exclusivas de MIRA, prueba cada modelo y asigna los
                  flujos antes de activar.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        await aiApi.migrate()
                        await reload()
                        setMessage(
                          "Configuración importada. Las credenciales candidatas requieren prueba y activación."
                        )
                      })
                    }
                  >
                    Importar configuración actual
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        await aiApi.activate()
                        await reload()
                        setMessage("Configuración de BD activada.")
                      })
                    }
                  >
                    Activar configuración de BD
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
      {section === "flows" && (
        <>
          <div className="flex gap-2">
            <select
              aria-label="Flujo"
              className={selectClass}
              value={flow?.id || ""}
              onChange={(e) => void action(() => chooseFlow(e.target.value))}
            >
              <option value="">Selecciona un flujo</option>
              {flows.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name} · v{f.version}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              disabled={!can("ai.flows.manage")}
              onClick={() => void chooseFlow("")}
            >
              Nuevo flujo
            </Button>
          </div>
          {flow && (
            <select
              aria-label="Versión del flujo"
              className={selectClass}
              value={flow.version}
              onChange={(e) => {
                if (flow.id)
                  void action(async () =>
                    setFlow(await aiApi.flow(flow.id, Number(e.target.value)))
                  )
              }}
              disabled={!flow.id}
            >
              {flow.id ? (
                versions.map((version) => (
                  <option key={version} value={version}>
                    Versión {version}
                    {version === flows.find((f) => f.id === flow.id)?.version
                      ? " · publicada actual"
                      : " · historial de solo lectura"}
                  </option>
                ))
              ) : (
                <option value={0}>Nueva versión</option>
              )}
            </select>
          )}
          {flow && (
            <FlowEditor
              key={`${flow.id}-${flow.version}`}
              flow={flow}
              connections={connections}
              editable={
                can("ai.flows.manage") &&
                (!flow.id ||
                  flow.version === flows.find((f) => f.id === flow.id)?.version)
              }
              save={async (draft) => {
                const result = await aiApi.saveFlow(draft)
                await reload()
                await chooseFlow(result.id)
                setMessage(
                  "Nueva versión publicada. Las ejecuciones en curso conservan su versión."
                )
              }}
            />
          )}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Asignaciones por escenario
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-4 md:grid-cols-2">
                {Object.entries(status?.scenarios || {}).map(
                  ([scenario, label]) => (
                    <div key={scenario} className="space-y-1">
                      <Label>{label}</Label>
                      <select
                        disabled={!can("ai.flows.manage")}
                        className={selectClass}
                        value={assignments[scenario] || ""}
                        onChange={(e) =>
                          setAssignments((previous) => ({
                            ...previous,
                            [scenario]: e.target.value,
                          }))
                        }
                      >
                        <option value="" disabled>
                          Seleccionar flujo
                        </option>
                        {flows.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  )
                )}
              </div>
              <Button
                disabled={!can("ai.flows.manage") || busy}
                onClick={() =>
                  void action(async () => {
                    await aiApi.assign(assignments)
                    setMessage("Asignaciones guardadas.")
                  })
                }
              >
                Guardar asignaciones
              </Button>
            </CardContent>
          </Card>
        </>
      )}
      {section === "diagnostics" && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Probar flujo publicado
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <select
                aria-label="Flujo a probar"
                className={selectClass}
                value={flow?.id || ""}
                onChange={(e) => void action(() => chooseFlow(e.target.value))}
              >
                <option value="">Selecciona un flujo</option>
                {flows.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name} · v{f.version}
                  </option>
                ))}
              </select>
              <select
                aria-label="Modo de prueba"
                className={selectClass}
                value={mode}
                onChange={(e) =>
                  setMode(e.target.value as "simulation" | "real")
                }
              >
                <option value="simulation">
                  Simulación · no altera la salud real
                </option>
                <option value="real">
                  Prueba real · consume uso del proveedor
                </option>
              </select>
              {mode === "simulation" &&
                flow?.nodes.map((n) => (
                  <div key={n.id} className="flex items-center gap-3">
                    <Label className="min-w-36">{n.name}</Label>
                    <select
                      className={selectClass}
                      value={faults[n.id] || "success"}
                      onChange={(e) =>
                        setFaults((previous) => ({
                          ...previous,
                          [n.id]: e.target.value,
                        }))
                      }
                    >
                      {[
                        ["success", "Respuesta válida"],
                        ["quota", "Cuota agotada"],
                        ["auth", "Autenticación inválida"],
                        ["timeout", "Timeout"],
                        ["invalid", "Respuesta inválida"],
                      ].map(([key, label]) => (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              <Button
                disabled={
                  !can("ai.diagnostics.run") ||
                  !flow?.id ||
                  diagnostic?.status === "running"
                }
                onClick={() =>
                  void action(() =>
                    perform({ mode, flow_id: flow!.id, action: "flow", faults })
                  )
                }
              >
                Probar flujo
              </Button>
              {diagnostic?.status === "running" && (
                <Button
                  variant="outline"
                  onClick={() =>
                    void action(() => aiApi.cancelDiagnostic(diagnostic.id))
                  }
                >
                  Cancelar cadena
                </Button>
              )}
              {diagnostic?.error && <p role="alert">{diagnostic.error}</p>}
            </CardContent>
          </Card>
          {diagnostic?.result && <RunTrace run={diagnostic.result} />}
          {diagnostic?.status === "running" && (
            <p role="status">Prueba en curso…</p>
          )}
          {query.get("job") && (
            <p className="text-sm">
              Recorridos del advisory job {query.get("job")} ·{" "}
              <Link className="underline" to="/advisory-jobs">
                Volver a jobs
              </Link>
            </p>
          )}
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Historial de ejecuciones</h2>
            <Button variant="ghost" onClick={() => void action(reload)}>
              Actualizar
            </Button>
          </div>
          {runs.map((r) => (
            <details key={r.id} open={query.get("run") === r.id}>
              <summary className="cursor-pointer rounded-md border p-3 text-sm">
                {date(r.created_at)} · {r.scenario} · {r.mode} ·{" "}
                {statusLabel[r.status] || r.status} · {r.steps.length} pasos
              </summary>
              <RunTrace run={r} />
            </details>
          ))}
          {runs.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Todavía no hay recorridos para esta selección.
            </p>
          )}
          <details>
            <summary className="cursor-pointer font-semibold">
              Historial de administración
            </summary>
            <div className="mt-3 max-h-96 overflow-auto text-xs">
              {audit.map((a) => (
                <p key={a.id} className="border-b py-2">
                  {date(a.created_at)} · usuario {a.actor} · {a.action} ·{" "}
                  {a.resource}
                </p>
              ))}
            </div>
          </details>
        </>
      )}
    </div>
  )
}

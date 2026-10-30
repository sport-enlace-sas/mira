import { useCallback, useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  FlaskConical,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Route,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Unplug,
} from "lucide-react"
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
  htmlFor,
}: {
  children: React.ReactNode
  className?: string
  htmlFor?: string
}) {
  return (
    <label htmlFor={htmlFor} className={`text-sm font-medium ${className}`}>
      {children}
    </label>
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
const phaseNames: Record<string, string> = {
  waiting_for_session: "Esperando disponibilidad de la cuenta",
  requesting: "Proveedor procesando la solicitud",
  saving: "Guardando el estado de la conexión",
}

const scenarioNames: Record<string, string> = {
  review: "Revisión de PR",
  security: "Seguridad",
  indexing: "Indexación",
  walkthrough: "Walkthrough",
  summary: "Resúmenes",
  self_critique: "Autocrítica",
  fix_verification: "Verificación de correcciones",
  conversation: "Conversaciones",
  thread_reply: "Respuestas a hilos",
  learning: "Aprendizaje",
  dependencies: "Dependencias",
  overlap: "Solapamientos",
  diagnostic: "Prueba de conexión",
}
const causeNames: Record<string, string> = {
  quota: "Cuota del modelo agotada",
  auth: "Acceso no válido",
  access: "Modelo no disponible para esta cuenta",
  timeout: "Tiempo de espera agotado",
  invalid: "Respuesta no válida",
  context_incompatible: "Contexto insuficiente",
  reasoning_incompatible: "Razonamiento no compatible",
  tools_incompatible: "Herramientas no compatibles",
  connection_disabled: "Conexión desactivada",
  cancelled: "Solicitud cancelada",
}

export function RunTrace({ run }: { run: Run }) {
  return (
    <Card className="overflow-hidden rounded-2xl">
      <CardHeader className="border-b bg-muted/20">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">
            {scenarioNames[run.scenario] || run.scenario}
          </CardTitle>
          <Badge
            variant="outline"
            className={run.status === "success" ? successTone : pendingTone}
          >
            {statusLabel[run.status] || run.status}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          {run.mode === "simulation"
            ? "Simulación · sin solicitudes a los proveedores"
            : "Prueba real · con consumo del proveedor"}{" "}
          · {date(run.created_at)}
        </p>
      </CardHeader>
      <CardContent className="space-y-4 pt-5">
        <ol className="space-y-3">
          {run.steps.map((step, index) => (
            <li key={`${step.node_id}-${index}`} className="flex gap-3">
              <span
                className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${step.result === "success" ? successTone : "bg-muted/50"}`}
              >
                {index + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2 rounded-xl border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold">{step.node_name}</p>
                  <Badge
                    variant="outline"
                    className={step.result === "success" ? successTone : ""}
                  >
                    {statusLabel[step.result] || step.result}
                  </Badge>
                </div>
                <p className="text-xs break-words text-muted-foreground">
                  {step.connection_name ||
                    providerNames[step.provider] ||
                    step.provider}{" "}
                  · {step.model} ·{" "}
                  {effortNames[step.reasoning_effort] || step.reasoning_effort}
                </p>
                {step.cause && (
                  <div className="rounded-lg bg-muted/40 p-2.5 text-xs">
                    <p className="font-medium">
                      {causeNames[step.cause] || step.cause}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      {step.action}
                      {step.reset_at
                        ? ` · reinicio informado: ${date(step.reset_at)}`
                        : step.retry_at
                          ? ` · próximo intento: ${date(step.retry_at)}`
                          : ""}
                    </p>
                  </div>
                )}
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                  <span>Intento {step.attempt}</span>
                  <span>
                    {step.result === "running"
                      ? phaseNames[step.phase || ""] || "Preparando solicitud"
                      : step.result === "skipped"
                        ? "Sin ejecutar"
                        : `${(step.duration_ms / 1000).toFixed(1)} s`}
                  </span>
                  <span>
                    Entrada:{" "}
                    {step.usage.prompt_tokens?.toLocaleString() ??
                      "no informada"}{" "}
                    · salida:{" "}
                    {step.usage.completion_tokens?.toLocaleString() ??
                      "no informada"}
                  </span>
                  {step.refresh_attempted && <span>Renovación intentada</span>}
                </div>
              </div>
            </li>
          ))}
        </ol>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            Identificadores de soporte
          </summary>
          <p className="mt-2 break-all">
            Ejecución {run.id} · versión del flujo {run.flow_version}
            {run.job_id ? ` · job ${run.job_id}` : ""}
          </p>
        </details>
      </CardContent>
    </Card>
  )
}

const providerNames: Record<string, string> = {
  "claude-cli": "Claude",
  "codex-cli": "Codex",
  openai: "API compatible con OpenAI",
  responses: "OpenAI Responses",
  bedrock: "Amazon Bedrock",
}
const effortNames: Record<string, string> = {
  off: "Automático",
  low: "Bajo",
  medium: "Medio",
  high: "Alto",
  xhigh: "Extra alto",
  max: "Máximo · max",
}
const successTone =
  "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
const pendingTone =
  "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300"
const preferredCredential = (connection: Connection) =>
  connection.active_credential ||
  [...connection.credentials].sort(
    (a, b) => (b.created_at || 0) - (a.created_at || 0)
  )[0]?.id ||
  ""

function ConnectionCard({
  connection,
  canManage,
  canTest,
  reload,
  perform,
  diagnostic,
  testing,
  onViewResult,
}: {
  connection: Connection
  canManage: boolean
  canTest: boolean
  reload: () => Promise<void>
  perform: (input: DiagnosticInput) => Promise<void>
  diagnostic: Diagnostic | null
  testing: boolean
  onViewResult: () => void
}) {
  const native = ["claude-cli", "codex-cli"].includes(connection.provider)
  const providerName = providerNames[connection.provider] || connection.provider
  const [kind, setKind] = useState(
    connection.provider === "bedrock" ? "aws" : native ? "session" : "api_key"
  )
  const [secret, setSecret] = useState("")
  const [credential, setCredential] = useState(() =>
    preferredCredential(connection)
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
  const [now, setNow] = useState(() => Date.now() / 1000)
  const active = connection.credentials.find(
    (c) => c.id === connection.active_credential
  )
  const selected = connection.credentials.find((c) => c.id === credential)
  const operational = connection.enabled && active?.status === "ready"
  const candidate = !!selected && selected.id !== connection.active_credential
  const loginPending = login?.status === "pending" && login.expires_at > now
  const disabled = busy || testing
  const title = !connection.enabled
    ? "Desactivada"
    : operational
      ? "Activa y comprobada"
      : active?.status === "auth_blocked"
        ? "Necesita reconexión"
        : connection.credentials.length
          ? "Pendiente de activar"
          : "Sin conectar"
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 30000)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    if (canManage)
      void aiApi
        .resumeLogin(connection.id)
        .then((value) => {
          setLogin(value)
          if (value?.status === "connected" && value.credential_id)
            setCredential(value.credential_id)
        })
        .catch(() => {})
  }, [connection.id, canManage])
  useEffect(() => {
    void aiApi
      .models(connection.id)
      .then((data) => setModels(data.models))
      .catch(() => {})
  }, [connection.id, connection.active_credential, diagnostic?.status])
  useEffect(() => {
    if (!login || login.status !== "pending") return
    const timer = setInterval(() => {
      void aiApi
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
  const probe = (rotation = false) =>
    perform({
      mode: "real",
      action: rotation ? "rotation" : "model",
      connection_id: connection.id,
      credential_id: credential || undefined,
      model,
      reasoning_effort: effort,
    })
  const orderedCredentials = [...connection.credentials].sort(
    (a, b) => (b.created_at || 0) - (a.created_at || 0)
  )
  const lastStep = diagnostic?.result?.steps.at(-1)
  return (
    <Card className="h-fit overflow-hidden rounded-2xl border-border/80">
      <CardHeader className="gap-4 border-b bg-muted/20 pb-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className={`flex size-11 items-center justify-center rounded-xl ${connection.provider === "claude-cli" ? "bg-orange-500/10 text-orange-600 dark:text-orange-300" : "bg-primary/10 text-primary"}`}
            >
              {connection.provider === "codex-cli" ? (
                <TerminalSquare className="size-5" />
              ) : (
                <Sparkles className="size-5" />
              )}
            </div>
            <div>
              <CardTitle className="text-lg">{providerName}</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                {connection.name}
              </p>
            </div>
          </div>
          <Badge
            className={operational ? successTone : pendingTone}
            variant="outline"
          >
            {operational ? (
              <CheckCircle2 className="mr-1 size-3.5" />
            ) : (
              <CircleAlert className="mr-1 size-3.5" />
            )}
            {title}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {operational
            ? "Esta cuenta ya se puede usar en tus flujos de IA."
            : connection.credentials.length
              ? "La sesión está guardada. Prueba y activa la credencial para empezar a usarla."
              : "Conecta una cuenta y comprueba que responde antes de usarla en un flujo."}
        </p>
        {active && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck className="size-3.5" />
              {active.renewable
                ? "Sesión con renovación automática"
                : "Reemplazo manual"}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Clock3 className="size-3.5" />
              Última prueba: {date(active.checked_at)}
            </span>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-5 pt-5">
        {loginPending && login && (
          <div
            className="space-y-3 rounded-xl border border-primary/20 bg-primary/5 p-4"
            role="status"
          >
            <p className="font-medium">Autoriza tu cuenta de {providerName}</p>
            <p className="text-sm text-muted-foreground">
              Abre el acceso oficial y completa la verificación. MIRA guardará
              su propia sesión al terminar.
            </p>
            {login.url ? (
              <Button asChild className="w-full">
                <a href={login.url} target="_blank" rel="noreferrer">
                  Abrir acceso oficial
                  <ExternalLink className="ml-2 size-4" />
                </a>
              </Button>
            ) : (
              <p className="flex items-center gap-2 text-sm">
                <Loader2 className="size-4 animate-spin" />
                Preparando el enlace…
              </p>
            )}
            {login.user_code && (
              <div className="rounded-lg border bg-background p-3">
                <p className="mb-1 text-xs text-muted-foreground">
                  Introduce este código en la página de Codex
                </p>
                <code className="text-xl font-semibold tracking-widest">
                  {login.user_code}
                </code>
              </div>
            )}
            {connection.provider === "claude-cli" && (
              <div className="space-y-2">
                <Label htmlFor={`login-code-${connection.id}`}>
                  Si Claude muestra un código, pégalo aquí
                </Label>
                <div className="flex gap-2">
                  <Input
                    id={`login-code-${connection.id}`}
                    type="password"
                    autoComplete="off"
                    placeholder="Código de autorización"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                  <Button
                    disabled={!code || busy}
                    onClick={() =>
                      void action(async () => {
                        await aiApi.loginCode(login.id, code)
                        setCode("")
                      })
                    }
                  >
                    Enviar
                  </Button>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                El acceso vence a las{" "}
                {new Date(login.expires_at * 1000).toLocaleTimeString()}
              </span>
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
            </div>
          </div>
        )}
        {login?.status === "connected" && candidate && (
          <div
            className={`flex gap-2 rounded-lg border p-3 text-sm ${successTone}`}
            role="status"
          >
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            <span>
              Cuenta autorizada. Elige un modelo y pulsa <b>Probar y activar</b>{" "}
              para terminar.
            </span>
          </div>
        )}
        {login &&
          !loginPending &&
          ["pending", "failed"].includes(login.status) && (
            <div
              className={`rounded-lg border p-3 text-sm ${pendingTone}`}
              role="status"
            >
              El acceso no se completó o ha caducado. Pulsa Conectar cuenta para
              intentarlo de nuevo.
            </div>
          )}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <FlaskConical className="size-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">Comprobar modelo</h3>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`credential-${connection.id}`}>
              Credencial para la prueba
            </Label>
            <select
              id={`credential-${connection.id}`}
              className={selectClass}
              value={credential}
              disabled={disabled}
              onChange={(e) => setCredential(e.target.value)}
            >
              <option value="">Selecciona una credencial</option>
              {orderedCredentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id === connection.active_credential
                    ? "Activa"
                    : "Guardada · sin activar"}{" "}
                  · {c.renewable ? "sesión renovable" : "credencial manual"} ·{" "}
                  {date(c.created_at)} · {c.id.slice(0, 6)}
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_145px]">
            <div className="min-w-0 space-y-1.5">
              <Label htmlFor={`model-${connection.id}`}>Modelo</Label>
              <Input
                id={`model-${connection.id}`}
                list={`models-${connection.id}`}
                disabled={disabled}
                value={model}
                placeholder="Selecciona o escribe un modelo"
                onChange={(e) => setModel(e.target.value)}
              />
              <datalist id={`models-${connection.id}`}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </datalist>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`effort-${connection.id}`}>Razonamiento</Label>
              <select
                id={`effort-${connection.id}`}
                className={selectClass}
                value={effort}
                disabled={disabled}
                onChange={(e) => setEffort(e.target.value)}
              >
                {efforts.map((e) => (
                  <option key={e} value={e}>
                    {effortNames[e]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={
                !canTest ||
                !credential ||
                !model ||
                disabled ||
                (candidate && !canManage)
              }
              onClick={() => void action(() => probe(candidate))}
            >
              {testing && diagnostic?.status === "running" ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : candidate ? (
                <ShieldCheck className="mr-2 size-4" />
              ) : (
                <FlaskConical className="mr-2 size-4" />
              )}
              {candidate ? "Probar y activar" : "Probar modelo"}
            </Button>
            {candidate && (
              <Button
                variant="outline"
                disabled={!canTest || !credential || !model || disabled}
                onClick={() => void action(() => probe())}
              >
                Solo probar
              </Button>
            )}
            {native && (
              <Button
                variant="outline"
                disabled={!canManage || disabled || loginPending}
                onClick={() =>
                  void action(async () =>
                    setLogin(await aiApi.login(connection.id))
                  )
                }
              >
                <KeyRound className="mr-2 size-4" />
                {active ? "Cambiar cuenta" : "Conectar cuenta"}
              </Button>
            )}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {candidate
              ? "Probar y activar hace una solicitud real y solo cambia la credencial si responde correctamente. Solo probar no la activa."
              : "La prueba hace una solicitud real y consume uso del proveedor. La cuenta y el modelo se comprueban por separado."}
          </p>
        </div>
        {diagnostic && (
          <div
            className={`space-y-2 rounded-xl border p-3 text-sm ${diagnostic.status === "success" ? successTone : diagnostic.status === "running" ? "bg-muted/30" : pendingTone}`}
            role="status"
          >
            <p className="flex items-center gap-2 font-medium">
              {diagnostic.status === "running" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : diagnostic.status === "success" ? (
                <CheckCircle2 className="size-4" />
              ) : (
                <CircleAlert className="size-4" />
              )}
              {diagnostic.status === "running"
                ? "Probando la conexión…"
                : diagnostic.status === "success"
                  ? "El modelo respondió correctamente"
                  : diagnostic.status === "cancelled"
                    ? "Prueba cancelada"
                    : "La prueba no se completó"}
            </p>
            {lastStep && (
              <p className="text-xs">
                {lastStep.model} · {lastStep.reasoning_effort} ·{" "}
                {(lastStep.duration_ms / 1000).toFixed(1)} s
                {lastStep.cause ? ` · ${lastStep.action}` : ""}
              </p>
            )}
            {diagnostic.error && <p>{diagnostic.error}</p>}
            <div className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={onViewResult}>
                Ver recorrido
                <ArrowRight className="ml-1 size-3.5" />
              </Button>
              {diagnostic.status === "running" && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void action(() => aiApi.cancelDiagnostic(diagnostic.id))
                  }
                >
                  Cancelar prueba
                </Button>
              )}
            </div>
          </div>
        )}
        <details className="group rounded-xl border">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-sm font-medium">
            <span>
              Modelos y comprobaciones{" "}
              <span className="ml-1 text-xs text-muted-foreground">
                {models.length}
              </span>
            </span>
            <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
          </summary>
          <div className="space-y-3 border-t p-3">
            <Button
              size="sm"
              variant="outline"
              disabled={!canTest || disabled}
              onClick={() =>
                void action(async () => {
                  await aiApi.refreshModels(connection.id)
                  const data = await aiApi.models(connection.id)
                  setModels(data.models)
                })
              }
            >
              <RefreshCw
                className={`mr-2 size-3.5 ${busy ? "animate-spin" : ""}`}
              />
              Actualizar catálogo
            </Button>
            <p className="text-xs text-muted-foreground">
              Catálogo de la credencial activa. Un modelo listado todavía puede
              necesitar una prueba. Los modelos que ya respondieron se conservan
              aunque el cliente no los liste.
            </p>
            <div className="max-h-72 space-y-2 overflow-auto">
              {models.map((m) => {
                const checks = Object.entries(m.access || {}).filter(
                  ([, a]) => a.status
                )
                return (
                  <div
                    key={m.id}
                    className="space-y-1 rounded-lg bg-muted/30 p-2.5"
                  >
                    <p className="text-xs font-medium break-all">{m.id}</p>
                    <p className="text-xs text-muted-foreground">
                      {m.status === "listed"
                        ? "Listado por el proveedor"
                        : "Sin listado del proveedor"}
                      {checks.length === 0 ? " · Sin probar" : ""}
                    </p>
                    {checks.map(([level, access]) => (
                      <p
                        key={level}
                        className={`text-xs ${access.status === "verified" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}
                      >
                        {effortNames[level] || level}:{" "}
                        {access.status === "quota"
                          ? "Última prueba con cuota agotada"
                          : statusLabel[access.status!] || access.status}{" "}
                        · {date(access.checked_at)}
                      </p>
                    ))}
                    {m.quota?.until && m.quota.until > now ? (
                      <p className="text-xs text-muted-foreground">
                        Próximo intento desde {date(m.quota.until)}
                      </p>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
        </details>
        <details className="group rounded-xl border">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-sm font-medium">
            <span>Credenciales y opciones avanzadas</span>
            <ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
          </summary>
          <div className="space-y-4 border-t p-3">
            <div className="space-y-2">
              {orderedCredentials.map((c) => (
                <div
                  key={c.id}
                  className="rounded-lg bg-muted/30 p-2.5 text-xs"
                >
                  <p className="font-medium">
                    {c.id === connection.active_credential
                      ? "Credencial activa"
                      : "Credencial guardada"}{" "}
                    · {statusLabel[c.status] || c.status}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {c.id.slice(0, 8)} · revisión {c.revision} · comprobación:{" "}
                    {date(c.checked_at)}
                  </p>
                  {c.expires_at && (
                    <p className="mt-1 text-muted-foreground">
                      Vigencia informada: {date(c.expires_at)}
                      {c.renewable ? " · renovable automáticamente" : ""}
                    </p>
                  )}
                </div>
              ))}
            </div>
            {canManage && (
              <details open={!native && connection.credentials.length === 0}>
                <summary className="cursor-pointer text-sm font-medium">
                  Importar API key o archivo de sesión
                </summary>
                <div className="mt-3 space-y-3">
                  <Label htmlFor={`kind-${connection.id}`}>
                    Tipo de credencial
                  </Label>
                  <select
                    id={`kind-${connection.id}`}
                    className={selectClass}
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value)
                      setSecret("")
                    }}
                  >
                    {connection.provider !== "bedrock" && (
                      <option value="api_key">API key</option>
                    )}
                    {native && (
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
                      aria-label={`Archivo de credenciales de ${providerName}`}
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
                      aria-label={`Clave de ${providerName}`}
                      autoComplete="new-password"
                      placeholder="El secreto no volverá a mostrarse"
                      value={secret}
                      onChange={(e) => setSecret(e.target.value)}
                    />
                  )}
                  <Button
                    disabled={!secret || disabled}
                    onClick={() =>
                      void action(async () => {
                        let value: unknown = secret
                        if (["session", "aws"].includes(kind)) {
                          try {
                            value = JSON.parse(secret)
                          } catch {
                            throw new Error(
                              "El archivo no contiene JSON válido. Revisa su formato y vuelve a importarlo."
                            )
                          }
                        }
                        const result = await aiApi.importCredential(
                          connection.id,
                          kind,
                          value
                        )
                        setSecret("")
                        setCredential(result.id)
                        await reload()
                      })
                    }
                  >
                    Guardar sin activar
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    La credencial actual seguirá activa hasta que la nueva
                    supere una prueba real.
                  </p>
                </div>
              </details>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={!canManage || disabled}
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
              <Unplug className="mr-2 size-3.5" />
              {connection.enabled
                ? "Desactivar conexión"
                : "Habilitar conexión"}
            </Button>
          </div>
        </details>
        {error && (
          <p
            role="alert"
            className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive"
          >
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
                ["context_window", "Capacidad de contexto (tokens)"],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="space-y-1">
                <Label>{label}</Label>
                <Input
                  aria-label={label}
                  type="number"
                  min={key === "retries" ? 0 : 1}
                  value={node[key]}
                  onChange={(e) => update({ [key]: Number(e.target.value) })}
                />
                {key === "context_window" && (
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <p>
                      Incluye instrucciones, contenido y reserva de salida. Una
                      solicitud que no cabe pasa al siguiente nodo.
                    </p>
                    {models.find((m) => m.id === node.model)
                      ?.context_window && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          update({
                            context_window: models.find(
                              (m) => m.id === node.model
                            )!.context_window!,
                          })
                        }
                      >
                        Usar capacidad anunciada:{" "}
                        {models
                          .find((m) => m.id === node.model)!
                          .context_window!.toLocaleString()}
                      </Button>
                    )}
                  </div>
                )}
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
  const [diagnosticInput, setDiagnosticInput] =
    useState<DiagnosticInput | null>(null)
  const [showConnectionForm, setShowConnectionForm] = useState(false)
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
    const started = await aiApi.diagnose(input)
    setDiagnosticInput(input)
    setDiagnostic(started)
    if (input.action === "flow") setSection("diagnostics")
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
  const activeConnections = connections.filter(
    (c) =>
      c.enabled &&
      c.credentials.some(
        (credential) =>
          credential.id === c.active_credential && credential.status === "ready"
      )
  )
  const assignedCount = Object.keys(assignments).length
  const scenarioCount = Object.keys(status?.scenarios || {}).length
  if (!can("ai.view"))
    return (
      <div className="p-6">
        Se requiere el permiso ai.view para consultar esta sección.
      </div>
    )
  if (!status)
    return (
      <div className="mx-auto max-w-7xl space-y-4 p-6">
        <h1 className="text-2xl font-semibold">Conexiones y fallbacks</h1>
        {error ? (
          <>
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
            <Button variant="outline" onClick={() => void action(reload)}>
              Volver a intentar
            </Button>
          </>
        ) : (
          <p
            role="status"
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <Loader2 className="size-4 animate-spin" />
            Cargando tus conexiones y flujos…
          </p>
        )}
      </div>
    )
  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="mt-1 flex size-11 shrink-0 items-center justify-center rounded-xl border bg-muted/40">
            <Sparkles className="size-5 text-primary" />
          </div>
          <div>
            <p className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
              Configuración / Inteligencia artificial
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">
              Conexiones y fallbacks
            </h1>
            <p className="mt-1 max-w-xl text-sm leading-relaxed text-muted-foreground">
              Conecta tus cuentas, elige qué modelo usar y define quién responde
              si el primero falla.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={status?.mode === "managed" ? successTone : pendingTone}
          >
            <Database className="mr-1.5 size-3.5" />
            {status?.mode === "managed"
              ? "Configuración de BD activa"
              : "Configuración por activar"}
          </Badge>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => void action(reload)}
          >
            <RefreshCw
              className={`mr-1.5 size-3.5 ${busy ? "animate-spin" : ""}`}
            />
            Actualizar
          </Button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          {
            icon: KeyRound,
            value: `${activeConnections.length} / ${connections.length}`,
            label: "Cuentas activas",
            note: "Con credencial comprobada",
          },
          {
            icon: Route,
            value: String(flows.length),
            label: "Flujos publicados",
            note: "Secuencias de recuperación",
          },
          {
            icon: ShieldCheck,
            value: `${assignedCount} / ${scenarioCount || "—"}`,
            label: "Escenarios asignados",
            note: "Cada uso tiene su propio flujo",
          },
        ].map(({ icon: Icon, value, label, note }) => (
          <div
            key={label}
            className="flex items-center gap-3 rounded-xl border bg-card p-4"
          >
            <div className="rounded-lg bg-muted p-2.5 text-muted-foreground">
              <Icon className="size-4" />
            </div>
            <div>
              <div className="flex items-baseline gap-2">
                <span className="text-xl font-semibold tabular-nums">
                  {value}
                </span>
                <span className="text-xs font-medium">{label}</span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>
            </div>
          </div>
        ))}
      </div>
      {status?.mode === "legacy" && (
        <div className="rounded-2xl border bg-muted/20 p-4 sm:p-5">
          <p className="font-medium">Prepara MIRA en tres pasos</p>
          <p className="mt-1 text-sm text-muted-foreground">
            La configuración anterior sigue en uso hasta completar la
            activación.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {[
              {
                label: "Conecta tus cuentas",
                done: connections.some((c) => c.credentials.length > 0),
                text: "Autoriza el acceso oficial o importa una clave.",
              },
              {
                label: "Prueba y activa",
                done: activeConnections.length > 0,
                text: "Comprueba un modelo y activa su credencial.",
              },
              {
                label: "Activa tus flujos",
                done: false,
                text: "Asigna los escenarios y termina la configuración.",
              },
            ].map((step, i) => (
              <div key={step.label} className="flex gap-2.5">
                <span
                  className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${step.done ? successTone : "border bg-background"}`}
                >
                  {step.done ? <Check className="size-3.5" /> : i + 1}
                </span>
                <div>
                  <p className="text-sm font-medium">{step.label}</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {step.text}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <nav
        aria-label="Secciones de configuración IA"
        className="grid gap-2 rounded-2xl border bg-muted/20 p-2 sm:grid-cols-3"
      >
        {[
          {
            key: "connections",
            label: "Proveedores y credenciales",
            description: "Cuentas, acceso y modelos",
            icon: KeyRound,
          },
          {
            key: "flows",
            label: "Flujos de fallback",
            description: "Orden de modelos por escenario",
            icon: Route,
          },
          {
            key: "diagnostics",
            label: "Diagnósticos e historial",
            description: "Pruebas, resultados y consumo",
            icon: FlaskConical,
          },
        ].map(({ key, label, description, icon: Icon }) => (
          <button
            key={key}
            type="button"
            aria-current={section === key ? "page" : undefined}
            className={`flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${section === key ? "border-border bg-background shadow-sm" : "border-transparent text-muted-foreground hover:bg-background/60 hover:text-foreground"}`}
            onClick={() => setSection(key)}
          >
            <Icon
              className={`size-4 shrink-0 ${section === key ? "text-primary" : ""}`}
            />
            <span>
              <span className="block text-sm font-medium">{label}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {description}
              </span>
            </span>
          </button>
        ))}
      </nav>
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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">Tus proveedores</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Una cuenta puede servir a varios modelos y flujos.
              </p>
            </div>
            <Button
              variant="outline"
              disabled={!can("ai.credentials.manage")}
              onClick={() => setShowConnectionForm((value) => !value)}
            >
              <Plus className="mr-2 size-4" />
              {showConnectionForm ? "Cerrar formulario" : "Añadir conexión"}
            </Button>
          </div>
          {(showConnectionForm || connections.length === 0) && (
            <Card className="rounded-2xl border-dashed">
              <CardHeader>
                <CardTitle className="text-base">Añadir una conexión</CardTitle>
                <p className="text-sm text-muted-foreground">
                  Asigna un nombre para identificar esta cuenta en tus flujos.
                </p>
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
                      setShowConnectionForm(false)
                      await reload()
                      setMessage(
                        "Conexión creada. Conecta la cuenta o importa su credencial para continuar."
                      )
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
                    <Label htmlFor="new-connection-provider">Proveedor</Label>
                    <select
                      id="new-connection-provider"
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
                        <option key={p} value={p}>
                          {providerNames[p] || p}
                        </option>
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
          )}
          <div className="grid items-start gap-5 lg:grid-cols-2">
            {connections.map((c) => (
              <ConnectionCard
                key={c.id}
                connection={c}
                canManage={can("ai.credentials.manage")}
                canTest={can("ai.diagnostics.run")}
                reload={reload}
                perform={perform}
                diagnostic={
                  diagnosticInput?.connection_id === c.id ? diagnostic : null
                }
                testing={diagnostic?.status === "running"}
                onViewResult={() => setSection("diagnostics")}
              />
            ))}
          </div>
          {user?.is_admin && status?.mode !== "managed" && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Último paso: activar los flujos de MIRA
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Cuando las cuentas estén activas y los modelos comprobados,
                  activa los flujos asignados a cada escenario. Desde ese
                  momento MIRA usará la configuración guardada en la base de
                  datos.
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
                    Activar configuración de MIRA
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

import { useCallback, useEffect, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router"
import {
  Check,
  ChevronDown,
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
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import {
  aiApi,
  type Connection,
  type Credential,
  type Diagnostic,
  type DiagnosticInput,
  type Flow,
  type Login,
  type Model,
  type Run,
} from "@/lib/api/ai"
import { useAuth } from "@/lib/auth"
import { useDocumentTitle } from "@/lib/hooks"

import { connectionState, effortNames, efforts, friendlyError, providerNames, selectClass, statusLabel } from "./ai-display"
import { FlowEditor } from "./ai-flow-editor"

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

const successTone =
  "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
const pendingTone =
  "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300"

const credentialKind: Record<string, string> = {
  session: "Sesión",
  api_key: "Clave API",
  oauth_token: "Token OAuth",
  aws: "Credencial AWS",
}

type ConnectionControls = {
  connection: Connection
  canManage: boolean
  canTest: boolean
  reload: () => Promise<void>
  perform: (input: DiagnosticInput) => Promise<void>
  testing: boolean
  now: number
}

function SessionCard({
  connection,
  session,
  canManage,
  canTest,
  reload,
  perform,
  testing,
  now,
  diagnostic,
}: ConnectionControls & { session: Credential; diagnostic?: Diagnostic }) {
  const [expanded, setExpanded] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [models, setModels] = useState<Model[]>([])
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
  const [prompt, setPrompt] = useState("")
  const active = session.id === connection.active_credential
  const expired = !!session.expires_at && session.expires_at <= now
  const providerName = providerNames[connection.provider] || connection.provider
  const label = credentialKind[session.kind] || "Sesión"
  const reference = session.id.slice(0, 8)
  const running = diagnostic?.status === "running"
  const disabled = busy || testing
  const canProbe = canTest && connection.enabled && !!model.trim() && !disabled

  useEffect(() => {
    if (!expanded) return
    let stale = false
    void aiApi
      .models(connection.id, session.id)
      .then((catalog) => {
        if (!stale) setModels(catalog.models)
      })
      .catch(() => {})
    return () => {
      stale = true
    }
  }, [connection.id, session.id, expanded, diagnostic?.status])

  const action = async (fn: () => Promise<void>) => {
    setError("")
    setBusy(true)
    try {
      await fn()
    } catch (failure) {
      setError(friendlyError(failure))
    } finally {
      setBusy(false)
    }
  }
  const probe = (action: "model" | "rotation", message?: string) =>
    perform({
      action,
      mode: "real",
      connection_id: connection.id,
      credential_id: session.id,
      model: model.trim(),
      reasoning_effort: effort,
      prompt: message,
    })

  return (
    <article className="relative min-w-0 overflow-hidden rounded-xl border border-border bg-card shadow-xs">
      <details
        className="group/session"
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary className="flex min-h-20 cursor-pointer list-none items-center gap-3 p-3 pr-16 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring sm:p-4 sm:pr-17 [&::-webkit-details-marker]:hidden">
          <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open/session:rotate-180 motion-reduce:transition-none" />
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">
                {label} de {providerName}
              </span>
              <span className="rounded border bg-muted/40 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                {reference}
              </span>
              {active && (
                <Badge
                  variant="outline"
                  className={connection.enabled ? successTone : ""}
                >
                  {connection.enabled ? "En uso" : "Seleccionada"}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {session.renewable
                ? "Renovación automática"
                : "Renovación manual"}
              {" · "}
              {session.checked_at
                ? `Probada el ${date(session.checked_at)}`
                : "Pendiente de prueba"}
            </p>
          </div>
          <Badge
            variant="outline"
            className={
              session.status === "ready" && (!expired || session.renewable)
                ? successTone
                : pendingTone
            }
          >
            {expired && !session.renewable
              ? "Vencida"
              : statusLabel[session.status] || session.status}
          </Badge>
        </summary>
        <div className="space-y-4 border-t bg-background/40 p-4 sm:p-5">
          <dl className="grid grid-cols-2 gap-3 rounded-lg border bg-muted/20 p-3 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Guardada</dt>
              <dd className="mt-1">{date(session.created_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Vigencia informada</dt>
              <dd className="mt-1">
                {session.expires_at ? date(session.expires_at) : "Sin fecha"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Revisión</dt>
              <dd className="mt-1 tabular-nums">{session.revision}</dd>
            </div>
          </dl>
          <div className="space-y-3 rounded-lg border bg-background p-3 sm:p-4">
            <h4 className="flex items-center gap-2 text-sm font-semibold">
              <FlaskConical className="size-4 text-muted-foreground" />
              Probar esta sesión
            </h4>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
              <div className="space-y-1.5">
                <Label htmlFor={`model-${session.id}`}>Modelo</Label>
                <Input
                  id={`model-${session.id}`}
                  list={`models-${session.id}`}
                  value={model}
                  placeholder="Selecciona o escribe un modelo"
                  disabled={disabled}
                  onChange={(event) => setModel(event.target.value)}
                />
                <datalist id={`models-${session.id}`}>
                  {models.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.name}
                    </option>
                  ))}
                </datalist>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`effort-${session.id}`}>Razonamiento</Label>
                <select
                  id={`effort-${session.id}`}
                  className={selectClass}
                  value={effort}
                  disabled={disabled}
                  onChange={(event) => setEffort(event.target.value)}
                >
                  {efforts.map((level) => (
                    <option key={level} value={level}>
                      {effortNames[level]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!canProbe}
                onClick={() => void action(() => probe("model"))}
              >
                {running ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <FlaskConical className="size-4" />
                )}
                Probar modelo
              </Button>
              {!active && canManage && (
                <Button
                  disabled={!canProbe}
                  onClick={() => void action(() => probe("rotation"))}
                >
                  <ShieldCheck className="size-4" /> Probar y activar
                </Button>
              )}
              {!connection.enabled && (
                <span className="self-center text-xs text-muted-foreground">
                  Habilita la conexión para probar.
                </span>
              )}
            </div>
            <details className="group/prompt rounded-lg border">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-sm font-medium focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
                Mensaje de prueba
                <ChevronDown className="size-4 transition-transform group-open/prompt:rotate-180 motion-reduce:transition-none" />
              </summary>
              <div className="space-y-2 border-t p-3">
                <Label htmlFor={`prompt-${session.id}`}>
                  Mensaje para el modelo
                </Label>
                <textarea
                  id={`prompt-${session.id}`}
                  value={prompt}
                  maxLength={1000}
                  disabled={disabled}
                  onChange={(event) => setPrompt(event.target.value)}
                  className="min-h-20 w-full rounded-md border border-input bg-background p-2.5 text-sm focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
                  placeholder="Escribe un mensaje corto"
                />
                <Button
                  variant="outline"
                  disabled={!canProbe || !prompt.trim()}
                  onClick={() =>
                    void action(() => probe("model", prompt.trim()))
                  }
                >
                  Enviar mensaje
                </Button>
              </div>
            </details>
            <p className="text-xs text-muted-foreground">
              Las pruebas hacen solicitudes reales y consumen cuota.
            </p>
          </div>
          {diagnostic && (
            <div
              className="space-y-2 rounded-lg border bg-background p-3"
              aria-live="polite"
            >
              {running ? (
                <div className="flex flex-wrap items-center gap-3">
                  <p className="flex items-center gap-2 text-sm">
                    <Loader2 className="size-4 animate-spin" /> Prueba en curso
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void action(() => aiApi.cancelDiagnostic(diagnostic.id))
                    }
                  >
                    Cancelar prueba
                  </Button>
                </div>
              ) : (
                <>
                  <p className="text-sm font-medium">
                    {statusLabel[diagnostic.status] || diagnostic.status}
                  </p>
                  {diagnostic.error && (
                    <p className="text-sm text-destructive">
                      {diagnostic.error}
                    </p>
                  )}
                  {diagnostic.result?.output && (
                    <pre className="max-h-56 overflow-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs wrap-anywhere whitespace-pre-wrap">
                      {diagnostic.result.output}
                    </pre>
                  )}
                  {diagnostic.result && (
                    <details className="rounded-lg border">
                      <summary className="cursor-pointer p-3 text-sm font-medium">
                        Ver recorrido
                      </summary>
                      <div className="border-t p-2 sm:p-3">
                        <RunTrace run={diagnostic.result} />
                      </div>
                    </details>
                  )}
                </>
              )}
            </div>
          )}
          <details className="group/catalog rounded-lg border bg-background">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-sm font-medium focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
              <span>
                Modelos y comprobaciones{" "}
                <span className="text-xs text-muted-foreground">
                  ({models.length})
                </span>
              </span>
              <ChevronDown className="size-4 text-muted-foreground transition-transform group-open/catalog:rotate-180 motion-reduce:transition-none" />
            </summary>
            <div className="space-y-3 border-t p-3">
              <Button
                size="sm"
                variant="outline"
                disabled={!canTest || disabled || !connection.enabled}
                onClick={() =>
                  void action(async () => {
                    const catalog = await aiApi.refreshModels(
                      connection.id,
                      session.id
                    )
                    setModels(catalog.models)
                  })
                }
              >
                <RefreshCw className="size-3.5" /> Actualizar catálogo
              </Button>
              {models.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  Puedes escribir el identificador del modelo para probarlo.
                </p>
              )}
              <div className="max-h-72 divide-y overflow-auto rounded-lg border empty:hidden">
                {models.map((entry) => (
                  <div key={entry.id} className="space-y-1 p-3 text-xs">
                    <p className="font-mono break-all">{entry.id}</p>
                    {Object.entries(entry.access || {}).map(
                      ([level, access]) => (
                        <p key={level} className="text-muted-foreground">
                          {effortNames[level] || level} ·{" "}
                          {statusLabel[access.status || ""] || access.status} ·{" "}
                          {date(access.checked_at)}
                        </p>
                      )
                    )}
                    {entry.quota?.until && entry.quota.until > now && (
                      <p className="text-muted-foreground">
                        Próximo intento desde {date(entry.quota.until)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </details>
        </div>
      </details>
      {canManage && (
        <Button
          type="button"
          size="icon"
          variant="outline"
          aria-label={`Eliminar ${label.toLowerCase()} de ${providerName} ${reference}`}
          title="Eliminar sesión"
          disabled={busy || running}
          className="absolute top-4 right-3 size-10 text-destructive hover:text-destructive"
          onClick={() => {
            setError("")
            setConfirm(true)
          }}
        >
          <Trash2 className="size-4" />
        </Button>
      )}
      {error && (
        <p
          role="alert"
          className="border-t bg-destructive/5 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={(open) => {
          if (!busy) setConfirm(open)
        }}
        destructive
        loading={busy}
        title="¿Eliminar esta sesión?"
        description={`La sesión ${reference} de ${providerName} se eliminará de MIRA. Los informes anteriores seguirán disponibles.${active ? " Si la conexión está en un flujo publicado, activa otra sesión primero." : ""}`}
        confirmLabel="Eliminar sesión"
        cancelLabel="Cancelar"
        onConfirm={() =>
          void action(async () => {
            setConfirm(false)
            await aiApi.deleteCredential(connection.id, session.id)
            await reload()
          })
        }
      />
    </article>
  )
}

function ConnectionCard({
  connection,
  canManage,
  canTest,
  reload,
  perform,
  testing,
  now,
  diagnostics,
}: ConnectionControls & { diagnostics: Record<string, Diagnostic> }) {
  const native = ["claude-cli", "codex-cli"].includes(connection.provider)
  const providerName = providerNames[connection.provider] || connection.provider
  const state = connectionState(connection, now)
  const [kind, setKind] = useState(
    connection.provider === "bedrock" ? "aws" : native ? "session" : "api_key"
  )
  const [secret, setSecret] = useState("")
  const [login, setLogin] = useState<Login | null>(null)
  const [code, setCode] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(connection.credentials.length === 0)
  const [confirm, setConfirm] = useState(false)
  const loginPending = login?.status === "pending" && login.expires_at > now
  const disabled = busy || testing
  const sessions = [...connection.credentials].sort(
    (a, b) =>
      Number(b.id === connection.active_credential) -
        Number(a.id === connection.active_credential) ||
      (b.created_at || 0) - (a.created_at || 0)
  )

  useEffect(() => {
    if (canManage && native) {
      void aiApi
        .resumeLogin(connection.id)
        .then((value) => {
          setLogin(value)
          if (value?.status === "pending") setAdding(true)
        })
        .catch(() => {})
    }
  }, [connection.id, canManage, native])
  useEffect(() => {
    if (!login || login.status !== "pending") return
    const timer = setInterval(() => {
      void aiApi
        .loginStatus(login.id)
        .then(async (value) => {
          setLogin(value)
          if (value.status === "connected") {
            setAdding(false)
            await reload()
          }
        })
        .catch((failure) => setError(friendlyError(failure)))
    }, 1500)
    return () => clearInterval(timer)
  }, [login, reload])

  const action = async (fn: () => Promise<unknown>) => {
    setError("")
    setBusy(true)
    try {
      await fn()
    } catch (failure) {
      setError(friendlyError(failure))
    } finally {
      setBusy(false)
    }
  }
  const save = async () => {
    let value: unknown = secret.trim()
    if (["session", "aws"].includes(kind)) {
      try {
        value = JSON.parse(secret)
      } catch {
        throw new Error("El archivo debe contener JSON válido.")
      }
    }
    await aiApi.importCredential(connection.id, kind, value)
    setSecret("")
    setAdding(false)
    await reload()
  }

  return (
    <section className="min-w-0 space-y-3 overflow-hidden rounded-xl border bg-card p-3 shadow-xs sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className={`flex size-10 shrink-0 items-center justify-center rounded-lg border ${connection.provider === "claude-cli" ? "border-orange-500/20 bg-orange-500/10 text-orange-700 dark:text-orange-300" : "bg-muted text-foreground"}`}
          >
            {connection.provider === "codex-cli" ? (
              <TerminalSquare className="size-4" />
            ) : (
              <KeyRound className="size-4" />
            )}
          </div>
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <h3 className="font-semibold wrap-anywhere">{connection.name}</h3>
              <Badge variant="outline" className={state === "Lista para usar" ? successTone : connection.enabled ? pendingTone : "text-muted-foreground"}>
                {state}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              {providerName} · {sessions.length}{" "}
              {sessions.length === 1 ? "sesión" : "sesiones"}
            </p>
          </div>
        </div>
        {canManage && (
          <div className="flex flex-wrap gap-2">
            <Button
              className="h-8"
              variant="outline"
              aria-expanded={adding}
              onClick={() => setAdding((value) => !value)}
            >
              <Plus aria-hidden="true" className="size-4" /> {adding ? "Cerrar formulario" : "Añadir sesión"}
            </Button>
            <Button
              className="h-8"
              variant="outline"
              disabled={disabled}
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
              {connection.enabled ? "Desactivar" : "Habilitar"}
            </Button>
            {sessions.length === 0 && (
              <Button
                size="icon"
                variant="outline"
                aria-label={`Eliminar conexión ${connection.name}`}
                title="Eliminar conexión"
                className="size-8 text-destructive hover:text-destructive"
                disabled={busy || loginPending}
                onClick={() => setConfirm(true)}
              >
                <Trash2 className="size-4" />
              </Button>
            )}
          </div>
        )}
      </div>
      {sessions.map((session) => (
        <SessionCard
          key={session.id}
          connection={connection}
          session={session}
          canManage={canManage}
          canTest={canTest}
          reload={reload}
          perform={perform}
          testing={testing}
          now={now}
          diagnostic={diagnostics[session.id]}
        />
      ))}
      {sessions.length === 0 && (!adding || !canManage) && (
        <p className="px-1 pb-2 text-sm text-muted-foreground">
          Esta conexión todavía no tiene sesiones.
        </p>
      )}
      {canManage && adding && (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          {native && !loginPending && (
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() =>
                void action(async () =>
                  setLogin(await aiApi.login(connection.id))
                )
              }
            >
              <KeyRound className="size-4" /> Conectar cuenta de {providerName}
            </Button>
          )}
          {loginPending && login && (
            <div
              className="space-y-3 rounded-lg border bg-muted/30 p-3"
              role="status"
            >
              <p className="text-sm font-medium">
                Autoriza tu cuenta de {providerName}
              </p>
              {login.url ? (
                <Button asChild variant="outline" size="sm">
                  <a href={login.url} target="_blank" rel="noreferrer">
                    Abrir acceso oficial <ExternalLink className="size-3.5" />
                  </a>
                </Button>
              ) : (
                <p className="flex items-center gap-2 text-sm">
                  <Loader2 className="size-4 animate-spin" /> Preparando el
                  enlace…
                </p>
              )}
              {login.user_code && (
                <div className="rounded-lg border bg-background p-3">
                  <p className="text-xs text-muted-foreground">
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
                    Código de autorización de Claude
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    <Input
                      id={`login-code-${connection.id}`}
                      type="password"
                      autoComplete="off"
                      value={code}
                      className="min-w-44 flex-1"
                      onChange={(event) => setCode(event.target.value)}
                    />
                    <Button
                      variant="outline"
                      disabled={!code || busy}
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
                </div>
              )}
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>
                  Vence a las{" "}
                  {new Date(login.expires_at * 1000).toLocaleTimeString()}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await aiApi.cancelLogin(login.id)
                      setLogin(null)
                      setCode("")
                    })
                  }
                >
                  Cancelar conexión
                </Button>
              </div>
            </div>
          )}
          {login &&
            !loginPending &&
            ["pending", "failed"].includes(login.status) && (
              <p role="status" className="text-sm text-destructive">
                La autorización venció o falló. Puedes volver a conectar la
                cuenta.
              </p>
            )}
          <details
            open={native ? undefined : true}
            className="rounded-lg border"
          >
            <summary className="cursor-pointer p-3 text-sm font-medium">
              Importar clave o archivo de sesión
            </summary>
            <div className="space-y-3 border-t p-3">
              <Label htmlFor={`kind-${connection.id}`}>
                Tipo de credencial
              </Label>
              <select
                id={`kind-${connection.id}`}
                className={selectClass}
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value)
                  setSecret("")
                }}
              >
                {connection.provider !== "bedrock" && (
                  <option value="api_key">Clave API</option>
                )}
                {native && (
                  <option value="session">Archivo de sesión JSON</option>
                )}
                {connection.provider === "claude-cli" && (
                  <option value="oauth_token">Token OAuth manual</option>
                )}
                {connection.provider === "bedrock" && (
                  <option value="aws">Credencial AWS JSON</option>
                )}
              </select>
              {["session", "aws"].includes(kind) ? (
                <Input
                  type="file"
                  aria-label={`Archivo de credenciales de ${providerName}`}
                  accept="application/json,.json"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    setSecret("")
                    if (file)
                      void file
                        .text()
                        .then(setSecret)
                        .catch(() => setError("No se pudo leer el archivo."))
                  }}
                />
              ) : (
                <Input
                  type="password"
                  aria-label={`Clave de ${providerName}`}
                  autoComplete="new-password"
                  value={secret}
                  onChange={(event) => setSecret(event.target.value)}
                />
              )}
              <Button
                variant="outline"
                disabled={!secret.trim() || disabled}
                onClick={() => void action(save)}
              >
                Guardar sesión
              </Button>
            </div>
          </details>
        </div>
      )}
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={(open) => {
          if (!busy) setConfirm(open)
        }}
        destructive
        loading={busy}
        title="¿Eliminar esta conexión?"
        description={`La conexión «${connection.name}» se eliminará de MIRA. Los informes anteriores seguirán disponibles.`}
        confirmLabel="Eliminar conexión"
        cancelLabel="Cancelar"
        onConfirm={() =>
          void action(async () => {
            setConfirm(false)
            await aiApi.deleteConnection(connection.id)
            await reload()
          })
        }
      />
    </section>
  )
}

export function AIPage() {
  useDocumentTitle("Configuración de IA")
  const { user } = useAuth()
  const [query] = useSearchParams()
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 30000)
    return () => clearInterval(timer)
  }, [])
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
  const [draftKey, setDraftKey] = useState(0)
  const [flowDirty, setFlowDirty] = useState(false)
  const [leaveDraft, setLeaveDraft] = useState<(() => void) | null>(null)
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
  const [sessionDiagnostics, setSessionDiagnostics] = useState<
    Record<string, Diagnostic>
  >({})
  const [startingDiagnostic, setStartingDiagnostic] = useState(false)
  const startingRef = useRef(false)
  const [showConnectionForm, setShowConnectionForm] = useState(false)
  const [firstConnection, setFirstConnection] = useState("")
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
    let stale = false
    const timer = setInterval(() => {
      aiApi
        .diagnostic(diagnostic.id)
        .then(async (result) => {
          if (stale) return
          setDiagnostic(result)
          const credential = diagnosticInput?.credential_id
          if (credential)
            setSessionDiagnostics((previous) => ({
              ...previous,
              [credential]: result,
            }))
          if (result.status !== "running") await reload()
        })
        .catch((e) => setError(String(e)))
    }, 1500)
    return () => {
      stale = true
      clearInterval(timer)
    }
  }, [diagnostic, diagnosticInput, reload])
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
    if (startingRef.current || diagnostic?.status === "running") {
      throw new Error("Ya hay una prueba en curso.")
    }
    startingRef.current = true
    setStartingDiagnostic(true)
    try {
      const started = await aiApi.diagnose(input)
      setDiagnosticInput(input)
      setDiagnostic(started)
      if (input.credential_id) {
        const credential = input.credential_id
        setSessionDiagnostics((previous) => ({
          ...previous,
          [credential]: started,
        }))
      }
      if (input.action === "flow") setSection("diagnostics")
      if (started.status !== "running") await reload()
    } finally {
      setStartingDiagnostic(false)
      startingRef.current = false
    }
  }
  const chooseFlow = async (id: string) => {
    const [selected, history] = id
      ? await Promise.all([aiApi.flow(id), aiApi.versions(id)])
      : [null, []]
    setFlow(selected)
    setVersions(history)
    setFlowDirty(false)
    setFaults({})
  }
  const guardDraft = (navigate: () => void) => {
    if (section === "flows" && flowDirty) setLeaveDraft(() => navigate)
    else navigate()
  }
  const selectSection = (next: string) => {
    if (section === next) return
    guardDraft(() => {
      setSection(next)
      setFlowDirty(false)
      if (next === "flows" && !flow && flows.length) {
        void action(() => chooseFlow(assignments.review || flows[0].id))
      }
    })
  }
  const createFlow = () => guardDraft(() => {
    setFlow({ id: "", name: "", version: 0, nodes: [], edges: [] })
    setDraftKey((current) => current + 1)
    setVersions([])
    setFlowDirty(false)
    setFaults({})
  })
  const publishFlow = async (draft: Flow) => {
    const result = await aiApi.saveFlow(draft)
    const published = { ...draft, id: result.id, version: draft.version + 1 }
    setFlows((previous) => [...previous.filter((entry) => entry.id !== published.id), {
      id: published.id, name: published.name, version: published.version,
    }].sort((left, right) => left.name.localeCompare(right.name)))
    setFlow(published)
    setVersions((previous) => [published.version, ...previous])
    setFlowDirty(false)
    setMessage(draft.id ? "Nueva versión publicada." : "Flujo publicado. Puedes asignarlo a un escenario.")
  }
  const activeConnections = connections.filter(
    (connection) => connectionState(connection, now) === "Lista para usar"
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
        <h1 className="text-2xl font-semibold">Conexiones y flujos de IA</h1>
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
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-5 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-sky-600/25 bg-sky-600/10 text-sky-800 dark:text-sky-300">
            <Sparkles aria-hidden="true" className="size-5" />
          </span>
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Conexiones y flujos de IA</h1>
            <p className="mt-1 text-sm text-muted-foreground">Administra tus cuentas y el orden de los modelos que usa MIRA.</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={status.mode === "managed" ? successTone : pendingTone}>
            <Database aria-hidden="true" className="size-3.5" />
            {status.mode === "managed" ? "Configuración activa" : "Configuración por activar"}
          </Badge>
          <Button type="button" variant="outline" className="h-8 px-2.5" disabled={busy} onClick={() => void action(reload)}>
            <RefreshCw aria-hidden="true" className={busy ? "motion-safe:animate-spin" : undefined} />Actualizar
          </Button>
        </div>
      </header>
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
      <nav aria-label="Secciones de configuración de IA" className="flex flex-wrap gap-1.5 rounded-xl border bg-muted/20 p-1.5">
        {[
          { key: "connections", label: "Conexiones", icon: KeyRound },
          { key: "flows", label: "Flujos y alternativas", icon: Route },
          { key: "diagnostics", label: "Diagnóstico", icon: FlaskConical },
        ].map(({ key, label, icon: Icon }) => (
          <Button key={key} type="button" variant="ghost" aria-current={section === key ? "page" : undefined}
            disabled={busy} onClick={() => selectSection(key)}
            className="h-9 px-3 text-sm aria-[current=page]:bg-sky-600/10 aria-[current=page]:text-sky-900 dark:aria-[current=page]:text-sky-200">
            <Icon aria-hidden="true" className="size-4" />{label}
          </Button>
        ))}
      </nav>
      <ConfirmDialog open={leaveDraft !== null} onOpenChange={(open) => { if (!open) setLeaveDraft(null) }}
        title="¿Salir del editor de flujo?" description="Los cambios de este borrador aún no se han publicado."
        confirmLabel="Descartar cambios" cancelLabel="Seguir editando"
        onConfirm={() => {
          const navigate = leaveDraft
          setLeaveDraft(null)
          navigate?.()
        }} />
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
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold">Conexiones guardadas</h2>
                <Badge variant="outline">{activeConnections.length} de {connections.length} listas</Badge>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">Abre una sesión para ver sus modelos, comprobar el acceso o renovar la credencial.</p>
            </div>
            <Button
              className="h-9 px-3"
              aria-expanded={showConnectionForm}
              disabled={!can("ai.credentials.manage")}
              onClick={() => setShowConnectionForm((value) => !value)}
            >
              <Plus className="mr-2 size-4" />
              {showConnectionForm ? "Cerrar formulario" : "Añadir conexión"}
            </Button>
          </div>
          {(showConnectionForm ||
            (connections.length === 0 && can("ai.credentials.manage"))) && (
            <Card className="rounded-2xl border">
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
                      const created = await aiApi.saveConnection({
                        name: connectionName.trim(),
                        provider,
                        base_url: endpoint,
                        region,
                        enabled: true,
                      })
                      setConnectionName("")
                      setShowConnectionForm(false)
                      setFirstConnection(created.id)
                      await reload()
                      setMessage(
                        "Conexión creada. Conecta la cuenta o importa su credencial para continuar."
                      )
                    })
                  }}
                >
                  <div className="space-y-1">
                    <Label htmlFor="new-connection-name">
                      Nombre de la conexión
                    </Label>
                    <Input
                      id="new-connection-name"
                      required
                      maxLength={120}
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
                      <Label htmlFor="new-connection-endpoint">Endpoint</Label>
                      <Input
                        id="new-connection-endpoint"
                        required
                        type="url"
                        value={endpoint}
                        onChange={(e) => setEndpoint(e.target.value)}
                      />
                    </div>
                  )}
                  {provider === "bedrock" && (
                    <div className="space-y-1">
                      <Label htmlFor="new-connection-region">Región AWS</Label>
                      <Input
                        id="new-connection-region"
                        value={region}
                        onChange={(e) => setRegion(e.target.value)}
                      />
                    </div>
                  )}
                  <Button
                    type="submit"
                    disabled={
                      !can("ai.credentials.manage") ||
                      busy ||
                      !connectionName.trim()
                    }
                  >
                    Crear conexión
                  </Button>
                </form>
              </CardContent>
            </Card>
          )}
          <div className="grid min-w-0 items-start gap-4">
            {[...connections]
              .sort(
                (a, b) =>
                  Number(b.id === firstConnection) -
                  Number(a.id === firstConnection)
              )
              .map((c) => (
                <ConnectionCard
                  key={c.id}
                  connection={c}
                  canManage={can("ai.credentials.manage")}
                  canTest={can("ai.diagnostics.run")}
                  reload={reload}
                  perform={perform}
                  diagnostics={sessionDiagnostics}
                  now={now}
                  testing={
                    startingDiagnostic || diagnostic?.status === "running"
                  }
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
        <section aria-labelledby="flow-settings-heading" className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="flow-settings-heading" className="text-lg font-semibold">Flujos de IA</h2>
              <p className="mt-1 text-sm text-muted-foreground">Crea una secuencia de modelos y asigna dónde debe usarla MIRA.</p>
            </div>
            <Button type="button" className="h-9 px-3" disabled={!can("ai.flows.manage") || busy} onClick={createFlow}>
              <Plus aria-hidden="true" />Nuevo flujo
            </Button>
          </div>
          {(flows.length > 0 || !!flow) && (
            <div className="grid gap-3 rounded-xl border bg-muted/15 p-3 sm:grid-cols-[1fr_14rem] sm:p-4">
              <div className="min-w-0 space-y-1">
                <Label htmlFor="ai-flow-selection">Flujo guardado</Label>
                <select id="ai-flow-selection" className={selectClass} value={flow?.id || ""} disabled={busy}
                  onChange={(event) => {
                    const id = event.target.value
                    guardDraft(() => void action(() => chooseFlow(id)))
                  }}>
                  <option value="">{flow && !flow.id ? "Borrador nuevo" : "Selecciona un flujo"}</option>
                  {flows.map((entry) => <option key={entry.id} value={entry.id}>{entry.name} · v{entry.version}</option>)}
                </select>
              </div>
              {!!flow?.id && (
                <div className="min-w-0 space-y-1">
                  <Label htmlFor="ai-flow-version">Versión</Label>
                  <select id="ai-flow-version" className={selectClass} value={flow.version} disabled={busy}
                    onChange={(event) => {
                      const version = Number(event.target.value)
                      const flowId = flow.id
                      guardDraft(() => void action(async () => {
                        setFlow(await aiApi.flow(flowId, version))
                        setFlowDirty(false)
                      }))
                    }}>
                    {versions.map((version) => (
                      <option key={version} value={version}>
                        v{version} {version === flows.find((entry) => entry.id === flow.id)?.version ? "· Actual" : "· Histórica"}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}
          {flow ? (
            <FlowEditor
              key={flow.id ? flow.id + "-" + flow.version : "draft-" + draftKey}
              flow={flow}
              connections={connections}
              now={now}
              editable={can("ai.flows.manage") && (!flow.id || flow.version === flows.find((entry) => entry.id === flow.id)?.version)}
              onDirtyChange={setFlowDirty}
              save={publishFlow}
            />
          ) : (
            <p className="rounded-xl border border-dashed px-4 py-6 text-sm text-muted-foreground">
              {busy ? "Cargando flujo…" : flows.length ? "Selecciona un flujo para ver o modificar su orden." : "Todavía no hay flujos publicados. Empieza eligiendo la conexión y el modelo principal."}
            </p>
          )}
          <details className="group/assign rounded-xl border bg-card">
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 rounded-xl px-4 py-3 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
              <ChevronDown aria-hidden="true" className="size-4 transition-transform group-open/assign:rotate-180 motion-reduce:transition-none" />
              <span className="font-semibold">Asignaciones por escenario</span>
              <Badge variant="outline">{assignedCount} de {scenarioCount} asignados</Badge>
            </summary>
            <div className="space-y-3 border-t p-4">
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.entries(status.scenarios).map(([scenario, label]) => (
                  <div key={scenario} className="min-w-0 space-y-1">
                    <Label htmlFor={"ai-scenario-" + scenario}>{scenarioNames[scenario] ?? label}</Label>
                    <select id={"ai-scenario-" + scenario} disabled={!can("ai.flows.manage") || busy}
                      className={selectClass} value={assignments[scenario] || ""}
                      onChange={(event) => setAssignments((previous) => ({ ...previous, [scenario]: event.target.value }))}>
                      <option value="" disabled>Seleccionar flujo</option>
                      {flows.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
                    </select>
                  </div>
                ))}
              </div>
              <Button disabled={!can("ai.flows.manage") || busy || !flows.length}
                onClick={() => void action(async () => {
                  await aiApi.assign(assignments)
                  setMessage("Asignaciones guardadas.")
                })}>Guardar asignaciones</Button>
            </div>
          </details>
        </section>
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

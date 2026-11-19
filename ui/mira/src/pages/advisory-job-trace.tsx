import { Activity, ChevronDown, Timer } from "lucide-react"
import { useRef } from "react"

import { api, type ReviewAuditStage, type ReviewAuditTrace, type ReviewJobModel } from "@/lib/api"
import { useAsync } from "@/lib/hooks"
import { cn } from "@/lib/utils"

import { formatDateTime, formatDuration, relativeTime, TOKEN_FORMATTER } from "./advisory-format"

const STAGE_LABEL: Record<string, string> = {
  ingestion: "Ingestión y contexto",
  indexing: "Indexación",
  review: "Revisión del PR",
  security: "Seguridad",
  dependencies: "Dependencias",
  walkthrough: "Walkthrough",
  summary: "Resumen",
  self_critique: "Autocrítica",
  fix_verification: "Verificación de correcciones",
  overlap: "Solapamiento con otros PR",
}

const STAGE_STATUS: Record<ReviewAuditStage["status"], string> = {
  pending: "Pendiente",
  running: "En curso",
  completed: "Completada",
  failed: "Falló",
  cancelled: "Cancelada",
  skipped: "Sin ejecutar",
}

const PHASE_LABEL: Record<string, string> = {
  preparing: "Preparando solicitud",
  waiting_for_session: "Esperando conexión",
  requesting: "Esperando respuesta del modelo",
}

const ISSUE_LABEL: Record<string, string> = {
  access: "Modelo sin acceso",
  auth: "La conexión necesita autenticación",
  capacity_busy: "Conexión ocupada por otra revisión",
  connection_disabled: "Conexión deshabilitada",
  context_incompatible: "Límite de contexto excedido",
  invalid: "Respuesta del modelo inválida",
  provider: "Error del proveedor",
  quota: "Cuota no disponible",
  reasoning_incompatible: "Nivel de razonamiento incompatible",
  session_timeout: "Tiempo agotado esperando conexión",
  timeout: "Tiempo de respuesta del modelo agotado",
  tools_incompatible: "Herramientas incompatibles",
  vault: "Almacén de credenciales no disponible",
}

const STAGE_TONE = {
  running: "border-sky-600/25 bg-sky-700/10 text-sky-900 dark:text-sky-200",
  completed: "border-emerald-600/25 bg-emerald-700/10 text-emerald-900 dark:text-emerald-200",
  failed: "border-destructive/25 bg-destructive/10 text-destructive",
  warning: "border-amber-600/30 bg-amber-600/10 text-amber-900 dark:text-amber-200",
  quiet: "border-border bg-muted/45 text-muted-foreground",
}

const STAGE_MARKER = {
  running: "border-sky-600 bg-sky-600 dark:border-sky-400 dark:bg-sky-400",
  completed: "border-emerald-600 bg-emerald-600 dark:border-emerald-400 dark:bg-emerald-400",
  failed: "border-destructive bg-destructive",
  warning: "border-amber-600 bg-amber-600 dark:border-amber-400 dark:bg-amber-400",
  quiet: "border-muted-foreground/65 bg-card",
}

const CLOCK = new Intl.DateTimeFormat("es", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
})

const STAGE_CHIP = "inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium leading-4"

type TraceActivity = "running" | "cancelling" | "closed"

function ModelMeasurements({ model, activity }: { model: ReviewAuditStage["models"][number]; activity: TraceActivity }) {
  const active = model.active_attempts ?? 0
  const measured = model.calls > active
  const wait = model.session_wait_ms
  const inference = model.inference_ms
  const tokenPrefix = model.calls_with_usage < model.calls ? "≥ " : ""
  const activeLabel = { running: "en curso", cancelling: "en cancelación", closed: "sin cierre" }[activity]
  const metrics = [
    {
      label: "Espera de conexión",
      value: wait != null && (wait > 0 || measured) ? formatDuration(wait) : null,
    },
    {
      label: "Tiempo de inferencia",
      value: inference != null && (inference > 0 || measured) ? formatDuration(inference) : null,
    },
    {
      label: "Tokens de entrada",
      value: model.usage_reported ? tokenPrefix + TOKEN_FORMATTER.format(model.input_tokens) : null,
    },
    {
      label: "Tokens de salida",
      value: model.usage_reported ? tokenPrefix + TOKEN_FORMATTER.format(model.output_tokens) : null,
    },
  ]

  return (
    <li className="min-w-0 space-y-2 py-2.5">
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 break-words text-xs">
          <code className="break-all font-semibold text-foreground" translate="no">{model.model}</code>
          <span className="ml-1.5 break-all text-muted-foreground" translate="no">{model.provider}</span>
        </p>
        <p className="min-w-0 text-xs text-muted-foreground tabular-nums">
          {model.calls} {model.calls === 1 ? "intento" : "intentos"}
          {active > 0 && ` · ${active} ${activeLabel}`}
          {measured && ` · ${formatDuration(model.duration_ms)} acumulados`}
        </p>
      </div>
      <dl className="grid min-w-0 grid-cols-2 gap-x-3 gap-y-1.5 text-xs xl:grid-cols-4">
        {metrics.map(({ label, value }) => (
          <div className="min-w-0" key={label}>
            <dt className="leading-snug text-muted-foreground">{label}</dt>
            <dd className={cn("mt-0.5 wrap-anywhere tabular-nums", value ? "font-medium" : "text-muted-foreground")}>
              {value ?? "Sin registro"}
            </dd>
          </div>
        ))}
      </dl>
    </li>
  )
}

function AuditStage({ stage, activity, now }: { stage: ReviewAuditStage; activity: TraceActivity; now: number }) {
  const title = STAGE_LABEL[stage.id] ?? stage.label
  const startedAt = stage.started_at
  const running = stage.status === "running"
  const active = running && activity === "running"
  const cancelling = running && activity === "cancelling"
  const missingClosure = running && activity === "closed"
  const phases = stage.phases.map((phase) => PHASE_LABEL[phase] ?? phase)
  const waiting = active && stage.phases.includes("waiting_for_session") && !stage.phases.includes("requesting")
  const tone = waiting || cancelling || missingClosure
    ? "warning"
    : active ? "running" : stage.status === "completed" || stage.status === "failed" ? stage.status : "quiet"
  const duration = active && startedAt != null
    ? Math.max(0, now - startedAt * 1000)
    : stage.duration_ms
  const issues = stage.issues ?? []
  const issueCount = issues.reduce((sum, issue) => sum + issue.count, 0)

  return (
    <li className="relative min-w-0 pl-5 sm:pl-6">
      <span className={cn("absolute left-0 top-3.5 size-2.5 rounded-full border-2 ring-4 ring-card", STAGE_MARKER[tone])} aria-hidden="true" />
      <div className="min-w-0 rounded-lg border border-border bg-card px-2.5 py-2.5 sm:px-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
          <div className="min-w-0">
            <h4 className="wrap-anywhere text-sm font-semibold leading-snug">{title}</h4>
            {startedAt != null && (
              <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
                Inicio{" "}
                <time dateTime={new Date(startedAt * 1000).toISOString()} title={formatDateTime(startedAt)}>
                  {CLOCK.format(startedAt * 1000)}
                </time>
              </p>
            )}
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className={cn(STAGE_CHIP, STAGE_TONE[tone])}>
              {missingClosure ? "Sin cierre registrado" : cancelling ? "Cancelando" : STAGE_STATUS[stage.status]}
            </span>
            {duration != null && (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
                <Timer className="size-3.5 shrink-0" aria-hidden="true" />
                <span>{active ? "Transcurrido" : "Duración registrada"} · {formatDuration(duration)}</span>
              </span>
            )}
          </div>
        </div>
        {phases.length > 0 && running && (
          <p className="mt-2 wrap-anywhere text-xs text-muted-foreground">
            {!active && "Última fase registrada · "}
            {phases.join(" · ")}
          </p>
        )}
        {stage.models.length > 0 && (
          <ul className="mt-2 divide-y border-t">
            {stage.models.map((model) => (
              <ModelMeasurements key={`${model.provider}/${model.model}`} model={model} activity={running ? activity : "closed"} />
            ))}
          </ul>
        )}
        {issueCount > 0 && (
          <details className="group/issue mt-1 border-t pt-1 text-xs">
            <summary className="flex min-h-9 cursor-pointer list-none flex-wrap items-center gap-2 rounded-sm text-muted-foreground marker:content-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring motion-reduce:transition-none [&::-webkit-details-marker]:hidden">
              <ChevronDown className="size-3.5 shrink-0 transition-transform group-open/issue:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
              {issueCount} {issueCount === 1 ? "incidencia" : "incidencias"} en los intentos
            </summary>
            <ul className="space-y-1.5 pb-1 pl-2 text-muted-foreground">
              {issues.map((issue) => (
                <li key={`${issue.provider}/${issue.model}/${issue.cause}/${issue.result}`} className="flex flex-wrap gap-x-1.5">
                  <code className="break-all text-foreground" translate="no">{issue.model}</code>
                  <span className="wrap-anywhere">{ISSUE_LABEL[issue.cause] ?? issue.label}</span>
                  <span className="tabular-nums">×{issue.count}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </li>
  )
}

export function AdvisoryJobTrace({ job, refresh, now }: { job: ReviewJobModel; refresh: number; now: number }) {
  const request = useRef<{ jobId: number; pending: Promise<ReviewAuditTrace | null> } | null>(null)
  const { data, error, loading } = useAsync(async () => {
    const jobId = job.id
    const current = request.current
    if (current?.jobId === jobId) return { jobId, trace: await current.pending }
    const pending = api.reviewJobTrace(jobId)
    request.current = { jobId, pending }
    try {
      return { jobId, trace: await pending }
    } finally {
      if (request.current?.pending === pending) request.current = null
    }
  }, [job.id, refresh])
  const trace = data?.jobId === job.id ? data.trace : null

  if (!trace) {
    const message = error
      ? "No se pudo cargar el recorrido. Usa Actualizar para volver a consultar."
      : loading
        ? "Cargando recorrido…"
        : job.status === "pending" || job.status === "running"
          ? "El recorrido estará disponible cuando este intento registre sus primeras etapas."
          : "Esta revisión no tiene un recorrido registrado."
    return (
      <section aria-label="Recorrido de auditoría" className="rounded-lg border border-dashed px-3 py-4">
        <p className="wrap-anywhere text-sm text-muted-foreground" role={loading ? "status" : undefined}>
          {message}
        </p>
      </section>
    )
  }

  const previousAttempt = trace.attempt !== job.attempts || (job.status === "running" && trace.updated_at < job.updated_at)
  const openTrace = trace.finished_at == null && trace.status === "running"
  const activity = openTrace && !previousAttempt && (job.status === "running" || job.status === "cancelling")
    ? job.status
    : "closed"
  const missingClosure = openTrace && activity === "closed"
  const started = trace.stages
    .filter((stage) => stage.started_at != null)
    .sort((left, right) => (left.started_at ?? 0) - (right.started_at ?? 0))
  const withoutStart = trace.stages.filter((stage) => stage.started_at == null)
  const activeCount = activity === "running" ? started.filter((stage) => stage.status === "running").length : 0
  const hasPartialUsage = trace.stages.some((stage) => stage.models.some((model) => model.usage_reported && model.calls_with_usage < model.calls))
  const hasTokens = trace.stages.some((stage) => stage.models.some((model) => model.usage_reported))

  return (
    <section aria-label="Recorrido de auditoría" className="min-w-0 space-y-3">
      <header className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Activity className="size-4 shrink-0 text-sky-800 dark:text-sky-300" aria-hidden="true" />
            Recorrido de auditoría
          </h3>
          <p className="mt-1 wrap-anywhere text-xs text-muted-foreground">
            Intento {trace.attempt}{trace.worker != null && ` · Worker ${trace.worker}`}
            {previousAttempt && " · Registro de un intento anterior"}
          </p>
        </div>
        <p className="max-w-full wrap-anywhere text-xs text-muted-foreground tabular-nums" title={formatDateTime(trace.updated_at)}>
          Último registro {relativeTime(trace.updated_at, now).toLocaleLowerCase("es")}
        </p>
      </header>
      {missingClosure && (
        <p className="rounded-md border border-amber-600/30 bg-amber-600/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
          El último registro quedó sin cierre. Las mediciones corresponden a ese momento.
        </p>
      )}
      {error && <p className="text-xs text-muted-foreground">No se pudo actualizar. Se muestran los últimos datos disponibles.</p>}
      {started.length > 0 && (
        <div className="min-w-0 space-y-2">
          <p className="text-xs text-muted-foreground">
            Orden de inicio. Las etapas pueden coincidir.
            {activeCount > 0 && ` ${activeCount} ${activeCount === 1 ? "etapa en curso" : "etapas en curso"}.`}
          </p>
          <ul className="relative min-w-0 space-y-2 before:absolute before:bottom-5 before:left-[4px] before:top-5 before:w-px before:bg-border">
            {started.map((stage) => <AuditStage key={stage.id} stage={stage} activity={activity} now={now} />)}
          </ul>
        </div>
      )}
      {withoutStart.length > 0 && (
        <section aria-label="Etapas sin inicio registrado" className="rounded-md border bg-muted/25 px-2.5 py-2">
          <h4 className="text-xs font-medium">Etapas sin inicio registrado</h4>
          <ul className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
            {withoutStart.map((stage) => (
              <li key={stage.id} className={cn(STAGE_CHIP, STAGE_TONE.quiet, "max-w-full wrap-anywhere rounded-md")}>
                {STAGE_LABEL[stage.id] ?? stage.label} · {STAGE_STATUS[stage.status]}
              </li>
            ))}
          </ul>
        </section>
      )}
      {trace.stages.length === 0 && <p className="text-xs text-muted-foreground">Este intento no tiene etapas registradas.</p>}
      {hasTokens && (
        <p className="text-xs text-muted-foreground">
          Los CLI pueden reportar tokens estimados.{hasPartialUsage && " ≥ indica un mínimo registrado."}
        </p>
      )}
    </section>
  )
}

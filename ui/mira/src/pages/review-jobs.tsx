import { ChevronDown, ExternalLink, LoaderCircle, Play, RefreshCw, Square } from "lucide-react"
import { Link } from "react-router"
import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { api, type ReviewAuditStage, type ReviewJobModel } from "@/lib/api"
import { useAsync, useDocumentTitle } from "@/lib/hooks"
import { useAuth } from "@/lib/auth"
import { toast } from "sonner"

const STATUS_VARIANT: Record<
  ReviewJobModel["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  running: "default",
  completed: "outline",
  failed: "destructive",
  superseded: "secondary",
  cancelling: "secondary",
  cancelled: "outline",
}

const STATUS_LABEL: Record<ReviewJobModel["status"], string> = {
  pending: "En cola",
  running: "En curso",
  completed: "Completada",
  failed: "Falló",
  superseded: "SHA sustituido",
  cancelling: "Cancelando",
  cancelled: "Cancelada",
}

const CANCELLATION_REASON: Record<string, string> = {
  cancelled_by_user: "Cancelada manualmente",
  pr_merged: "PR fusionado",
  pr_closed: "PR cerrado",
  pr_draft: "PR movido a borrador",
  pr_reopen_requested: "Esperando que se libere la conexión",
}

const TOKEN_FORMATTER = new Intl.NumberFormat("es")

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

function relativeTime(epoch: number, now: number) {
  const seconds = Math.max(0, Math.floor(now / 1000 - epoch))
  if (seconds < 60) return "Ahora"
  if (seconds < 3600) return `Hace ${Math.floor(seconds / 60)} min`
  if (seconds < 86400) return `Hace ${Math.floor(seconds / 3600)} h`
  return `Hace ${Math.floor(seconds / 86400)} d`
}

function formatDuration(durationMs: number) {
  if (durationMs < 1_000) return `${Math.round(durationMs)} ms`
  if (durationMs < 60_000)
    return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)} s`
  const totalSeconds = Math.round(durationMs / 1_000)
  const seconds = totalSeconds % 60
  const totalMinutes = Math.floor(totalSeconds / 60)
  if (totalMinutes < 60) return `${totalMinutes}m ${seconds}s`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours}h ${minutes}m`
}

type JobAction = "cancel" | "resume" | "retry"

function JobTrace({ job, refresh, now }: { job: ReviewJobModel; refresh: number; now: number }) {
  const { data, error, loading } = useAsync(() => api.reviewJobTrace(job.id), [job.id, refresh])
  if (error) return <p className="text-sm text-destructive">No se pudo cargar el recorrido.</p>
  if (!data) {
    return (
      <p className="text-sm text-muted-foreground">
        {loading ? "Cargando recorrido…" : "Este intento todavía no tiene un recorrido registrado."}
      </p>
    )
  }
  const active = data.stages.filter((stage) => stage.status === "running")
  const incomplete = data.finished_at != null && data.stages.some((stage) => stage.status === "failed")
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            Recorrido de auditoría
            {incomplete && <Badge variant="destructive">Con incidencias</Badge>}
          </p>
          <p className="text-xs text-muted-foreground">
            Intento {data.attempt}{data.worker ? ` · Worker ${data.worker}` : ""}
            {active.length > 0 && ` · En curso · ${active.map((stage) => STAGE_LABEL[stage.id] ?? stage.label).join(", ")}`}
          </p>
        </div>
        <div className="text-xs text-muted-foreground">
          {active.length > 0 && <p>Último avance · {relativeTime(data.updated_at, now)}</p>}
          <p>Los CLI pueden reportar tokens estimados.</p>
        </div>
      </div>
      <ol className="grid gap-2 lg:grid-cols-2">
        {data.stages.map((stage) => {
          const elapsed = stage.status === "running" && stage.started_at != null
            ? Math.max(0, now - stage.started_at * 1000)
            : stage.duration_ms
          const phases = stage.phases.map((phase) => PHASE_LABEL[phase]).filter(Boolean)
          return (
            <li key={stage.id} className="min-w-0">
              <div className={`h-full rounded-md border px-3 py-2 ${stage.status === "running" ? "border-primary/60 bg-primary/5" : "bg-card"}`}>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="min-w-0 flex-1 font-medium">{STAGE_LABEL[stage.id] ?? stage.label}</span>
                  <Badge variant={stage.status === "failed" ? "destructive" : stage.status === "running" ? "default" : "outline"}>
                    {STAGE_STATUS[stage.status]}
                  </Badge>
                  <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                    {elapsed == null ? "—" : formatDuration(elapsed)}
                  </span>
                </div>
                {phases.length > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">{phases.join(" · ")}</p>
                )}
                {stage.models.length > 0 && (
                  <ul className="mt-2 divide-y border-t">
                    {stage.models.map((model) => {
                      const active = model.active_attempts ?? 0
                      const hasFinished = model.calls > active
                      const wait = model.session_wait_ms
                      const inference = model.inference_ms
                      const showWait = wait != null && (wait > 0 || hasFinished)
                      const showInference = inference != null && (inference > 0 || hasFinished)
                      return (
                        <li key={`${model.provider}/${model.model}`} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-1.5 text-xs">
                          <span className="min-w-0 break-all font-mono">{model.model} <span className="text-muted-foreground">· {model.provider}</span></span>
                          <span className="tabular-nums text-muted-foreground">
                            {model.calls} {model.calls === 1 ? "intento" : "intentos"}
                            {active > 0 && ` · ${active} en curso`}
                            {hasFinished && ` · ${formatDuration(model.duration_ms)}`}
                            {model.usage_reported
                              ? ` · ${model.calls_with_usage < model.calls ? "≥ " : ""}${TOKEN_FORMATTER.format(model.input_tokens)} entrada / ${TOKEN_FORMATTER.format(model.output_tokens)} salida`
                              : hasFinished && " · tokens sin reporte"}
                          </span>
                          {(showWait || showInference) && (
                            <span className="flex w-full flex-wrap gap-x-3 tabular-nums text-muted-foreground">
                              {showWait && <span>Espera de conexión · {formatDuration(wait)}</span>}
                              {showInference && <span>Tiempo del modelo · {formatDuration(inference)}</span>}
                            </span>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
                {!!stage.issues?.length && (
                  <details className="mt-2 border-t pt-2 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">Intentos omitidos o fallidos</summary>
                    <ul className="mt-1 space-y-1 pl-3">
                      {stage.issues.map((issue) => (
                        <li key={`${issue.provider}/${issue.model}/${issue.cause}/${issue.result}`}>
                          <span className="break-all font-mono">{issue.model}</span> · {ISSUE_LABEL[issue.cause] ?? issue.label} · {issue.count}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function ReviewJobsPage() {
  useDocumentTitle("Advisory jobs")
  const { user } = useAuth()
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [acting, setActing] = useState<number | null>(null)
  const [expandedJob, setExpandedJob] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const { data: jobs, loading, error } = useAsync(() => api.listReviewJobs(), [refreshNonce])

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now())
      setRefreshNonce((n) => n + 1)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const act = async (jobId: number, action: JobAction) => {
    setActing(jobId)
    try {
      if (action === "cancel") await api.cancelReviewJob(jobId)
      else if (action === "resume") await api.resumeReviewJob(jobId)
      else await api.retryReviewJob(jobId)
      toast.success(
        action === "cancel"
          ? "Cancelación solicitada"
          : "Revisión puesta en cola"
      )
    } catch (error) {
      const message = error instanceof Error ? error.message : ""
      const detail = message.match(/"detail"\s*:\s*"([^"\n]+)"/)?.[1]
      toast.error(
        detail ||
          "No se pudo cambiar la revisión. Actualiza e inténtalo de nuevo"
      )
    } finally {
      setRefreshNonce((n) => n + 1)
      setActing(null)
    }
  }

  return (
    <div className="min-w-0 space-y-4 px-3 pb-6 pt-2 sm:px-5 [contain:inline-size]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Advisory jobs</h1>
        <Button variant="outline" size="sm" onClick={() => setRefreshNonce((n) => n + 1)}>
          <RefreshCw className={loading ? "mr-2 size-4 animate-spin" : "mr-2 size-4"} />
          Actualizar
        </Button>
      </div>

      {error && <p className="p-3 text-sm text-destructive">No se pudo actualizar la cola.</p>}
      {!jobs?.length && (
        <Card className="p-4 text-sm text-muted-foreground">
          {loading ? "Cargando cola…" : "Aún no hay revisiones en Advisory Jobs."}
        </Card>
      )}

      <div className="space-y-2">
        {jobs?.map((job) => {
          const delayed = job.status === "pending" && job.next_attempt_at > now / 1000
          return (
            <Collapsible key={job.id} open={expandedJob === job.id} onOpenChange={(open) => setExpandedJob(open ? job.id : null)}>
              <Card className="gap-0 overflow-hidden py-0">
                <div className="flex flex-wrap items-start gap-3 p-3 sm:items-center sm:p-4">
                  <div className="min-w-40 flex-1">
                    <a href={job.pr_url} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 font-medium hover:underline">
                      <span className="truncate">{job.owner}/{job.repo} #{job.pr_number}</span>
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                    <p className="truncate text-xs text-muted-foreground" title={job.pr_title}>{job.pr_title}</p>
                    <p className="mt-1 font-mono text-xs text-muted-foreground">{job.head_sha.slice(0, 12)}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                    <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                    <span className="tabular-nums text-muted-foreground">
                      {job.audit_duration_ms > 0 ? formatDuration(job.audit_duration_ms) : relativeTime(job.updated_at, now)}
                    </span>
                    {(job.input_tokens > 0 || job.output_tokens > 0) && (
                      <span className="tabular-nums text-muted-foreground">
                        {TOKEN_FORMATTER.format(job.input_tokens)} ent. / {TOKEN_FORMATTER.format(job.output_tokens)} sal.
                      </span>
                    )}
                  </div>
                  <CollapsibleTrigger asChild>
                    <Button variant="outline" size="sm" className="group">
                      {expandedJob === job.id ? "Ocultar detalle" : "Ver recorrido"}
                      <ChevronDown className="ml-1 size-4 transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
                    </Button>
                  </CollapsibleTrigger>
                  {(job.error || delayed || job.status === "cancelling") && (
                    <p className={`w-full text-xs ${job.error && !CANCELLATION_REASON[job.error] ? "text-destructive" : "text-muted-foreground"}`}>
                      {job.error ? CANCELLATION_REASON[job.error] ?? job.error
                        : delayed ? (job.attempts ? "Reintento programado" : "Esperando que termine la serie de pushes")
                        : "Liberando conexión…"}
                    </p>
                  )}
                </div>
                <CollapsibleContent className="space-y-4 border-t bg-muted/20 p-3 sm:p-4">
                  <JobTrace job={job} refresh={refreshNonce} now={now} />
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
                    <span>Intentos · {job.attempts}</span>
                    {job.provider_used && <span>Proveedor · {job.provider_used}{job.fallback_used ? " (fallback)" : ""}</span>}
                    <span>OCR · {job.ocr_status}{job.ocr_duration_ms ? ` · ${formatDuration(job.ocr_duration_ms)}` : ""}{job.ocr_version ? ` · ${job.ocr_version}` : ""}</span>
                    {job.ocr_error && <span className="text-destructive">{job.ocr_error}</span>}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {user?.is_admin && (job.status === "pending" || job.status === "running") && (
                      <Button variant="outline" size="sm" disabled={acting !== null} onClick={() => void act(job.id, "cancel")}>
                        <Square className="mr-1 size-3" />{acting === job.id ? "Cancelando…" : "Cancelar"}
                      </Button>
                    )}
                    {job.status === "cancelling" && <LoaderCircle aria-label="Cancelando" className="size-4 animate-spin" />}
                    {user?.is_admin && job.status === "cancelled" && job.error !== "pr_merged" && (
                      <Button variant="outline" size="sm" disabled={acting !== null} onClick={() => void act(job.id, "resume")}>
                        <Play className="mr-1 size-3" />{acting === job.id ? "Reanudando…" : "Reanudar"}
                      </Button>
                    )}
                    {user?.is_admin && (job.status === "failed" || (job.status === "pending" && job.attempts > 0)) && (
                      <Button variant="outline" size="sm" disabled={acting !== null} onClick={() => void act(job.id, "retry")}>
                        {acting === job.id ? "Programando…" : "Reintentar"}
                      </Button>
                    )}
                    <Button asChild variant="outline" size="sm">
                      <Link to={`/settings/ai?job=${job.id}`}>Diagnóstico de IA</Link>
                    </Button>
                  </div>
                </CollapsibleContent>
              </Card>
            </Collapsible>
          )
        })}
      </div>
    </div>
  )
}

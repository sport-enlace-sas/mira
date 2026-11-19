import {
  AlertCircle, ArrowDownLeft, ArrowUpRight, ChevronDown, ChevronLeft, ChevronRight,
  Clock3, Code2, ExternalLink, FileCheck2, ListChecks, LoaderCircle,
  Play, RefreshCw, RotateCcw, Search, Square, X,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import { api, type ReviewJobModel } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useAsync, useDocumentTitle } from "@/lib/hooks"
import { cn } from "@/lib/utils"

import { formatDateTime, formatDuration, relativeTime, STAGE_NAME, TOKEN_FORMATTER } from "./advisory-format"
import { AdvisoryJobTrace } from "./advisory-job-trace"

type JobStatus = ReviewJobModel["status"]
type JobAction = "cancel" | "resume" | "retry"
type Filter = "all" | "active" | "issues" | JobStatus

const JOB_LIMIT = 200
const PAGE_SIZE = 12

const STATUS: Record<JobStatus, { label: string; color: string }> = {
  pending: { label: "En cola", color: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200" },
  running: { label: "En ejecución", color: "border-sky-500/30 bg-sky-500/10 text-sky-800 dark:text-sky-200" },
  completed: { label: "Finalizada", color: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200" },
  failed: { label: "Fallida", color: "border-destructive/30 bg-destructive/10 text-destructive" },
  superseded: { label: "SHA sustituido", color: "border-border bg-muted text-muted-foreground" },
  cancelling: { label: "Cancelando", color: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200" },
  cancelled: { label: "Cancelada", color: "border-border bg-muted text-muted-foreground" },
}

const CONCLUSION: Record<string, string> = {
  success: "Revisión completa",
  findings: "Con hallazgos",
  incomplete: "Revisión parcial",
  neutral: "Resultado neutral",
  review_failed: "Revisión fallida",
  failure: "Revisión fallida",
  cancelled: "Revisión cancelada",
  skipped: "Revisión omitida",
}

const CANCELLATION_REASON: Record<string, string> = {
  cancelled_by_user: "Cancelada manualmente",
  pr_merged: "PR fusionado",
  pr_closed: "PR cerrado",
  pr_draft: "PR movido a borrador",
  pr_reopen_requested: "Esperando que termine la cancelación del intento anterior",
  "worker restarted": "Trabajo recuperado después de reiniciar el worker",
  "review already running": "Ya había otra auditoría del mismo PR en curso",
}

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "Todas" },
  { value: "active", label: "En ejecución" },
  { value: "pending", label: "En cola" },
  { value: "issues", label: "Con incidencias" },
]

function currentSummary(job: ReviewJobModel) {
  return job.attempts > 0 && job.audit_summary?.attempt === job.attempts
    ? job.audit_summary
    : null
}

function matchesStatus(job: ReviewJobModel, status: Filter) {
  if (status === "all") return true
  if (status === "active") return job.status === "running" || job.status === "cancelling"
  if (status === "issues") {
    const summary = currentSummary(job)
    return job.status === "failed" || !!summary?.has_stage_failures
      || ["findings", "incomplete", "failure", "review_failed"].includes(summary?.result?.conclusion ?? "")
  }
  return job.status === status
}

function jobNote(job: ReviewJobModel, now: number) {
  if (job.status === "failed") return null
  if (job.status === "pending" && job.next_attempt_at > now / 1000) {
    const delay = formatDuration(job.next_attempt_at * 1000 - now)
    const schedule = job.attempts > 0
      ? "Reintento disponible en " + delay
      : "Disponible en " + delay + " si no entra otro push"
    return job.error ? schedule + " · " + (CANCELLATION_REASON[job.error] ?? job.error) : schedule
  }
  if (job.error) return CANCELLATION_REASON[job.error] ?? job.error
  if (job.status === "cancelling") return "Liberando la conexión…"
  return null
}

function actionAvailability(job: ReviewJobModel, isAdmin: boolean, action: JobAction, hasNewer: boolean) {
  if (!isAdmin) return "Disponible para administradores"
  if (action === "cancel") {
    return job.status === "running" || job.status === "pending"
      ? null : "Disponible mientras la revisión está en cola o en ejecución"
  }
  if (action === "resume") {
    if (hasNewer) return "Hay un SHA más reciente para este PR"
    if (job.status !== "cancelled" || job.platform !== "github") return "Disponible para revisiones canceladas de GitHub"
    return ["pr_merged", "pr_closed", "pr_draft"].includes(job.error) ? CANCELLATION_REASON[job.error] : null
  }
  return job.status === "failed" || (job.status === "pending" && job.attempts > 0)
    ? null : "Disponible si falló un intento o hay un reintento en cola"
}

const ACTIONS = [
  { id: "cancel", label: "Cancelar", busy: "Cancelando…", icon: Square },
  { id: "resume", label: "Reanudar", busy: "Reanudando…", icon: Play },
  { id: "retry", label: "Reintentar", busy: "Programando…", icon: RotateCcw },
] as const

function JobActions({
  job, acting, isAdmin, canDiagnose, hasNewer, onAction,
}: {
  job: ReviewJobModel
  acting: JobAction | undefined
  isAdmin: boolean
  canDiagnose: boolean
  hasNewer: boolean
  onAction: (action: JobAction) => Promise<void>
}) {
  const cancelButton = useRef<HTMLButtonElement>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const closeCancel = () => {
    setConfirmCancel(false)
    window.requestAnimationFrame(() => cancelButton.current?.focus())
  }
  return (
    <div role="group" aria-label={"Acciones de PR #" + job.pr_number} className="flex min-w-0 flex-wrap gap-2">
      {ACTIONS.map(({ id, label, busy, icon: Icon }) => {
        const reason = actionAvailability(job, isAdmin, id, hasNewer)
        const blocked = !!reason || !!acting
        const active = acting === id
        const buttonProps = {
          className: "h-9 px-2.5 aria-disabled:opacity-55 aria-disabled:cursor-not-allowed sm:px-3",
          variant: "outline" as const,
          "aria-disabled": blocked,
          title: reason ?? (acting ? "Actualizando esta revisión" : undefined),
        }
        const content = <>{active ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : <Icon aria-hidden="true" />}{active ? busy : label}</>
        return (
          <Button key={id} type="button" {...buttonProps} ref={id === "cancel" ? cancelButton : undefined}
            onClick={() => {
              if (blocked) return
              if (id === "cancel") setConfirmCancel(true)
              else void onAction(id)
            }}>
            {content}
            {reason && <span className="sr-only">. {reason}</span>}
          </Button>
        )
      })}
      {canDiagnose ? (
        <Button asChild variant="outline" className="h-9 px-3">
          <Link to={"/settings/ai?job=" + job.id}><Code2 aria-hidden="true" />Diagnóstico de IA</Link>
        </Button>
      ) : (
        <Button type="button" variant="outline" aria-disabled="true" title="Requiere permiso para ver IA"
          className="h-9 cursor-not-allowed px-3 opacity-55">
          <Code2 aria-hidden="true" />Diagnóstico de IA<span className="sr-only">. Requiere permiso para ver IA</span>
        </Button>
      )}
      <ConfirmDialog open={confirmCancel} loading={acting === "cancel"} destructive
        onOpenChange={(open) => { if (!open && acting !== "cancel") closeCancel() }}
        title={"¿Cancelar la revisión del PR #" + job.pr_number + "?"}
        description="MIRA detendrá la auditoría de este commit. Puedes reanudarla si el PR sigue abierto y conserva el mismo SHA."
        confirmLabel="Cancelar revisión" cancelLabel="Volver"
        onConfirm={() => {
          if (!acting && !actionAvailability(job, isAdmin, "cancel", hasNewer)) {
            void onAction("cancel").finally(closeCancel)
          } else {
            closeCancel()
          }
        }}
      />
    </div>
  )
}

function JobCard({
  job, open, setOpen, acting, isAdmin, canDiagnose, hasNewer, refresh, now, onAction,
}: {
  job: ReviewJobModel
  open: boolean
  setOpen: (open: boolean) => void
  acting: JobAction | undefined
  isAdmin: boolean
  canDiagnose: boolean
  hasNewer: boolean
  refresh: number
  now: number
  onAction: (action: JobAction) => Promise<void>
}) {
  const summary = currentSummary(job)
  const result = summary?.result
  const status = STATUS[job.status]
  const models = [...new Set(summary?.models.map((model) => model.model) ?? [])]
  const previousModels = job.models_attempted.split(" -> ").filter(Boolean)
  const modelNames = models.length ? models : previousModels
  const note = jobNote(job, now)
  const totals = job.audit_totals
  const inputTokens = totals ? totals.input_tokens : job.input_tokens || null
  const outputTokens = totals ? totals.output_tokens : job.output_tokens || null
  const tokensKnown = inputTokens != null || outputTokens != null
  const tokenCount = TOKEN_FORMATTER.format((inputTokens ?? 0) + (outputTokens ?? 0))
  const tokenTotal = tokensKnown ? (totals?.tokens_partial ? "≥ " : "") + tokenCount : null
  const working = job.status === "running" || job.status === "cancelling"
  const started = working && summary?.status === "running" ? summary.created_at : null
  const recorded = totals?.duration_ms ?? (job.audit_duration_ms || null)
  const elapsed = started != null ? (recorded ?? 0) + Math.max(0, now - started * 1000) : recorded
  const duration = elapsed == null ? null : formatDuration(elapsed)
  const showTotals = job.attempts > 0 || duration != null || tokensKnown
  const focusStages = working
    ? summary?.active_stages.map((stage) => {
        const name = STAGE_NAME[stage.id] ?? stage.id
        return stage.phases.includes("waiting_for_session") && !stage.phases.includes("requesting")
          ? name + " espera conexión" : name
      }) : undefined

  const header = useRef<HTMLButtonElement>(null)
  const changeOpen = (next: boolean) => {
    const button = header.current
    const main = button?.closest("main.overflow-auto")
    const scroller = main && main.scrollHeight > main.clientHeight ? main : document.scrollingElement
    const top = button?.getBoundingClientRect().top
    setOpen(next)
    if (button && scroller && top != null) {
      // Opening a row also closes the previous one, which may be above it.
      window.requestAnimationFrame(() => {
        scroller.scrollTop += button.getBoundingClientRect().top - top
      })
    }
  }
  const title = job.pr_title || "Pull request #" + job.pr_number
  const conclusion = result ? CONCLUSION[result.conclusion] : null

  return (
    <li className="min-w-0">
      <Collapsible open={open} onOpenChange={changeOpen}>
        <article aria-labelledby={"review-title-" + job.id} className={cn(
          "overflow-hidden rounded-xl border bg-card",
          open && "border-sky-600/35 dark:border-sky-400/35",
        )}>
          <h2 id={"review-title-" + job.id}>
            <CollapsibleTrigger asChild>
              <button ref={header} type="button" className="group flex w-full min-w-0 items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring sm:gap-3 sm:px-4">
                <span className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-md border bg-muted/40 text-muted-foreground",
                  open && "border-sky-600/25 bg-sky-500/10 text-sky-800 dark:text-sky-200",
                )}>
                  <ChevronDown aria-hidden="true" className="size-4 transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
                </span>
                <span className="min-w-0 flex-1">
                  <span title={title} className="block truncate text-sm font-semibold leading-snug">{title}</span>
                  <span className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                    <span title={job.owner + "/" + job.repo + " #" + job.pr_number} className="truncate text-sky-800 dark:text-sky-300">
                      {job.repo} #{job.pr_number}
                    </span>
                    <code title={job.head_sha} className="hidden shrink-0 font-mono @sm:inline">{job.head_sha.slice(0, 8)}</code>
                    {job.status !== "pending" && summary?.worker != null && (
                      <span className="hidden shrink-0 @md:inline">Worker {summary.worker}</span>
                    )}
                  </span>
                  {showTotals && (
                    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground tabular-nums">
                      <span className="inline-flex items-center gap-1 whitespace-nowrap">
                        <Clock3 aria-hidden="true" className="size-3" />
                        {duration ?? "Tiempo sin registro"}
                      </span>
                      <span className="whitespace-nowrap">{tokenTotal == null ? "Tokens sin registro" : tokenTotal + " tokens"}</span>
                    </span>
                  )}
                </span>
                <span className="flex shrink-0 flex-col items-end gap-0.5">
                  <span className={cn("inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-xs font-medium sm:px-2", status.color)}>
                    <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />{status.label}
                  </span>
                  {conclusion && <span className="hidden text-xs text-muted-foreground @lg:inline">{conclusion}</span>}
                </span>
              </button>
            </CollapsibleTrigger>
          </h2>
          <CollapsibleContent className="space-y-4 border-t bg-muted/20 p-3 sm:px-5 sm:py-4">
            <p className="text-sm font-medium wrap-anywhere">{title}</p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <JobActions job={job} acting={acting} isAdmin={isAdmin} canDiagnose={canDiagnose} hasNewer={hasNewer} onAction={onAction} />
              <div className="flex flex-wrap gap-2">
                {result?.report_url && (
                  <Button asChild variant="outline" className="h-9 px-3">
                    <a href={result.report_url} target="_blank" rel="noreferrer">
                      <FileCheck2 aria-hidden="true" />{result.report_kind === "check" ? "Ver check" : "Ver informe"}
                    </a>
                  </Button>
                )}
                <Button asChild variant="outline" className="h-9 px-3">
                  <a href={job.pr_url} target="_blank" rel="noreferrer">Abrir PR<ExternalLink aria-hidden="true" /></a>
                </Button>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="wrap-anywhere">{job.owner}/{job.repo} #{job.pr_number}</span>
              <code title={job.head_sha} className="font-mono">{job.head_sha.slice(0, 12)}</code>
              <time dateTime={new Date(job.created_at * 1000).toISOString()} title={formatDateTime(job.created_at)}>
                {relativeTime(job.created_at, now)}
              </time>
              {!result && summary?.has_stage_failures && <span className="text-amber-800 dark:text-amber-200">Etapas con incidencias</span>}
            </div>
            {focusStages && focusStages.length > 0 && (
              <p className="flex items-start gap-1.5 text-xs text-sky-800 dark:text-sky-200">
                <Clock3 aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />{focusStages.join(" · ")}
              </p>
            )}
            {note && <p className="text-sm text-muted-foreground wrap-anywhere">{note}</p>}
            <dl className="grid min-w-0 grid-cols-2 gap-x-3 gap-y-2 rounded-lg border bg-muted/35 p-3">
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Worker</dt>
                <dd className="mt-0.5 text-sm font-medium tabular-nums">
                  {job.status === "pending" ? "Por asignar" : summary?.worker != null ? "Worker " + summary.worker : "Sin registro"}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Duración acumulada</dt>
                <dd className="mt-0.5 text-sm font-medium tabular-nums">
                  {duration ?? "Sin registro"}
                </dd>
              </div>
              {modelNames.length > 0 && (
                <div className="col-span-2 min-w-0 border-t pt-2">
                  <dt className="text-xs text-muted-foreground">{job.status === "pending" ? "Modelos del último intento" : "Modelos registrados"}</dt>
                  <dd className="mt-0.5 font-mono text-xs leading-relaxed [overflow-wrap:anywhere]">{modelNames.join(" · ")}</dd>
                </div>
              )}
              {showTotals && (
                <div className="col-span-2 min-w-0 text-xs">
                  <dt className="mb-0.5 text-muted-foreground">Tokens acumulados{totals?.tokens_partial && tokensKnown ? " · mínimo registrado" : ""}</dt>
                  <dd className="flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums text-muted-foreground">
                    <span className="font-medium text-foreground">{tokenTotal ?? "Sin registro"}</span>
                    {tokensKnown && (
                      <>
                        <span className="inline-flex items-center gap-1"><ArrowDownLeft aria-hidden="true" className="size-3" />{TOKEN_FORMATTER.format(inputTokens ?? 0)} entrada</span>
                        <span className="inline-flex items-center gap-1"><ArrowUpRight aria-hidden="true" className="size-3" />{TOKEN_FORMATTER.format(outputTokens ?? 0)} salida</span>
                      </>
                    )}
                  </dd>
                </div>
              )}
            </dl>
            {result && (
              <section aria-label="Resultado de la revisión" className="rounded-lg border border-sky-600/20 bg-sky-600/5 px-3.5 py-3">
                <h3 className="text-sm font-semibold">Resultado de MIRA</h3>
                <p className="mt-1 text-sm">{CONCLUSION[result.conclusion] ?? "Consultar resultado en el PR"}</p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {result.files_reviewed != null && result.files_total != null && (
                    <span>{result.files_reviewed} de {result.files_total} archivos revisados en este alcance</span>
                  )}
                  {result.findings_count != null && (
                    <span>{result.findings_count} {result.findings_count === 1 ? "hallazgo" : "hallazgos"}</span>
                  )}
                </div>
              </section>
            )}
            <AdvisoryJobTrace job={job} refresh={refresh} now={now} />
            {(job.fallback_used || job.ocr_status !== "disabled" || job.ocr_error) && (
              <div className="flex flex-wrap gap-x-4 gap-y-2 border-t pt-3 text-xs text-muted-foreground">
                {job.fallback_used && <span>Se registró un proveedor alternativo{job.provider_used ? " · " + job.provider_used : ""}</span>}
                {job.ocr_status !== "disabled" && (
                  <span className="[overflow-wrap:anywhere]">
                    Revisión estática · {job.ocr_status === "used" ? "Ejecutada" : job.ocr_status}
                    {job.ocr_duration_ms > 0 && " · " + formatDuration(job.ocr_duration_ms)}
                    {job.ocr_version && " · " + job.ocr_version}
                  </span>
                )}
                {job.ocr_error && <span className="text-destructive [overflow-wrap:anywhere]">{job.ocr_error}</span>}
              </div>
            )}
          </CollapsibleContent>
        </article>
      </Collapsible>
    </li>
  )
}

export function ReviewJobsPage() {
  useDocumentTitle("Revisiones de PR")
  const { user } = useAuth()
  const [refresh, setRefresh] = useState(0)
  const [acting, setActing] = useState<Record<number, JobAction>>({})
  const [expandedJob, setExpandedJob] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<Filter>("all")
  const [page, setPage] = useState(0)
  const searchInput = useRef<HTMLInputElement>(null)
  const resultsHeader = useRef<HTMLDivElement>(null)
  const pendingRequest = useRef<Promise<ReviewJobModel[]> | null>(null)
  const { data, loading, error } = useAsync(() => {
    pendingRequest.current ??= api.listReviewJobs(JOB_LIMIT).finally(() => { pendingRequest.current = null })
    return pendingRequest.current
  }, [refresh])
  const jobs = data ?? []
  const newestJobByPr = new Map<string, number>()
  const prKey = (job: ReviewJobModel) => [job.platform, job.owner, job.repo, job.pr_number].join("/")
  for (const job of jobs) {
    const key = prKey(job)
    newestJobByPr.set(key, Math.max(job.id, newestJobByPr.get(key) ?? 0))
  }
  const hasNewer = (job: ReviewJobModel) => (newestJobByPr.get(prKey(job)) ?? job.id) > job.id
  const pollingInterval = !data || jobs.some((job) => matchesStatus(job, "active") || job.status === "pending")
    ? 5000 : 20_000

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return
      setNow(Date.now())
      setRefresh((count) => count + 1)
    }, pollingInterval)
    return () => window.clearInterval(timer)
  }, [pollingInterval])

  const refreshJobs = () => {
    setNow(Date.now())
    setRefresh((count) => count + 1)
  }

  const act = async (job: ReviewJobModel, action: JobAction) => {
    if (acting[job.id] || actionAvailability(job, !!user?.is_admin, action, hasNewer(job))) return
    setActing((pending) => ({ ...pending, [job.id]: action }))
    try {
      if (action === "cancel") await api.cancelReviewJob(job.id)
      else if (action === "resume") await api.resumeReviewJob(job.id)
      else await api.retryReviewJob(job.id)
      toast.success(action === "cancel" ? "Cancelación solicitada" : "Revisión puesta en cola")
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : ""
      const detail = message.match(/"detail"\s*:\s*"([^"\n]+)"/)?.[1]
      toast.error(detail || "No se pudo cambiar la revisión. Actualiza e inténtalo de nuevo.")
    } finally {
      setActing((pending) => {
        const next = { ...pending }
        delete next[job.id]
        return next
      })
      refreshJobs()
    }
  }

  const normalizedQuery = query.toLowerCase().trim()
  const workerQuery = normalizedQuery.match(/\bworker\s+(\d+)\b/)
  const terms = normalizedQuery.replace(/\bworker\s+\d+\b/, "").split(/\s+/).filter(Boolean)
  const filtered = jobs.filter((job) => {
    if (!matchesStatus(job, filter)) return false
    const summary = currentSummary(job)
    if (workerQuery && summary?.worker !== Number(workerQuery[1])) return false
    const text = [job.owner, job.repo, job.pr_title, "#" + job.pr_number, job.head_sha,
      summary?.worker == null ? "" : "worker " + summary.worker,
      ...(summary?.models.map((model) => model.model) ?? []), job.models_attempted].join(" ").toLowerCase()
    return terms.every((term) => /^#\d+$/.test(term) ? term === "#" + job.pr_number : text.includes(term))
  })
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount - 1)
  const start = currentPage * PAGE_SIZE
  const displayed = filtered.slice(start, start + PAGE_SIZE)
  const isAdmin = !!user?.is_admin
  const canDiagnose = isAdmin || !!user?.permissions?.includes("ai.view")

  const changeFilter = (next: Filter) => {
    setFilter(next)
    setPage(0)
  }
  const changeQuery = (value: string) => {
    setQuery(value)
    setPage(0)
  }
  const clearQuery = () => {
    changeQuery("")
    searchInput.current?.focus()
  }
  const changePage = (value: number) => {
    setPage(value)
    window.requestAnimationFrame(() => resultsHeader.current?.focus())
  }

  return (
    <div className="@container mx-auto min-w-0 max-w-7xl space-y-5 px-3 pb-8 pt-3 sm:px-6 sm:pt-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center sm:size-11 rounded-xl border border-sky-600/25 bg-sky-600/10 text-sky-800 dark:text-sky-300">
            <ListChecks aria-hidden="true" className="size-5" />
          </span>
          <h1 className="text-balance text-xl font-semibold tracking-tight sm:text-3xl">Revisiones de PR</h1>
        </div>
        <Button type="button" variant="outline" className="h-9 px-2.5 @sm:px-3" onClick={refreshJobs} aria-busy={loading} aria-label="Actualizar">
          <RefreshCw aria-hidden="true" className={loading ? "motion-safe:animate-spin" : undefined} /><span className="hidden @sm:inline">Actualizar</span>
        </Button>
      </header>

      <section aria-label="Filtros de revisiones" className="space-y-3 rounded-xl border bg-card p-3.5 sm:p-4">
        <div role="group" aria-label="Estado rápido" className="flex flex-wrap gap-2">
          {FILTERS.map(({ value, label }) => (
            <Button key={value} type="button" variant="outline" aria-pressed={filter === value}
              className={cn("h-9 gap-2 px-3", filter === value && "border-sky-600/35 bg-sky-600/10 text-sky-900 dark:text-sky-200")}
              onClick={() => changeFilter(value)}>
              {label}
              {data && <span className="rounded bg-muted px-1.5 tabular-nums">{jobs.filter((job) => matchesStatus(job, value)).length}</span>}
            </Button>
          ))}
        </div>
        <div className="grid gap-2 @xl:grid-cols-[minmax(0,1fr)_minmax(10rem,13rem)]">
          <div className="relative min-w-0">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input ref={searchInput} type="search" aria-label="Buscar revisiones" autoComplete="off" spellCheck={false} name="review-search"
              className="h-10 rounded-lg pl-9 pr-10 text-sm [&::-webkit-search-cancel-button]:appearance-none"
              placeholder="PR, SHA, worker o modelo…" value={query}
              onChange={(event) => changeQuery(event.target.value)} />
            {query && (
              <Button type="button" variant="ghost" size="icon" className="absolute right-1.5 top-1/2 -translate-y-1/2"
                aria-label="Limpiar búsqueda" onClick={clearQuery}><X aria-hidden="true" /></Button>
            )}
          </div>
          <select aria-label="Filtrar por estado" value={filter} onChange={(event) => changeFilter(event.target.value as Filter)}
            className="h-10 min-w-0 rounded-lg border border-input bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring">
            <option value="all">Todos los estados</option>
            <option value="active">En ejecución o cancelando</option>
            <option value="issues">Con incidencias</option>
            {Object.entries(STATUS).map(([value, { label }]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </div>
      </section>

      {error && (
        <p role="status" className="flex gap-2 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {data ? "Falló la actualización. Se muestran los últimos datos cargados." : "No se pudo cargar la cola. Pulsa Actualizar para volver a intentarlo."}
        </p>
      )}

      {data && (
        <div ref={resultsHeader} tabIndex={-1} className="flex scroll-mt-4 flex-wrap items-center justify-between gap-2 rounded-sm text-xs text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
          <p>{filtered.length} {filtered.length === 1 ? "revisión" : "revisiones"}{jobs.length === JOB_LIMIT && " de " + JOB_LIMIT + " registros cargados"}</p>
          {pageCount > 1 && <p>Página {currentPage + 1} de {pageCount}</p>}
        </div>
      )}

      {!displayed.length && (!error || data) && (
        <div className="rounded-xl border border-dashed bg-card px-5 py-9 text-center text-sm text-muted-foreground" role="status">
          {loading && !data ? "Cargando revisiones…" : jobs.length ? "Ninguna revisión coincide con estos filtros." : "Aún no hay revisiones en la cola."}
          {jobs.length > 0 && (
            <div className="mt-3">
              <Button type="button" variant="outline" className="h-9 px-3"
                onClick={() => { clearQuery(); changeFilter("all") }}>Limpiar filtros</Button>
            </div>
          )}
        </div>
      )}

      {displayed.length > 0 && (
        <ul aria-label="Revisiones por SHA" className="space-y-2">
          {displayed.map((job) => (
            <JobCard key={job.id} job={job} open={expandedJob === job.id}
              setOpen={(isOpen) => setExpandedJob(isOpen ? job.id : null)}
              acting={acting[job.id]} isAdmin={isAdmin} canDiagnose={canDiagnose}
              hasNewer={hasNewer(job)}
              refresh={refresh} now={now} onAction={(action) => act(job, action)} />
          ))}
        </ul>
      )}

      {pageCount > 1 && (
        <nav aria-label="Páginas de revisiones" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3">
          <span className="text-xs tabular-nums text-muted-foreground">{start + 1}–{start + displayed.length} de {filtered.length}</span>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" className="h-9 px-3" disabled={currentPage === 0}
              onClick={() => changePage(currentPage - 1)}><ChevronLeft aria-hidden="true" />Anterior</Button>
            <Button type="button" variant="outline" className="h-9 px-3" disabled={currentPage === pageCount - 1}
              onClick={() => changePage(currentPage + 1)}>Siguiente<ChevronRight aria-hidden="true" /></Button>
          </div>
        </nav>
      )}
    </div>
  )
}

import { ExternalLink, RefreshCw } from "lucide-react"
import {
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, type ReviewJobModel } from "@/lib/api"
import { useAsync, useDocumentTitle } from "@/lib/hooks"

const STATUS_VARIANT: Record<
  ReviewJobModel["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  running: "default",
  completed: "outline",
  failed: "destructive",
  superseded: "secondary",
}

const COLUMNS = [
  {
    key: "pullRequest",
    label: "Pull request / SHA",
    width: 300,
    minWidth: 220,
  },
  { key: "status", label: "Status", width: 120, minWidth: 96 },
  { key: "provider", label: "Provider", width: 150, minWidth: 110 },
  { key: "models", label: "Models attempted", width: 220, minWidth: 150 },
  { key: "ocr", label: "OCR", width: 220, minWidth: 140 },
  { key: "duration", label: "Audit duration", width: 130, minWidth: 110 },
  { key: "tokens", label: "Tokens", width: 150, minWidth: 120 },
  { key: "attempts", label: "Attempts", width: 100, minWidth: 80 },
  { key: "actions", label: "Actions", width: 110, minWidth: 90 },
  { key: "updated", label: "Updated", width: 110, minWidth: 90 },
] as const

type ColumnKey = (typeof COLUMNS)[number]["key"]
type ColumnWidths = Record<ColumnKey, number>

const INITIAL_COLUMN_WIDTHS = Object.fromEntries(
  COLUMNS.map((column) => [column.key, column.width])
) as ColumnWidths

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US")

function relativeTime(epoch: number) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch))
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function formatDuration(durationMs: number) {
  if (durationMs < 1_000) return `${durationMs} ms`
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

export function ReviewJobsPage() {
  useDocumentTitle("Advisory jobs")
  const [refreshNonce, setRefreshNonce] = useState(0)
  const {
    data: jobs,
    loading,
    error,
  } = useAsync(() => api.listReviewJobs(), [refreshNonce])
  const [retrying, setRetrying] = useState<number | null>(null)
  const [columnWidths, setColumnWidths] = useState<ColumnWidths>(
    INITIAL_COLUMN_WIDTHS
  )
  const [nowEpochSeconds] = useState(() => Date.now() / 1_000)

  const resizeColumnBy = (key: ColumnKey, delta: number) => {
    const column = COLUMNS.find((candidate) => candidate.key === key)
    if (!column) return
    setColumnWidths((current) => ({
      ...current,
      [key]: Math.max(column.minWidth, current[key] + delta),
    }))
  }

  const beginColumnResize = (
    event: ReactPointerEvent<HTMLDivElement>,
    key: ColumnKey
  ) => {
    event.preventDefault()
    const column = COLUMNS.find((candidate) => candidate.key === key)
    if (!column) return

    const startX = event.clientX
    const startWidth = columnWidths[key]

    const move = (pointerEvent: PointerEvent) => {
      const nextWidth = Math.max(
        column.minWidth,
        startWidth + pointerEvent.clientX - startX
      )
      setColumnWidths((current) => ({ ...current, [key]: nextWidth }))
    }
    const stop = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", stop)
      window.removeEventListener("pointercancel", stop)
    }

    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", stop)
    window.addEventListener("pointercancel", stop)
  }

  const handleResizeKey = (
    event: KeyboardEvent<HTMLDivElement>,
    key: ColumnKey
  ) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
    event.preventDefault()
    resizeColumnBy(key, event.key === "ArrowLeft" ? -16 : 16)
  }

  const tableWidth = COLUMNS.reduce(
    (total, column) => total + columnWidths[column.key],
    0
  )

  const retry = async (jobId: number) => {
    setRetrying(jobId)
    try {
      await api.retryReviewJob(jobId)
      setRefreshNonce((n) => n + 1)
    } finally {
      setRetrying(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Advisory jobs
          </h1>
          <p className="text-sm text-muted-foreground">
            Native durable queue by pull-request SHA. Superseded work never
            publishes a review.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setRefreshNonce((n) => n + 1)}
          disabled={loading}
        >
          <RefreshCw
            className={loading ? "mr-2 size-4 animate-spin" : "mr-2 size-4"}
          />{" "}
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent executions</CardTitle>
          <p className="text-xs text-muted-foreground">
            Drag a column edge to resize it. Double-click an edge to reset its
            width. CLI-backed token counts are estimates.
          </p>
        </CardHeader>
        <CardContent className="p-0">
          {error ? (
            <p className="p-6 text-sm text-destructive">
              Could not load review jobs.
            </p>
          ) : loading && !jobs ? (
            <p className="p-6 text-sm text-muted-foreground">Loading queue…</p>
          ) : !jobs?.length ? (
            <p className="p-6 text-sm text-muted-foreground">
              No advisory jobs have been received yet.
            </p>
          ) : (
            <Table
              className="table-fixed"
              style={{ width: tableWidth, minWidth: tableWidth }}
            >
              <colgroup>
                {COLUMNS.map((column) => (
                  <col
                    key={column.key}
                    style={{ width: columnWidths[column.key] }}
                  />
                ))}
              </colgroup>
              <TableHeader>
                <TableRow>
                  {COLUMNS.map((column) => (
                    <TableHead
                      key={column.key}
                      className="relative overflow-hidden pr-3"
                    >
                      <span className="block truncate">{column.label}</span>
                      <div
                        role="separator"
                        aria-label={`Resize ${column.label} column`}
                        aria-orientation="vertical"
                        aria-valuemin={column.minWidth}
                        aria-valuenow={columnWidths[column.key]}
                        tabIndex={0}
                        className="group absolute inset-y-0 right-0 z-10 w-2 cursor-col-resize touch-none outline-none select-none focus-visible:bg-primary/10"
                        onPointerDown={(event) =>
                          beginColumnResize(event, column.key)
                        }
                        onKeyDown={(event) =>
                          handleResizeKey(event, column.key)
                        }
                        onDoubleClick={() =>
                          setColumnWidths((current) => ({
                            ...current,
                            [column.key]: column.width,
                          }))
                        }
                      >
                        <span className="absolute inset-y-2 right-0 w-px bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary" />
                      </div>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="overflow-hidden">
                      <a
                        href={job.pr_url}
                        target="_blank"
                        rel="noreferrer"
                        className="block truncate font-medium hover:underline"
                      >
                        {job.owner}/{job.repo} #{job.pr_number}
                        <ExternalLink className="ml-1 inline size-3" />
                      </a>
                      <div className="mt-1 font-mono text-xs text-muted-foreground">
                        {job.head_sha.slice(0, 12)}
                      </div>
                      {job.error && (
                        <div className="mt-1 truncate text-xs text-destructive">
                          {job.error}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden">
                      <Badge variant={STATUS_VARIANT[job.status]}>
                        {job.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="overflow-hidden text-sm">
                      {job.provider_used || "pending"}
                      {job.fallback_used && (
                        <span className="ml-1 text-xs text-amber-600">
                          fallback
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden text-xs">
                      {job.models_attempted ? (
                        job.models_attempted.split(" -> ").map((model) => (
                          <div
                            key={model}
                            className="truncate font-mono text-muted-foreground"
                          >
                            {model}
                          </div>
                        ))
                      ) : (
                        <span className="text-muted-foreground">pending</span>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden text-xs">
                      <span>{job.ocr_status}</span>
                      {job.ocr_version && (
                        <div className="truncate text-muted-foreground">
                          {job.ocr_version}
                        </div>
                      )}
                      {job.ocr_error && (
                        <div className="truncate text-destructive">
                          {job.ocr_error}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden text-sm text-muted-foreground">
                      {job.audit_duration_ms > 0
                        ? formatDuration(job.audit_duration_ms)
                        : "pending"}
                    </TableCell>
                    <TableCell className="overflow-hidden text-xs">
                      {job.audit_duration_ms > 0 ||
                      job.input_tokens > 0 ||
                      job.output_tokens > 0 ? (
                        <>
                          <div>
                            <span className="text-muted-foreground">In</span>{" "}
                            {TOKEN_FORMATTER.format(job.input_tokens)}
                          </div>
                          <div>
                            <span className="text-muted-foreground">Out</span>{" "}
                            {TOKEN_FORMATTER.format(job.output_tokens)}
                          </div>
                        </>
                      ) : (
                        <span className="text-muted-foreground">pending</span>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden">
                      {job.attempts}
                      {job.status === "pending" &&
                        job.attempts > 0 &&
                        job.next_attempt_at > nowEpochSeconds && (
                          <div className="mt-1 text-xs text-muted-foreground">
                            retry scheduled
                          </div>
                        )}
                    </TableCell>
                    <TableCell className="overflow-hidden">
                      {(job.status === "failed" ||
                        job.status === "pending") && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={retrying !== null}
                          onClick={() => void retry(job.id)}
                        >
                          {retrying === job.id ? "Scheduling…" : "Retry"}
                        </Button>
                      )}
                    </TableCell>
                    <TableCell className="overflow-hidden text-sm text-muted-foreground">
                      {relativeTime(job.updated_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

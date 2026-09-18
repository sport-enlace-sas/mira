import { ExternalLink, RefreshCw } from "lucide-react"
import { useState } from "react"

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

const STATUS_VARIANT: Record<ReviewJobModel["status"], "default" | "secondary" | "destructive" | "outline"> = {
  pending: "secondary",
  running: "default",
  completed: "outline",
  failed: "destructive",
  superseded: "secondary",
}

function relativeTime(epoch: number) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch))
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function ReviewJobsPage() {
  useDocumentTitle("Advisory jobs")
  const [refreshNonce, setRefreshNonce] = useState(0)
  const { data: jobs, loading, error } = useAsync(() => api.listReviewJobs(), [refreshNonce])
  const [retrying, setRetrying] = useState<number | null>(null)

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
          <h1 className="text-2xl font-semibold tracking-tight">Advisory jobs</h1>
          <p className="text-sm text-muted-foreground">
            Native durable queue by pull-request SHA. Superseded work never publishes a review.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setRefreshNonce((n) => n + 1)} disabled={loading}>
          <RefreshCw className={loading ? "mr-2 size-4 animate-spin" : "mr-2 size-4"} /> Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent executions</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {error ? (
            <p className="p-6 text-sm text-destructive">Could not load review jobs.</p>
          ) : loading && !jobs ? (
            <p className="p-6 text-sm text-muted-foreground">Loading queue…</p>
          ) : !jobs?.length ? (
            <p className="p-6 text-sm text-muted-foreground">No advisory jobs have been received yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pull request / SHA</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>OCR</TableHead>
                  <TableHead>Attempts</TableHead>
                  <TableHead />
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="min-w-72">
                      <a href={job.pr_url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
                        {job.owner}/{job.repo} #{job.pr_number}<ExternalLink className="ml-1 inline size-3" />
                      </a>
                      <div className="mt-1 font-mono text-xs text-muted-foreground">{job.head_sha.slice(0, 12)}</div>
                      {job.error && <div className="mt-1 max-w-80 truncate text-xs text-destructive">{job.error}</div>}
                    </TableCell>
                    <TableCell><Badge variant={STATUS_VARIANT[job.status]}>{job.status}</Badge></TableCell>
                    <TableCell className="text-sm">
                      {job.provider_used || "pending"}
                      {job.fallback_used && <span className="ml-1 text-xs text-amber-600">fallback</span>}
                    </TableCell>
                    <TableCell className="text-xs">
                      <span>{job.ocr_status}</span>
                      {job.ocr_version && <div className="text-muted-foreground">{job.ocr_version}</div>}
                      {job.ocr_error && <div className="text-destructive">{job.ocr_error}</div>}
                    </TableCell>
                    <TableCell>
                      {job.attempts}
                      {job.status === "pending" && job.attempts > 0 && job.next_attempt_at > Date.now() / 1000 && (
                        <div className="mt-1 text-xs text-muted-foreground">retry scheduled</div>
                      )}
                    </TableCell>
                    <TableCell>
                      {(job.status === "failed" || job.status === "pending") && (
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
                    <TableCell className="text-sm text-muted-foreground">{relativeTime(job.updated_at)}</TableCell>
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

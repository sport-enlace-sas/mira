import { fetchJson, postJson } from "./http"
import type { ReviewAuditTrace, ReviewJobModel } from "./types"

export const reviewJobsApi = {
  reviewJobTrace: (jobId: number) =>
    fetchJson<ReviewAuditTrace | null>(`/api/review-jobs/${encodeURIComponent(jobId)}/trace`),
  listReviewJobs: (limit = 200) =>
    fetchJson<ReviewJobModel[]>(
      `/api/review-jobs?limit=${encodeURIComponent(limit)}`
    ),
  retryReviewJob: (jobId: number) =>
    postJson<ReviewJobModel>(
      `/api/review-jobs/${encodeURIComponent(jobId)}/retry`,
      {}
    ),
  cancelReviewJob: (jobId: number) =>
    postJson<ReviewJobModel>(
      `/api/review-jobs/${encodeURIComponent(jobId)}/cancel`,
      {}
    ),
  resumeReviewJob: (jobId: number) =>
    postJson<ReviewJobModel>(
      `/api/review-jobs/${encodeURIComponent(jobId)}/resume`,
      {}
    ),
}

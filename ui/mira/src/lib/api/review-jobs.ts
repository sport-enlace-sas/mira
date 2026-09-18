import { fetchJson, postJson } from "./http"
import type { ReviewJobModel } from "./types"

export const reviewJobsApi = {
  listReviewJobs: (limit = 200) =>
    fetchJson<ReviewJobModel[]>(`/api/review-jobs?limit=${encodeURIComponent(limit)}`),
  retryReviewJob: (jobId: number) =>
    postJson<ReviewJobModel>(`/api/review-jobs/${encodeURIComponent(jobId)}/retry`, {}),
}

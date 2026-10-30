import { deleteJson, fetchJson, postJson, putJson } from "./http"

export type Credential = {
  id: string
  revision: number
  created_at?: number
  kind: string
  status: string
  renewable: boolean
  checked_at: number | null
  expires_at?: number | null
}
export type Connection = {
  id: string
  name: string
  provider: string
  base_url: string
  region: string
  enabled: boolean
  active_credential: string
  credentials: Credential[]
}
export type FlowNode = {
  id: string
  name: string
  connection_id: string
  model: string
  reasoning_effort: string
  timeout: number
  retries: number
  context_window: number
  position?: { x: number; y: number }
}
export type Flow = {
  id: string
  name: string
  version: number
  nodes: FlowNode[]
  edges: { source: string; target: string }[]
}
export type Step = {
  connection_name?: string
  node_id: string
  node_name: string
  model: string
  reasoning_effort: string
  provider: string
  result: string
  phase?: string
  cause: string
  action: string
  duration_ms: number
  attempt: number
  refresh_attempted: boolean
  usage: { prompt_tokens?: number; completion_tokens?: number }
  reset_at?: number
  retry_at?: number
}
export type Run = {
  id: string
  created_at: number
  scenario: string
  flow_id: string
  flow_version: number
  mode: string
  status: string
  steps: Step[]
  job_id: string
}
export type Model = {
  id: string
  name: string
  status: string
  source: string
  reasoning_efforts?: string[]
  context_window?: number
  access?: Record<string, { status?: string; checked_at?: number }>
  quota?: { until?: number }
}
export type Login = {
  id: string
  connection_id: string
  status: string
  url?: string
  user_code?: string
  credential_id?: string
  expires_at: number
}
export type Diagnostic = {
  id: string
  status: string
  result?: Run
  error?: string
}
export type DiagnosticInput = {
  mode: "simulation" | "real"
  flow_id?: string
  connection_id?: string
  credential_id?: string
  model?: string
  reasoning_effort?: string
  action: "connection" | "model" | "rotation" | "flow"
  faults?: Record<string, string>
}

export const aiApi = {
  status: () =>
    fetchJson<{
      mode: string
      permissions: string[]
      scenarios: Record<string, string>
      key_present: boolean
    }>("/api/ai/status"),
  connections: () => fetchJson<Connection[]>("/api/ai/connections"),
  saveConnection: (
    body: Omit<Connection, "id" | "credentials" | "active_credential">,
    id?: string
  ) =>
    id
      ? putJson<{ id: string }>(`/api/ai/connections/${id}`, body)
      : postJson<{ id: string }>("/api/ai/connections", body),
  importCredential: (id: string, kind: string, secret: unknown) =>
    postJson<{ id: string }>(`/api/ai/connections/${id}/credentials`, {
      kind,
      secret,
    }),
  activateCredential: (connection: string, credential: string) =>
    postJson(
      `/api/ai/connections/${connection}/credentials/${credential}/activate`,
      {}
    ),
  models: (id: string) =>
    fetchJson<{ models: Model[]; status: string; checked_at: number | null }>(
      `/api/ai/connections/${id}/models`
    ),
  refreshModels: (id: string) =>
    postJson<{ models: Model[] }>(
      `/api/ai/connections/${id}/models/refresh`,
      {}
    ),
  login: (id: string) => postJson<Login>(`/api/ai/connections/${id}/login`, {}),
  resumeLogin: (id: string) =>
    fetchJson<Login | null>(`/api/ai/connections/${id}/login`),
  loginStatus: (id: string) => fetchJson<Login>(`/api/ai/logins/${id}`),
  loginCode: (id: string, code: string) =>
    postJson<Login>(`/api/ai/logins/${id}/code`, { code }),
  cancelLogin: (id: string) => deleteJson(`/api/ai/logins/${id}`),
  flows: () =>
    fetchJson<Pick<Flow, "id" | "name" | "version">[]>("/api/ai/flows"),
  flow: (id: string, version?: number) =>
    fetchJson<Flow>(
      `/api/ai/flows/${id}${version ? `?version=${version}` : ""}`
    ),
  versions: (id: string) => fetchJson<number[]>(`/api/ai/flows/${id}/versions`),
  saveFlow: (flow: Flow) => {
    const body = {
      name: flow.name,
      nodes: flow.nodes,
      edges: flow.edges,
      expected_version: flow.version,
    }
    return flow.id
      ? putJson<{ id: string }>(`/api/ai/flows/${flow.id}`, body)
      : postJson<{ id: string }>("/api/ai/flows", body)
  },
  assignments: () => fetchJson<Record<string, string>>("/api/ai/assignments"),
  assign: (body: Record<string, string>) =>
    putJson("/api/ai/assignments", body),
  diagnose: (body: DiagnosticInput) =>
    postJson<Diagnostic>("/api/ai/diagnostics/start", body),
  diagnostic: (id: string) =>
    fetchJson<Diagnostic>(`/api/ai/diagnostics/${id}`),
  cancelDiagnostic: (id: string) => deleteJson(`/api/ai/diagnostics/${id}`),
  runs: (job?: string) =>
    fetchJson<Run[]>(
      `/api/ai/runs${job ? `?job_id=${encodeURIComponent(job)}` : ""}`
    ),
  run: (id: string) => fetchJson<Run>(`/api/ai/runs/${id}`),
  audit: () =>
    fetchJson<
      {
        id: string
        actor: number
        action: string
        resource: string
        created_at: number
      }[]
    >("/api/ai/audit"),
  migrate: () => postJson("/api/ai/migration/prepare", {}),
  activate: () => postJson("/api/ai/activate", {}),
  permissions: (id: number, permissions: string[]) =>
    putJson(`/api/ai/users/${id}/permissions`, { permissions }),
}

import type { Connection } from "@/lib/api/ai"

export const selectClass =
  "h-9 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"

export const efforts = ["off", "low", "medium", "high", "xhigh", "max"]

export const statusLabel: Record<string, string> = {
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

export const providerNames: Record<string, string> = {
  "claude-cli": "Claude",
  "codex-cli": "Codex",
  openai: "API compatible con OpenAI",
  responses: "OpenAI Responses",
  bedrock: "Amazon Bedrock",
}

export function connectionState(connection: Connection, now?: number) {
  if (!connection.enabled) return "Desactivada"
  const active = connection.credentials.find((session) => session.id === connection.active_credential)
  if (active?.expires_at && !active.renewable && now != null && active.expires_at <= now) return "Sesión vencida"
  if (active?.status === "ready") return "Lista para usar"
  if (active?.status === "auth_blocked") return "Requiere reconexión"
  return connection.credentials.length ? "Sin sesión activa" : "Sin sesiones"
}

export const effortNames: Record<string, string> = {
  off: "Automático",
  low: "Bajo",
  medium: "Medio",
  high: "Alto",
  xhigh: "Extra alto",
  max: "Máximo · max",
}

export function friendlyError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)
  try {
    const detail = JSON.parse(message.replace(/^API error \d+: /, "")).detail
    if (typeof detail === "string") return detail
  } catch {
    return message
  }
  return message
}

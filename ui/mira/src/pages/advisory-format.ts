export const TOKEN_FORMATTER = new Intl.NumberFormat("es-VE")

export const STAGE_NAME: Record<string, string> = {
  ingestion: "Ingestión y contexto",
  indexing: "Indexación",
  review: "Revisión del PR",
  security: "Seguridad",
  dependencies: "Dependencias",
  walkthrough: "Recorrido del cambio",
  summary: "Resumen",
  self_critique: "Autocrítica",
  fix_verification: "Verificación de correcciones",
  overlap: "Solapamiento con otros PR",
}

const DATE_TIME = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" })
const RELATIVE_TIME = new Intl.RelativeTimeFormat("es", { numeric: "auto" })

export function formatDateTime(epochSeconds: number) {
  return DATE_TIME.format(epochSeconds * 1000)
}

export function relativeTime(epochSeconds: number, nowMs: number) {
  const seconds = Math.max(0, Math.floor(nowMs / 1000 - epochSeconds))
  if (seconds < 60) return "Ahora"
  if (seconds < 3600) return RELATIVE_TIME.format(-Math.floor(seconds / 60), "minute")
  if (seconds < 86400) return RELATIVE_TIME.format(-Math.floor(seconds / 3600), "hour")
  return RELATIVE_TIME.format(-Math.floor(seconds / 86400), "day")
}

export function formatDuration(durationMs: number) {
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`
  if (durationMs < 60_000) {
    const seconds = durationMs / 1000
    return `${seconds < 10 ? seconds.toFixed(1).replace(".", ",") : Math.floor(seconds)} s`
  }
  const seconds = Math.floor(durationMs / 1000)
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

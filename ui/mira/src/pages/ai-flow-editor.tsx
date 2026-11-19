import { useEffect, useId, useRef, useState } from "react"
import { ArrowDown, ArrowUp, ChevronDown, GitBranch, Plus, Trash2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { aiApi, type Connection, type Flow, type FlowNode, type Model } from "@/lib/api/ai"
import { cn } from "@/lib/utils"

import { connectionState, effortNames, efforts, friendlyError, selectClass, statusLabel } from "./ai-display"

function orderedSteps(flow: Flow) {
  const incoming = new Set(flow.edges.map((edge) => edge.target))
  const outgoing = new Map(flow.edges.map((edge) => [edge.source, edge.target]))
  const byId = new Map(flow.nodes.map((node) => [node.id, node]))
  const ordered: FlowNode[] = []
  let next = flow.nodes.find((node) => !incoming.has(node.id))
  while (next && ordered.length < flow.nodes.length) {
    ordered.push(next)
    next = byId.get(outgoing.get(next.id) ?? "")
  }
  return ordered.length === flow.nodes.length ? ordered : flow.nodes
}

function defaultStep(connections: Connection[], index: number, now: number): FlowNode {
  const connection = connections.find((item) => connectionState(item, now) === "Lista para usar")
    ?? connections.find((item) => item.enabled)
    ?? connections[0]
  return {
    id: crypto.randomUUID(),
    name: index === 0 ? "Modelo principal" : "Alternativa " + index,
    connection_id: connection?.id ?? "",
    model: "",
    reasoning_effort: "off",
    timeout: 120,
    retries: 0,
    context_window: 120000,
  }
}

function StepSettings({
  step, connections, editable, now, onChange,
}: {
  step: FlowNode
  connections: Connection[]
  editable: boolean
  now: number
  onChange: (patch: Partial<FlowNode>) => void
}) {
  const id = useId()
  const [loaded, setLoaded] = useState<{ connection: string; models: Model[] | null } | null>(null)
  const [customModel, setCustomModel] = useState(false)
  const currentConnection = connections.find((connection) => connection.id === step.connection_id)
  const result = loaded?.connection === step.connection_id ? loaded : null
  const catalog = result?.models
  const catalogFailed = result != null && catalog == null
  const model = catalog?.find((entry) => entry.id === step.model)
  const manual = customModel || catalogFailed || catalog?.length === 0
  const permittedEfforts = model?.reasoning_efforts?.length
    ? efforts.filter((effort) => effort === "off" || model.reasoning_efforts?.includes(effort))
    : efforts

  useEffect(() => {
    if (!step.connection_id) return
    let cancelled = false
    void aiApi.models(step.connection_id).then(
      (result) => { if (!cancelled) setLoaded({ connection: step.connection_id, models: result.models }) },
      () => { if (!cancelled) setLoaded({ connection: step.connection_id, models: null }) },
    )
    return () => { cancelled = true }
  }, [step.connection_id])

  function selectModel(value: string) {
    const selected = catalog?.find((entry) => entry.id === value)
    onChange({
      model: value,
      reasoning_effort: selected?.reasoning_efforts?.length && step.reasoning_effort !== "off"
        && !selected.reasoning_efforts.includes(step.reasoning_effort) ? "off" : step.reasoning_effort,
      context_window: selected?.context_window
        ? Math.min(step.context_window, selected.context_window) : step.context_window,
    })
  }

  return (
    <fieldset disabled={!editable} className="min-w-0 space-y-3 border-t bg-muted/15 p-3 sm:p-4">
      <div className="grid min-w-0 gap-3 md:grid-cols-2">
        <div className="min-w-0 space-y-1">
          <label htmlFor={id + "-connection"} className="text-sm font-medium">Conexión</label>
          <select id={id + "-connection"} className={selectClass} value={step.connection_id}
            onChange={(event) => {
              setCustomModel(false)
              onChange({ connection_id: event.target.value, model: "", reasoning_effort: "off" })
            }}>
            <option value="">Elige una conexión</option>
            {!currentConnection && step.connection_id && <option value={step.connection_id}>Conexión guardada sin acceso</option>}
            {connections.map((connection) => (
              <option key={connection.id} value={connection.id}>{connection.name} · {connectionState(connection, now)}</option>
            ))}
          </select>
        </div>
        <div className="min-w-0 space-y-1">
          <label htmlFor={id + "-model"} className="text-sm font-medium">{manual ? "Identificador del modelo" : "Modelo"}</label>
          {manual ? (
            <Input className="h-9 font-mono text-sm" id={id + "-model"} value={step.model} maxLength={200}
              autoComplete="off" spellCheck={false} placeholder="Identificador del proveedor…"
              onChange={(event) => selectModel(event.target.value)} />
          ) : (
            <select id={id + "-model"} className={selectClass} value={step.model} disabled={!step.connection_id}
              onChange={(event) => selectModel(event.target.value)}>
              <option value="">{!step.connection_id ? "Selecciona una conexión primero" : catalog ? "Elige un modelo" : "Cargando catálogo…"}</option>
              {step.model && !model && <option value={step.model}>{step.model} · Selección guardada</option>}
              {catalog?.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.id} · {statusLabel[entry.status] ?? entry.status}</option>
              ))}
            </select>
          )}
          {editable && !!step.connection_id && (
            catalogFailed || catalog?.length === 0
              ? <p className="text-xs text-muted-foreground">Catálogo no disponible. Puedes introducir el identificador del proveedor.</p>
              : <button type="button" className="rounded text-xs font-medium text-sky-800 underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring dark:text-sky-300"
                  onClick={() => setCustomModel(!customModel)}>
                  {manual ? "Elegir del catálogo" : "Ingresar un modelo manualmente"}
                </button>
          )}
        </div>
      </div>
      <details className="group/advanced rounded-md border bg-card p-2.5 sm:px-3">
        <summary className="flex min-h-7 list-none cursor-pointer items-center gap-2 rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          <ChevronDown aria-hidden="true" className="size-4 transition-transform group-open/advanced:rotate-180 motion-reduce:transition-none" />Ajustes avanzados
        </summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1">
            <label htmlFor={id + "-name"} className="text-sm font-medium">Nombre del paso</label>
            <Input id={id + "-name"} className="h-9" maxLength={120} value={step.name}
              onChange={(event) => onChange({ name: event.target.value })} />
          </div>
          <div className="space-y-1">
            <label htmlFor={id + "-effort"} className="text-sm font-medium">Razonamiento</label>
            <select id={id + "-effort"} className={selectClass} value={step.reasoning_effort}
              onChange={(event) => onChange({ reasoning_effort: event.target.value })}>
              {!permittedEfforts.includes(step.reasoning_effort) && (
                <option value={step.reasoning_effort}>{effortNames[step.reasoning_effort] ?? step.reasoning_effort} · Guardado</option>
              )}
              {permittedEfforts.map((effort) => <option key={effort} value={effort}>{effortNames[effort]}</option>)}
            </select>
          </div>
          {([
            ["timeout", "Tiempo límite (s)", 1, 7200],
            ["retries", "Reintentos", 0, 10],
            ["context_window", "Contexto (tokens)", 1024, undefined],
          ] as const).map(([key, label, min, max]) => (
            <div key={key} className="space-y-1">
              <label htmlFor={id + "-" + key} className="text-sm font-medium">{label}</label>
              <Input id={id + "-" + key} className="h-9" type="number" min={min} max={max}
                value={step[key]} onChange={(event) => onChange({ [key]: Number(event.target.value) })} />
              {key === "context_window" && model?.context_window && (
                <button type="button" className="rounded text-xs text-sky-800 hover:underline focus-visible:outline-2 focus-visible:outline-ring dark:text-sky-300"
                  onClick={() => onChange({ context_window: model.context_window })}>
                  Usar {model.context_window.toLocaleString("es-VE")} tokens anunciados
                </button>
              )}
            </div>
          ))}
        </div>
      </details>
    </fieldset>
  )
}

export function FlowEditor({
  flow, connections, save, editable, now, onDirtyChange,
}: {
  flow: Flow
  connections: Connection[]
  save: (flow: Flow) => Promise<void>
  editable: boolean
  now: number
  onDirtyChange: (dirty: boolean) => void
}) {
  const [baseline] = useState(() => flow.nodes.length ? orderedSteps(flow) : [defaultStep(connections, 0, now)])
  const [name, setName] = useState(flow.name)
  const [steps, setSteps] = useState(baseline)
  const [expanded, setExpanded] = useState(baseline[0]?.id ?? "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const addButton = useRef<HTMLButtonElement>(null)
  const dirty = name !== flow.name || JSON.stringify(steps) !== JSON.stringify(baseline)
  const canEdit = editable && !saving
  const incomplete = steps.some((step) => !step.connection_id || !step.model.trim() || !step.name.trim())

  useEffect(() => { onDirtyChange(dirty) }, [dirty, onDirtyChange])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [dirty])

  const move = (index: number, offset: number) => {
    setSteps((previous) => {
      const ordered = [...previous]
      const [step] = ordered.splice(index, 1)
      ordered.splice(index + offset, 0, step)
      return ordered
    })
  }

  const publish = async () => {
    if (!canEdit || !name.trim() || incomplete) return
    setSaving(true)
    setError("")
    try {
      await save({
        ...flow,
        name: name.trim(),
        nodes: steps.map((step) => ({ ...step, name: step.name.trim(), model: step.model.trim() })),
        edges: steps.slice(1).map((step, index) => ({ source: steps[index].id, target: step.id })),
      })
    } catch (failure) {
      setError(friendlyError(failure))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form aria-label="Editor de flujo" className="min-w-0 space-y-4 rounded-xl border bg-card p-3 shadow-xs sm:p-5"
      onSubmit={(event) => { event.preventDefault(); void publish() }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-base font-semibold"><GitBranch aria-hidden="true" className="size-4 text-sky-700 dark:text-sky-300" />Orden de ejecución</h3>
          <p className="mt-1 text-sm text-muted-foreground">MIRA empieza por el modelo principal y prueba la siguiente alternativa cuando hace falta.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">{flow.version ? "Versión " + flow.version : "Nuevo flujo"}</Badge>
          {dirty && <Badge variant="secondary">Cambios sin publicar</Badge>}
        </div>
      </div>
      <div className="max-w-lg space-y-1">
        <label htmlFor="ai-flow-name" className="text-sm font-medium">Nombre del flujo</label>
        <Input id="ai-flow-name" className="h-9" value={name} maxLength={120} required disabled={!canEdit}
          placeholder="Revisión de PR…" onChange={(event) => setName(event.target.value)} />
      </div>
      <ol className="min-w-0 space-y-2" aria-label="Modelos en orden de ejecución">
        {steps.map((step, index) => {
          const connection = connections.find((item) => item.id === step.connection_id)
          const label = index === 0 ? "Principal" : "Alternativa " + index
          return (
            <li key={step.id} className="min-w-0">
              <Collapsible open={expanded === step.id} onOpenChange={(open) => setExpanded(open ? step.id : "")}
                className="min-w-0 rounded-lg border bg-background/70">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5 p-2 sm:flex-nowrap">
                  <CollapsibleTrigger asChild>
                    <button id={"ai-flow-step-" + step.id} type="button" className="group/step flex min-w-0 flex-[1_1_10rem] items-center gap-2 rounded px-1 py-1 text-left focus-visible:outline-2 focus-visible:outline-ring">
                      <span className={cn("grid size-7 shrink-0 place-items-center rounded-md border text-xs font-semibold",
                        index === 0 ? "border-sky-500/25 bg-sky-500/10 text-sky-800 dark:text-sky-300" : "bg-muted")}>{index + 1}</span>
                      <span className="min-w-0 flex-1 wrap-anywhere">
                        <span className="block text-sm font-semibold">{label}</span>
                        <span className="block text-xs text-muted-foreground">
                          {step.model || "Modelo por elegir"}
                          {connection && " · " + connection.name}
                        </span>
                        {connection && !connection.enabled && (
                          <span className="block text-xs text-amber-800 dark:text-amber-300">Conexión desactivada</span>
                        )}
                      </span>
                      <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]/step:rotate-180 motion-reduce:transition-none" />
                    </button>
                  </CollapsibleTrigger>
                  {editable && (
                    <div className="flex shrink-0 flex-wrap gap-1" role="group" aria-label={"Orden de " + label.toLowerCase()}>
                      <Button type="button" variant="outline" size="icon" className="size-8" disabled={!canEdit || index === 0}
                        title="Subir" aria-label={"Subir " + label.toLowerCase()} onClick={() => move(index, -1)}><ArrowUp aria-hidden="true" /></Button>
                      <Button type="button" variant="outline" size="icon" className="size-8" disabled={!canEdit || index === steps.length - 1}
                        title="Bajar" aria-label={"Bajar " + label.toLowerCase()} onClick={() => move(index, 1)}><ArrowDown aria-hidden="true" /></Button>
                      <Button type="button" variant="outline" size="icon" className="size-8 text-destructive" disabled={!canEdit || steps.length === 1}
                        title="Quitar paso" aria-label={"Quitar " + label.toLowerCase()} onClick={() => {
                          setSteps((previous) => previous.filter((value) => value.id !== step.id))
                          if (expanded === step.id) setExpanded("")
                          addButton.current?.focus()
                        }}><Trash2 aria-hidden="true" /></Button>
                    </div>
                  )}
                </div>
                <CollapsibleContent>
                  <StepSettings step={step} connections={connections} editable={canEdit} now={now}
                    onChange={(patch) => setSteps((previous) => previous.map((value) => value.id === step.id ? { ...value, ...patch } : value))} />
                </CollapsibleContent>
              </Collapsible>
            </li>
          )
        })}
      </ol>
      {connections.length === 0 && (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-950 dark:text-amber-200">
          Añade una cuenta en Conexiones para elegir un modelo.
        </p>
      )}
      {error && <p role="alert" className="text-sm text-destructive wrap-anywhere">{error}</p>}
      {editable && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
          <Button ref={addButton} type="button" variant="outline" className="h-9 px-3" disabled={!canEdit || !connections.length}
            onClick={() => {
              const added = defaultStep(connections, steps.length, now)
              setSteps((previous) => [...previous, added])
              setExpanded(added.id)
              window.requestAnimationFrame(() => document.getElementById("ai-flow-step-" + added.id)?.focus())
            }}><Plus aria-hidden="true" />Añadir alternativa</Button>
          <div className="flex flex-wrap items-center gap-2">
            {incomplete && <span className="text-xs text-muted-foreground">Completa el nombre, la conexión y el modelo de cada paso.</span>}
            <Button type="submit" className="h-9 px-3" disabled={!canEdit || !name.trim() || incomplete || (!!flow.id && !dirty)}>
              {saving ? "Publicando…" : flow.id ? "Publicar cambios" : "Publicar flujo"}
            </Button>
          </div>
        </div>
      )}
    </form>
  )
}

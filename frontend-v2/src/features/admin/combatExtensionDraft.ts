/** 战斗扩展可视化编辑器的 draft 模型与模板块互转（Issue 212 phase 2）。 */

export type FormulaDraft =
  | { type: 'dice'; formula: string }
  | { type: 'constant'; value: number }
  | { type: 'attribute'; id: string }
  | { type: 'combine'; op: 'add' | 'multiply'; left: FormulaDraft; right: FormulaDraft }
  | { type: 'json'; raw: string }

type RawFields = Record<string, unknown>

export interface CostDraft { resource: string; amount: FormulaDraft; raw?: RawFields }
export interface EffectDraft {
  kind: string
  resource?: string
  amount?: FormulaDraft
  duration?: number
  damage_type?: string
  raw?: RawFields
}
export interface ActionDraft {
  id: string
  kind: string
  name: string
  costs: CostDraft[]
  effects: EffectDraft[]
  consume_item?: { item: string; qty: number }
  raw?: RawFields
}
export interface ResourceDraft {
  id: string
  source: 'hp' | 'special_stat' | 'combat_state'
  stat?: string
  maximum?: number | null
  costable?: boolean
  damage_priority?: string | null
  damage_types?: string[] | null
  raw?: RawFields
}
export interface SchedulerDraft {
  kind: string
  threshold: number
  overflow: string
  consume: string
  gauge: string
  speed: string
  raw?: RawFields
}
export interface CombatDraft {
  scheduler: SchedulerDraft | null
  resources: ResourceDraft[]
  actions: ActionDraft[]
  raw?: RawFields
}

export function formulaToAst(draft: FormulaDraft): unknown {
  switch (draft.type) {
    case 'dice':
      return { op: 'dice', formula: draft.formula }
    case 'constant':
      return { op: 'constant', value: draft.value }
    case 'attribute':
      return { op: 'attribute', id: draft.id }
    case 'combine':
      return { op: draft.op, args: [formulaToAst(draft.left), formulaToAst(draft.right)] }
    case 'json':
      return JSON.parse(draft.raw)
  }
}

export function astToFormula(ast: unknown): FormulaDraft {
  const node = ast as { op?: string; formula?: string; value?: unknown; id?: string; args?: unknown[] }
  if (!node || typeof node !== 'object' || !node.op) return { type: 'constant', value: 0 }
  if (node.op === 'dice' && typeof node.formula === 'string') return { type: 'dice', formula: node.formula }
  if (node.op === 'constant' && typeof node.value === 'number') return { type: 'constant', value: node.value }
  if (node.op === 'attribute' && typeof node.id === 'string') return { type: 'attribute', id: node.id }
  if ((node.op === 'add' || node.op === 'multiply') && Array.isArray(node.args) && node.args.length === 2) {
    return { type: 'combine', op: node.op, left: astToFormula(node.args[0]), right: astToFormula(node.args[1]) }
  }
  return { type: 'json', raw: JSON.stringify(node) }
}

export function draftFromBlock(block: unknown): CombatDraft | null {
  if (!block || typeof block !== 'object') return null
  const raw = block as {
    scheduler?: { kind?: string; threshold?: number; overflow?: string; consume?: string; gauge?: string; speed?: string } | null
    resources?: Array<{ id?: string; source?: string; stat?: string; maximum?: number; costable?: boolean; damage_priority?: string; damage_types?: string[] }>
    actions?: Array<{ id?: string; kind?: string; name?: string; costs?: Array<{ resource?: string; amount?: unknown }>; effects?: Array<{ kind?: string; resource?: string; amount?: unknown; duration?: number; damage_type?: string }>; consume_item?: { item: string; qty: number } }>
  }
  return {
    raw: { ...(block as RawFields) },
    scheduler: raw.scheduler?.kind
      ? {
          kind: raw.scheduler.kind,
          threshold: raw.scheduler.threshold ?? 100,
          overflow: raw.scheduler.overflow ?? 'carry',
          consume: raw.scheduler.consume ?? 'reset',
          gauge: raw.scheduler.gauge ?? 'action_gauge',
          speed: raw.scheduler.speed ?? 'action_speed',
          raw: { ...raw.scheduler },
        }
      : null,
    resources: (raw.resources || []).map((item): ResourceDraft => ({
      id: item.id || '',
      source: (item.source as ResourceDraft['source']) || 'special_stat',
      stat: item.stat,
      maximum: item.maximum ?? null,
      costable: item.costable ?? true,
      damage_priority: item.damage_priority ?? null,
      damage_types: item.damage_types ?? null,
      raw: { ...item },
    })),
    actions: (raw.actions || []).map((action): ActionDraft => ({
      id: action.id || '',
      kind: action.kind || 'ability',
      name: action.name || action.id || '',
      costs: (action.costs || []).map((cost): CostDraft => ({
        resource: cost.resource || '',
        amount: astToFormula(cost.amount),
        raw: { ...cost },
      })),
      effects: (action.effects || []).map((effect): EffectDraft => ({
        kind: effect.kind || 'damage',
        resource: effect.resource,
        amount: effect.amount === undefined || effect.amount === null
          ? { type: 'constant', value: 0 }
          : astToFormula(effect.amount),
        duration: effect.duration,
        damage_type: effect.damage_type,
        raw: { ...effect },
      })),
      consume_item: action.consume_item,
      raw: { ...action },
    })),
  }
}

export function blockFromDraft(draft: CombatDraft): Record<string, unknown> {
  const block: Record<string, unknown> = {
    ...(draft.raw || {}),
    resources: draft.resources.map((item): Record<string, unknown> => {
      const entry: Record<string, unknown> = {
        ...(item.raw || {}),
        id: item.id,
        source: item.source,
      }
      if (item.source === 'special_stat' && item.stat) entry.stat = item.stat
      else delete entry.stat
      if (item.maximum != null) entry.maximum = item.maximum
      else delete entry.maximum
      if (item.source === 'special_stat') entry.costable = item.costable !== false
      else if (item.costable === false) entry.costable = false
      else delete entry.costable
      if (item.source === 'combat_state' && item.damage_priority) {
        entry.damage_priority = item.damage_priority
      } else delete entry.damage_priority
      if (item.damage_types?.length) entry.damage_types = item.damage_types
      else delete entry.damage_types
      return entry
    }),
    actions: draft.actions.map((action): Record<string, unknown> => {
      const entry: Record<string, unknown> = {
        ...(action.raw || {}),
        id: action.id,
        kind: action.kind,
        name: action.name,
        costs: action.costs.map((cost): Record<string, unknown> => ({
          ...(cost.raw || {}),
          resource: cost.resource,
          amount: formulaToAst(cost.amount),
        })),
        effects: action.effects.map((effect): Record<string, unknown> => {
        const entry: Record<string, unknown> = {
          ...(effect.raw || {}),
          kind: effect.kind,
          amount: formulaToAst(effect.amount ?? { type: 'constant', value: 0 }),
        }
        if (effect.kind === 'resource_change' || effect.kind === 'modify_stat') {
          if (effect.resource) entry.resource = effect.resource
          else delete entry.resource
        } else if (effect.kind === 'damage') delete entry.resource
        if (effect.kind === 'damage') {
          if (effect.damage_type) entry.damage_type = effect.damage_type
          else delete entry.damage_type
        } else if (effect.kind === 'resource_change' || effect.kind === 'modify_stat') {
          delete entry.damage_type
        }
        if (effect.kind === 'modify_stat') {
          if (effect.duration != null) entry.duration = effect.duration
          else delete entry.duration
        } else if (effect.kind === 'damage' || effect.kind === 'resource_change') {
          delete entry.duration
        }
        return entry
        }),
      }
      if (action.consume_item) entry.consume_item = action.consume_item
      else delete entry.consume_item
      return entry
    }),
  }
  if (draft.scheduler) {
    const { raw, ...fields } = draft.scheduler
    block.scheduler = { ...(raw || {}), ...fields }
  } else {
    delete block.scheduler
  }
  return block
}

export function emptyDraft(): CombatDraft {
  return { scheduler: null, resources: [{ id: 'hp', source: 'hp' }], actions: [] }
}

/** 战斗扩展可视化编辑器的 draft 模型与模板块互转（Issue 212 phase 2）。 */

export type FormulaDraft =
  | { type: 'dice'; formula: string }
  | { type: 'constant'; value: number }
  | { type: 'attribute'; id: string }
  | { type: 'combine'; op: 'add' | 'multiply'; left: FormulaDraft; right: FormulaDraft }
  | { type: 'json'; raw: string }

export interface CostDraft { resource: string; amount: FormulaDraft }
export interface EffectDraft {
  kind: string
  resource?: string
  amount?: FormulaDraft
  damage_type?: string
}
export interface ActionDraft {
  id: string
  kind: string
  name: string
  costs: CostDraft[]
  effects: EffectDraft[]
}
export interface ResourceDraft {
  id: string
  source: 'hp' | 'special_stat' | 'combat_state'
  stat?: string
  maximum?: number | null
  costable?: boolean
  damage_priority?: string | null
  damage_types?: string[] | null
}
export interface CombatDraft {
  scheduler: { kind: string; threshold: number; overflow: string; consume: string; gauge: string; speed: string } | null
  resources: ResourceDraft[]
  actions: ActionDraft[]
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
    actions?: Array<{ id?: string; kind?: string; name?: string; costs?: Array<{ resource?: string; amount?: unknown }>; effects?: Array<{ kind?: string; resource?: string; amount?: unknown; damage_type?: string }> }>
  }
  return {
    scheduler: raw.scheduler?.kind
      ? {
          kind: raw.scheduler.kind,
          threshold: raw.scheduler.threshold ?? 100,
          overflow: raw.scheduler.overflow ?? 'carry',
          consume: raw.scheduler.consume ?? 'reset',
          gauge: raw.scheduler.gauge ?? 'action_gauge',
          speed: raw.scheduler.speed ?? 'action_speed',
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
    })),
    actions: (raw.actions || []).map((action): ActionDraft => ({
      id: action.id || '',
      kind: action.kind || 'ability',
      name: action.name || action.id || '',
      costs: (action.costs || []).map((cost): CostDraft => ({
        resource: cost.resource || '',
        amount: astToFormula(cost.amount),
      })),
      effects: (action.effects || []).map((effect): EffectDraft => ({
        kind: effect.kind || 'damage',
        resource: effect.resource,
        amount: effect.amount === undefined || effect.amount === null
          ? { type: 'constant', value: 0 }
          : astToFormula(effect.amount),
        damage_type: effect.damage_type,
      })),
    })),
  }
}

export function blockFromDraft(draft: CombatDraft): Record<string, unknown> {
  return {
    ...(draft.scheduler ? { scheduler: draft.scheduler } : {}),
    resources: draft.resources.map((item): Record<string, unknown> => {
      const entry: Record<string, unknown> = { id: item.id, source: item.source }
      if (item.source === 'special_stat' && item.stat) entry.stat = item.stat
      if (item.maximum != null) entry.maximum = item.maximum
      if (item.source === 'special_stat') entry.costable = item.costable !== false
      if (item.damage_priority) entry.damage_priority = item.damage_priority
      if (item.damage_types?.length) entry.damage_types = item.damage_types
      return entry
    }),
    actions: draft.actions.map((action): Record<string, unknown> => ({
      id: action.id,
      kind: action.kind,
      name: action.name,
      costs: action.costs.map((cost): Record<string, unknown> => ({ resource: cost.resource, amount: formulaToAst(cost.amount) })),
      effects: action.effects.map((effect): Record<string, unknown> => {
        const entry: Record<string, unknown> = {
          kind: effect.kind,
          amount: formulaToAst(effect.amount ?? { type: 'constant', value: 0 }),
        }
        if (effect.kind === 'resource_change' && effect.resource) entry.resource = effect.resource
        if (effect.damage_type) entry.damage_type = effect.damage_type
        return entry
      }),
    })),
  }
}

export function emptyDraft(): CombatDraft {
  return { scheduler: null, resources: [{ id: 'hp', source: 'hp' }], actions: [] }
}

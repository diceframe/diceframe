import type { PeerGameOperation } from '@/peer/game/protocol'
import type { MultiPeerConnectionSession } from '@/peer/session/MultiPeerConnectionSession'

export type PeerLocalApiExecutor = (
  path: string,
  init?: RequestInit,
) => Promise<unknown>

interface PeerApiResult<T> {
  handled: boolean
  value?: T
}

const MUTATING_OPERATIONS = new Set<PeerGameOperation>([
  'player.create',
  'player.rebind',
  'player.away',
  'action.submit',
  'ruleset.intent',
  'ruleset.decision',
  'luck.resolve',
  'payment.resolve',
  'character.update',
  'character.profile',
  'character.rest',
  'character.advancement.apply',
  'roll.resolve',
])
const MAX_REQUESTS_PER_MINUTE = 120
const MAX_IN_FLIGHT_PER_PEER = 8

const LOBBY_DETAIL_FIELDS = [
  'game_key', 'player_access_open', 'player_count', 'max_players',
  'has_room_password', 'world_name', 'scene', 'rule_id', 'solo_mode',
] as const

const LOBBY_MULTIPLAYER_FIELDS = [
  'state', 'round_number', 'solo_mode', 'player_count', 'max_players',
  'ready_count', 'alive_count', 'active_count', 'away_count', 'ai_count',
  'unclaimed_count', 'can_accept_actions', 'can_advance', 'action_count',
  'pending_action_count', 'player_access_open',
] as const

function lobbyMultiplayer(value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (!value || typeof value !== 'object' || Array.isArray(value)) return out
  const current = value as Record<string, unknown>
  for (const key of LOBBY_MULTIPLAYER_FIELDS) if (key in current) out[key] = current[key]
  return out
}

function lobbyDetail(current: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of LOBBY_DETAIL_FIELDS) if (key in current) out[key] = current[key]
  if ('multiplayer' in current) out.multiplayer = lobbyMultiplayer(current.multiplayer)
  return out
}

/**
 * 对端 payload 字段白名单：只放行各操作实际需要的字段，其余全部剥离。
 * 对端是其他玩家的浏览器，任意 JSON 直通本地 API 会造成越权注入
 * （如 player.create 塞 user_id 冒充他人、character.update 塞 owner 标记）。
 */
const OPERATION_FIELD_WHITELIST: Record<PeerGameOperation, readonly string[]> = {
  'game.detail': [],
  'game.characters': [],
  'game.log': ['page', 'per_page'],
  'game.private_log': [],
  'game.table_talk': [],
  'game.map': [],
  'game.player_context': [],
  'roll.requests': [],
  'roll.resolve': ['run_id', 'request_id'],
  'player.create': [
    'character_name', 'race', 'class', 'background', 'hp',
    'attributes', 'skills', 'identity', 'portrait', 'ruleset_character',
  ],
  'player.rebind': ['user_id'],
  'player.away': ['away'],
  'action.submit': ['text', 'selected_attribute', 'selected_skill', 'target_text'],
  'kp.question': ['question', 'visibility'],
  'ruleset.actions': [],
  'ruleset.intent': [
    'intent_id', 'type', 'expected_version', 'actor_id', 'target_id', 'target_ids',
    'weapon_ref', 'attack_id', 'spell_ref', 'slot_level', 'damage_type', 'distance',
    'decision_id', 'option', 'response', 'comment', 'choice_id', 'enabled', 'text',
    'agreement', 'kind', 'visibility', 'entity_id', 'title', 'summary',
    'proposal_id', 'mode', 'adventure_id',
    'encounter_preset_id', 'encounter_instance_id', 'enemies',
    // 职业特性提供的战斗能力只提交 capability id 与目标；成本与结算仍由
    // 服务端 capability 声明决定，P2P 与 Web 走同一条权威链。
    'capability_id',
  ],
  'ruleset.decision': [
    'intent_id', 'type', 'expected_version', 'decision_id', 'option',
    'response', 'comment', 'choice_id', 'enabled',
  ],
  'luck.resolve': ['check_id', 'spend'],
  'payment.resolve': ['payment_id', 'accepted'],
  'character.update': [
    'character_name', 'race', 'class', 'background', 'hp',
    'attributes', 'skills', 'identity', 'portrait',
  ],
  'character.profile': ['character_name', 'portrait', 'profile'],
  'character.rest': [
    'rest', 'hit_dice', 'confirm_elapsed_time', 'expected_revision', 'operation_id',
  ],
  'character.advancement.preview': ['choices'],
  'character.advancement.apply': ['choices', 'expected_revision', 'operation_id'],
}

function sanitizePayload(
  operation: PeerGameOperation,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const allowed = OPERATION_FIELD_WHITELIST[operation] ?? []
  if (!allowed.length) return {}
  const cleaned: Record<string, unknown> = {}
  for (const field of allowed) {
    if (field in payload) cleaned[field] = payload[field]
  }
  // Session 0 的完整 agreement 只属于 propose；respond 即使夹带该字段也不能
  // 借 P2P 通道改写约定。最终 GM/玩家权限仍由规则运行时验证。
  if (operation === 'ruleset.intent' && cleaned.type !== 'session_zero.propose') {
    delete cleaned.agreement
  }
  return cleaned
}

export class PeerHostGameBridge {
  private readonly actorByPeer = new Map<string, string>()
  private readonly assignedActorByPeer: Map<string, string>
  private readonly requestTimes = new Map<string, number[]>()
  private readonly inFlight = new Map<string, number>()
  private readonly waiters = new Map<string, Array<() => void>>()

  constructor(
    readonly gameKey: string,
    private readonly executor: PeerLocalApiExecutor,
    private readonly onMutation: () => void,
    assignedActorByPeer: Readonly<Record<string, string>> = {},
    boundActorByPeer: Readonly<Record<string, string>> = {},
    private readonly onActorBinding: (peerId: string, actorId: string) => void = () => undefined,
  ) {
    if (!gameKey || gameKey.length > 512) throw new Error('invalid_game_key')
    this.assignedActorByPeer = new Map(
      Object.entries(assignedActorByPeer).filter(([, actorId]) => Boolean(actorId)),
    )
    for (const [peerId, actorId] of Object.entries(boundActorByPeer)) {
      if (actorId) this.actorByPeer.set(peerId, actorId)
    }
  }

  async handle(
    peerId: string,
    operation: PeerGameOperation,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    await this.admit(peerId)
    try {
      const sanitized = sanitizePayload(operation, payload)
      const result = await this.dispatch(peerId, operation, sanitized)
      if (
        MUTATING_OPERATIONS.has(operation)
        || (operation === 'kp.question' && sanitized.visibility === 'party')
      ) this.onMutation()
      return result
    } finally {
      this.release(peerId)
    }
  }

  /** 每分钟总量限流直接拒绝；并发超限排队等空位（游玩页挂载会并发十几个读请求，不能抛错）。 */
  private admit(peerId: string): Promise<void> {
    const now = Date.now()
    const recent = (this.requestTimes.get(peerId) ?? [])
      .filter(timestamp => now - timestamp < 60_000)
    if (recent.length >= MAX_REQUESTS_PER_MINUTE) {
      return Promise.reject(new Error('peer_game_rate_limited'))
    }
    recent.push(now)
    this.requestTimes.set(peerId, recent)
    const active = this.inFlight.get(peerId) ?? 0
    if (active < MAX_IN_FLIGHT_PER_PEER) {
      this.inFlight.set(peerId, active + 1)
      return Promise.resolve()
    }
    return new Promise(resolve => {
      const list = this.waiters.get(peerId) ?? []
      list.push(resolve)
      this.waiters.set(peerId, list)
    })
  }

  /** 释放一个并发位：优先 handed off 给排队者（计数不变），否则递减。 */
  private release(peerId: string): void {
    const next = (this.waiters.get(peerId) ?? []).shift()
    if (next) {
      next()
      return
    }
    this.inFlight.set(peerId, Math.max(0, (this.inFlight.get(peerId) ?? 1) - 1))
  }

  private async dispatch(
    peerId: string,
    operation: PeerGameOperation,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const game = encodeURIComponent(this.gameKey)
    const current = await this.execute(`/games/${game}`)
    if (!current.game_key) throw new Error('game_not_found')
    if (current.player_access_open === false) throw new Error('player_access_closed')
    if (operation === 'player.create') {
      if (this.assignedActorByPeer.has(peerId)) throw new Error('player_invite_has_identity')
      const existingActor = this.actorByPeer.get(peerId)
      if (existingActor) return { ok: true, user_id: existingActor, reused: true }
      const playerCount = Number(current.player_count || 0)
      const maxPlayers = Number(current.max_players || 6)
      if (playerCount >= maxPlayers) throw new Error('game_room_full')
      const character = { ...payload, user_id: undefined, join_as_new: true }
      const result = await this.execute(`/games/${game}/players`, {
        method: 'POST',
        body: JSON.stringify(character),
      })
      const actorId = typeof result.user_id === 'string' ? result.user_id : ''
      if (!actorId) throw new Error(String(result.error || 'player_creation_failed'))
      this.bindActor(peerId, actorId)
      return result
    }
    if (operation === 'player.rebind') {
      // 邀请中指定的已有角色，或该 peer 此前创建并已持久化的角色，才允许恢复。
      // 不能让持有普通“新玩家”邀请码的客户端枚举并冒充任意已有角色。
      const claimedActor = requiredIdentifier(payload.user_id, 'user_id')
      const allowedActor = this.actorByPeer.get(peerId) || this.assignedActorByPeer.get(peerId) || ''
      if (!allowedActor || claimedActor !== allowedActor) {
        throw new Error('player_identity_not_assigned')
      }
      const claimedBy = [...this.actorByPeer.entries()]
        .find(([, actor]) => actor === claimedActor)
      if (claimedBy && claimedBy[0] !== peerId) throw new Error('player_identity_taken')
      const characters = await this.execute(
        `/games/${game}/characters?share=1&user=${encodeURIComponent(claimedActor)}`,
      )
      const players = Array.isArray(characters.players) ? characters.players : []
      const exists = players.some(player => (
        player && typeof player === 'object' && player.user_id === claimedActor
      ))
      if (!exists) throw new Error('player_identity_unknown')
      this.bindActor(peerId, claimedActor)
      return { ok: true, user_id: claimedActor, rebound: true }
    }

    const actorId = this.actorByPeer.get(peerId) || ''
    const actorQuery = actorId
      ? `user=${encodeURIComponent(actorId)}&share=1&delegate=1`
      : 'share=1'
    const read = (suffix: string) => this.execute(
      `/games/${game}${suffix}${suffix.includes('?') ? '&' : '?'}${actorQuery}`,
    )
    const write = (suffix: string, body: Record<string, unknown>) => (
      this.execute(
        `/games/${game}${suffix}${suffix.includes('?') ? '&' : '?'}${actorQuery}`,
        { method: 'POST', body: JSON.stringify(body) },
      )
    )

    if (operation === 'game.detail') {
      const detail = actorId ? await read('') : lobbyDetail(current)
      return { ...detail, has_room_password: false, peer_transport: true }
    }
    if (operation === 'game.player_context') {
      return { ok: true, preview: false, delegate: false, user_id: actorId }
    }
    if (!actorId) {
      // JoinView loads characters before binding and supports empty lobby data.
      if (operation === 'game.characters') return { players: [], npcs: [] }
      throw new Error('player_identity_required')
    }
    if (operation === 'game.characters') return read('/characters')
    if (operation === 'roll.requests') return read('/roll-requests')
    if (operation === 'game.log') {
      const page = boundedInteger(payload.page, 1, 10_000, 1)
      const perPage = boundedInteger(payload.per_page, 1, 100, 50)
      return read(`/log?page=${page}&per_page=${perPage}`)
    }
    if (operation === 'game.private_log') return read('/private-log')
    if (operation === 'game.table_talk') return read('/table-talk')
    if (operation === 'game.map') return read('/map')
    if (operation === 'player.away') {
      return write(`/players/${encodeURIComponent(actorId)}/away`, {
        away: Boolean(payload.away),
      })
    }
    if (operation === 'action.submit') return write('/action', payload)
    if (operation === 'kp.question') return write('/kp-question', payload)
    if (operation === 'ruleset.actions') return read('/available-actions')
    if (operation === 'ruleset.intent') return write('/intents', payload)
    if (operation === 'ruleset.decision') {
      const decisionId = requiredIdentifier(payload.decision_id, 'decision_id')
      return write(`/decisions/${encodeURIComponent(decisionId)}`, payload)
    }
    if (operation === 'luck.resolve') {
      const checkId = requiredIdentifier(payload.check_id, 'check_id')
      return write(`/checks/${encodeURIComponent(checkId)}/luck`, {
        spend: Boolean(payload.spend),
      })
    }
    if (operation === 'payment.resolve') {
      const paymentId = requiredIdentifier(payload.payment_id, 'payment_id')
      return write(`/payments/${encodeURIComponent(paymentId)}`, {
        accepted: Boolean(payload.accepted),
      })
    }
    if (operation === 'character.update') {
      return this.execute(
        `/games/${game}/character/${encodeURIComponent(actorId)}?${actorQuery}`,
        { method: 'PUT', body: JSON.stringify(payload) },
      )
    }
    if (operation === 'character.profile') {
      return this.execute(
        `/games/${game}/character/${encodeURIComponent(actorId)}/profile?${actorQuery}`,
        { method: 'PATCH', body: JSON.stringify(payload) },
      )
    }
    if (operation === 'character.rest') {
      return write(`/character/${encodeURIComponent(actorId)}/rest`, payload)
    }
    if (operation === 'character.advancement.preview') {
      return write(`/character/${encodeURIComponent(actorId)}/advancement/preview`, payload)
    }
    if (operation === 'character.advancement.apply') {
      return write(`/character/${encodeURIComponent(actorId)}/advancement/apply`, payload)
    }
    if (operation === 'roll.resolve') {
      const requestId = requiredIdentifier(payload.request_id, 'request_id')
      return write(`/roll-requests/${encodeURIComponent(requestId)}/roll`, {
        run_id: payload.run_id,
        target_uid: actorId,
      })
    }
    throw new Error('peer_game_operation_not_supported')
  }

  private async execute(path: string, init?: RequestInit): Promise<Record<string, unknown>> {
    const value = await this.executor(path, init)
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('invalid_local_game_response')
    }
    return value as Record<string, unknown>
  }

  private bindActor(peerId: string, actorId: string): void {
    this.actorByPeer.set(peerId, actorId)
    this.onActorBinding(peerId, actorId)
  }
}

export class PeerRemoteGameClient {
  private listeners = new Set<() => void>()
  private actorId = ''

  constructor(
    private readonly session: MultiPeerConnectionSession,
    private readonly hostPeerId: string,
    readonly gameKey: string,
    assignedActorId = '',
  ) {
    // 指定角色的邀请码优先于本机旧缓存；普通邀请码刷新时恢复此前创建的角色。
    this.actorId = assignedActorId || readStoredPeerActor(gameKey)
  }

  get userId(): string {
    return this.actorId
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  notifyStateChanged(): void {
    for (const listener of this.listeners) listener()
  }

  handlesGamePath(path: string): boolean {
    return this.parseGamePath(path) !== null
  }

  async tryApi<T>(path: string, init: RequestInit = {}): Promise<PeerApiResult<T>> {
    const parsed = this.parseGamePath(path)
    if (!parsed) return { handled: false }
    const method = String(init.method || 'GET').toUpperCase()
    const body = parseRequestBody(init.body)
    let operation: PeerGameOperation
    let payload: Record<string, unknown> = body

    if (method === 'GET' && parsed.tail === '') operation = 'game.detail'
    else if (method === 'GET' && parsed.tail === '/characters') operation = 'game.characters'
    else if (method === 'GET' && parsed.tail === '/character-cards') {
      return { handled: true, value: { cards: [] } as T }
    } else if (method === 'GET' && parsed.tail === '/log') {
      operation = 'game.log'
      payload = Object.fromEntries(parsed.query)
    } else if (method === 'GET' && parsed.tail === '/private-log') operation = 'game.private_log'
    else if (method === 'GET' && parsed.tail === '/table-talk') operation = 'game.table_talk'
    else if (method === 'GET' && parsed.tail === '/map') operation = 'game.map'
    else if (method === 'GET' && parsed.tail === '/player-context') operation = 'game.player_context'
    else if (method === 'GET' && parsed.tail === '/roll-requests') operation = 'roll.requests'
    else if (method === 'POST' && parsed.tail === '/players') operation = 'player.create'
    else if (method === 'POST' && parsed.tail === '/action') operation = 'action.submit'
    else if (method === 'POST' && parsed.tail === '/kp-question') operation = 'kp.question'
    else if (method === 'GET' && parsed.tail === '/available-actions') operation = 'ruleset.actions'
    else if (method === 'POST' && parsed.tail === '/intents') operation = 'ruleset.intent'
    else {
      const luck = /^\/checks\/([^/]+)\/luck$/u.exec(parsed.tail)
      const payment = /^\/payments\/([^/]+)$/u.exec(parsed.tail)
      const away = /^\/players\/([^/]+)\/away$/u.exec(parsed.tail)
      const character = /^\/character\/([^/]+)$/u.exec(parsed.tail)
      const decision = /^\/decisions\/([^/]+)$/u.exec(parsed.tail)
      const profile = /^\/character\/([^/]+)\/profile$/u.exec(parsed.tail)
      const rest = /^\/character\/([^/]+)\/rest$/u.exec(parsed.tail)
      const advancement = /^\/character\/([^/]+)\/advancement\/(preview|apply)$/u.exec(parsed.tail)
      const roll = /^\/roll-requests\/([^/]+)\/roll$/u.exec(parsed.tail)
      if (method === 'POST' && luck) {
        operation = 'luck.resolve'
        payload = { ...body, check_id: decodeURIComponent(luck[1]) }
      } else if (method === 'POST' && payment) {
        operation = 'payment.resolve'
        payload = { ...body, payment_id: decodeURIComponent(payment[1]) }
      } else if (method === 'POST' && away) {
        operation = 'player.away'
      } else if (method === 'PUT' && character) {
        operation = 'character.update'
      } else if (method === 'POST' && decision) {
        operation = 'ruleset.decision'
        payload = { ...body, decision_id: decodeURIComponent(decision[1]) }
      } else if (method === 'PATCH' && profile) {
        operation = 'character.profile'
      } else if (method === 'POST' && rest) {
        operation = 'character.rest'
      } else if (method === 'POST' && advancement?.[2] === 'preview') {
        operation = 'character.advancement.preview'
      } else if (method === 'POST' && advancement?.[2] === 'apply') {
        operation = 'character.advancement.apply'
      } else if (method === 'POST' && roll) {
        operation = 'roll.resolve'
        payload = { ...body, request_id: decodeURIComponent(roll[1]) }
      } else if (method === 'POST' && parsed.tail === '/sse-ticket') {
        return { handled: true, value: { ticket: 'peer-data-channel' } as T }
      } else {
        throw new Error('peer_game_operation_not_supported')
      }
    }

    const value = await this.session.requestGame(this.hostPeerId, operation, payload)
    if (operation === 'player.create' && typeof value.user_id === 'string') {
      this.actorId = value.user_id
      writeStoredPeerActor(this.gameKey, this.actorId)
    }
    return { handled: true, value: value as T }
  }

  /** 刷新/重连后向 host 恢复身份；失败（角色已删）则清除存档返回 false。 */
  async rebindIdentity(): Promise<boolean> {
    if (!this.actorId) return false
    try {
      const value = await this.session.requestGame(
        this.hostPeerId,
        'player.rebind',
        { user_id: this.actorId },
      )
      if (typeof value.user_id === 'string') {
        this.actorId = value.user_id
        writeStoredPeerActor(this.gameKey, this.actorId)
        return true
      }
      return false
    } catch {
      return false
    }
  }

  private parseGamePath(path: string): { tail: string; query: URLSearchParams } | null {
    const [pathname, rawQuery = ''] = path.split('?', 2)
    const prefix = `/games/${encodeURIComponent(this.gameKey)}`
    if (pathname !== prefix && !pathname.startsWith(`${prefix}/`)) return null
    return { tail: pathname.slice(prefix.length), query: new URLSearchParams(rawQuery) }
  }
}

let activeRemoteClient: PeerRemoteGameClient | null = null

export function setActivePeerGameClient(client: PeerRemoteGameClient | null): void {
  activeRemoteClient = client
}

export function activePeerGameClient(): PeerRemoteGameClient | null {
  return activeRemoteClient
}

function parseRequestBody(body: BodyInit | null | undefined): Record<string, unknown> {
  if (body === undefined || body === null || body === '') return {}
  if (typeof body !== 'string') throw new Error('peer_game_json_body_required')
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    throw new Error('peer_game_json_body_required')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('peer_game_json_body_required')
  }
  return parsed as Record<string, unknown>
}

function boundedInteger(
  value: unknown,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : fallback
}

function requiredIdentifier(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value || value.length > 128) {
    throw new Error(`invalid_${name}`)
  }
  return value
}

const PEER_ACTOR_KEY_PREFIX = 'diceframe_peer_actor_'

function readStoredPeerActor(gameKey: string): string {
  try {
    return localStorage.getItem(PEER_ACTOR_KEY_PREFIX + gameKey) || ''
  } catch {
    return ''
  }
}

function writeStoredPeerActor(gameKey: string, actorId: string): void {
  try {
    localStorage.setItem(PEER_ACTOR_KEY_PREFIX + gameKey, actorId)
  } catch {
    // 存储不可用时身份只在内存，刷新后降级为重建角色。
  }
}

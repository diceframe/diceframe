const APPLICATION_VERSION = 1
export const MAX_APPLICATION_BYTES = 2 * 1024 * 1024

export type PeerGameOperation =
  | 'game.detail'
  | 'game.characters'
  | 'game.log'
  | 'game.private_log'
  | 'game.table_talk'
  | 'game.map'
  | 'game.player_context'
  | 'roll.requests'
  | 'check_modes.read'
  | 'check_modes.request'
  | 'recovery.read'
  | 'recovery.request'
  | 'recovery.claim'
  | 'roll.resolve'
  | 'player.create'
  | 'player.rebind'
  | 'player.away'
  | 'action.submit'
  | 'kp.question'
  | 'ruleset.actions'
  | 'ruleset.intent'
  | 'ruleset.decision'
  | 'luck.resolve'
  | 'check.reveal'
  | 'push.resolve'
  | 'payment.resolve'
  | 'character.update'
  | 'character.profile'
  | 'character.rest'
  | 'character.spells'
  | 'character.advancement.preview'
  | 'character.advancement.apply'

const GAME_OPERATIONS = new Set<PeerGameOperation>([
  'game.detail',
  'game.characters',
  'game.log',
  'game.private_log',
  'game.table_talk',
  'game.map',
  'game.player_context',
  'roll.requests',
  'check_modes.read',
  'check_modes.request',
  'recovery.read',
  'recovery.request',
  'recovery.claim',
  'roll.resolve',
  'player.create',
  'player.rebind',
  'player.away',
  'action.submit',
  'kp.question',
  'ruleset.actions',
  'ruleset.intent',
  'ruleset.decision',
  'luck.resolve',
  'check.reveal',
  'push.resolve',
  'payment.resolve',
  'character.update',
  'character.profile',
  'character.rest',
  'character.spells',
  'character.advancement.preview',
  'character.advancement.apply',
])

export type PeerApplicationMessage =
  | { version: 1; type: 'session.ping'; id: string; sent_at: number }
  | { version: 1; type: 'session.pong'; id: string; sent_at: number }
  | {
    version: 1
    type: 'game.request'
    id: string
    operation: PeerGameOperation
    payload: Record<string, unknown>
  }
  | {
    version: 1
    type: 'game.response'
    id: string
    request_id: string
    ok: boolean
    payload?: Record<string, unknown>
    error?: string
  }
  | { version: 1; type: 'game.event'; id: string; event: 'state.changed' }

let fallbackSequence = 0

function messageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  fallbackSequence += 1
  return `local-${Date.now()}-${fallbackSequence}`
}

export function encodeApplicationMessage(message: PeerApplicationMessage): string {
  return JSON.stringify(message)
}

export function encodeHeartbeat(type: 'session.ping' | 'session.pong', sentAt: number): string {
  return encodeApplicationMessage(createHeartbeat(type, sentAt))
}

export function createHeartbeat(
  type: 'session.ping' | 'session.pong',
  sentAt: number,
): Extract<PeerApplicationMessage, { type: 'session.ping' | 'session.pong' }> {
  return {
    version: APPLICATION_VERSION,
    type,
    id: messageId(),
    sent_at: sentAt,
  }
}

export function createGameRequest(
  operation: PeerGameOperation,
  payload: Record<string, unknown>,
): Extract<PeerApplicationMessage, { type: 'game.request' }> {
  return {
    version: APPLICATION_VERSION,
    type: 'game.request',
    id: messageId(),
    operation,
    payload,
  }
}

export function createGameResponse(
  requestId: string,
  ok: boolean,
  payload?: Record<string, unknown>,
  error?: string,
): Extract<PeerApplicationMessage, { type: 'game.response' }> {
  return {
    version: APPLICATION_VERSION,
    type: 'game.response',
    id: messageId(),
    request_id: requestId,
    ok,
    ...(payload ? { payload } : {}),
    ...(error ? { error: error.slice(0, 500) } : {}),
  }
}

export function createGameChangedEvent(): Extract<
  PeerApplicationMessage,
  { type: 'game.event' }
> {
  return {
    version: APPLICATION_VERSION,
    type: 'game.event',
    id: messageId(),
    event: 'state.changed',
  }
}

export function parseApplicationMessage(raw: string): PeerApplicationMessage {
  if (new TextEncoder().encode(raw).byteLength > MAX_APPLICATION_BYTES) {
    throw new Error('application_message_too_large')
  }
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    throw new Error('invalid_application_message')
  }
  if (!value || typeof value !== 'object') throw new Error('invalid_application_message')
  const message = value as Record<string, unknown>
  if (message.version !== APPLICATION_VERSION || !isMessageId(message.id)) {
    throw new Error('invalid_application_message')
  }
  if (message.type === 'session.ping' || message.type === 'session.pong') {
    if (typeof message.sent_at !== 'number' || !Number.isFinite(message.sent_at)) {
      throw new Error('invalid_application_message')
    }
    return message as PeerApplicationMessage
  }
  if (message.type === 'game.request') {
    if (
      typeof message.operation !== 'string'
      || !GAME_OPERATIONS.has(message.operation as PeerGameOperation)
      || !isPlainObject(message.payload)
    ) {
      throw new Error('invalid_application_message')
    }
    return message as PeerApplicationMessage
  }
  if (message.type === 'game.response') {
    if (
      typeof message.request_id !== 'string'
      || !isMessageId(message.request_id)
      || typeof message.ok !== 'boolean'
      || (message.payload !== undefined && !isPlainObject(message.payload))
      || (
        message.error !== undefined
        && (typeof message.error !== 'string' || message.error.length > 500)
      )
    ) {
      throw new Error('invalid_application_message')
    }
    return message as PeerApplicationMessage
  }
  if (message.type === 'game.event' && message.event === 'state.changed') {
    return message as PeerApplicationMessage
  }
  throw new Error('unsupported_application_message')
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function isMessageId(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 100
}

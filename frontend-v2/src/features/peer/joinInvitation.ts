import type { MessageKey } from '@/i18n'
import { decodePeerInvite } from './inviteCode'

type InvalidReason = 'link' | 'code' | 'input' | 'missing-game' | 'unsupported-link' | 'expired-code'

export type JoinInvitation =
  | { kind: 'empty' }
  | { kind: 'link'; url: string; origin: string }
  | { kind: 'peer'; code: string }
  | { kind: 'invalid'; reason: InvalidReason }

export const joinInvitationErrorKeys: Record<InvalidReason, MessageKey> = {
  link: 'friendJoinInvalidLink',
  code: 'friendJoinInvalidCode',
  input: 'friendJoinInvalidInput',
  'missing-game': 'friendJoinMissingGame',
  'unsupported-link': 'friendJoinUnsupportedLink',
  'expired-code': 'peerInviteExpired',
}

/** Classify locally; never probe the pasted host or consume a one-time code. */
export function classifyJoinInvitation(value: string): JoinInvitation {
  const input = value.trim()
  if (!input) return { kind: 'empty' }

  if (input.startsWith('DFP2-')) {
    try {
      const invite = decodePeerInvite(input)
      if (Date.parse(invite.expiresAt) <= Date.now()) return { kind: 'invalid', reason: 'expired-code' }
      return { kind: 'peer', code: input }
    } catch {
      return { kind: 'invalid', reason: 'code' }
    }
  }

  if (!/^https?:\/\//iu.test(input)) return { kind: 'invalid', reason: 'input' }
  if (input.length > 8192 || [...input].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) {
    return { kind: 'invalid', reason: 'link' }
  }

  let url: URL
  try {
    url = new URL(input)
  } catch {
    return { kind: 'invalid', reason: 'link' }
  }
  if (url.username || url.password) return { kind: 'invalid', reason: 'link' }

  const [path, query = ''] = url.hash.slice(1).split('?', 2)
  const params = new URLSearchParams(query)
  if ((path !== '/join' && path !== '/play') || params.get('peer') === '1'
    || (path === '/play' && params.get('share') !== '1')) {
    return { kind: 'invalid', reason: 'unsupported-link' }
  }
  if (!params.get('game')?.trim()) return { kind: 'invalid', reason: 'missing-game' }
  if (params.getAll('game').length !== 1 || params.get('game')!.length > 512) {
    return { kind: 'invalid', reason: 'link' }
  }

  // Keep origin, deployment subpath, server and seat parameters on the original
  // invitation. Rebuilding it as a local route could target another game/server.
  return { kind: 'link', url: input, origin: url.origin }
}

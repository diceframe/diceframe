import { describe, expect, it } from 'vitest'
import { classifyJoinInvitation } from '../src/features/peer/joinInvitation'
import { encodePeerInvites } from '../src/features/peer/inviteCode'
import { buildJoinLink } from '../src/utils/shareLink'

function peerCode(expiresAt = '2099-01-01T00:00:00Z') {
  return encodePeerInvites({
    ok: true,
    protocol_version: 2,
    topology: 'host-star',
    room_code: 'FRIENDS1',
    host_peer_id: 'h_abcdefghijk',
    host_token: 'host-credential-must-stay-local',
    invitations: [{ peer_id: 'p_abcdefghijk', token: 'guest-invitation-with-at-least-thirty-two-characters' }],
    expires_at: expiresAt,
    websocket_url: 'wss://hub.example.invalid/rooms/FRIENDS1/ws',
  }, [], 'web|friends|gm', [{ actorId: 'player_nightingale', actorName: '夜莺' }])[0]!.inviteCode
}

describe('joining friends with the invitation they received', () => {
  it('recognizes the actual play invitation without treating it as a broken peer code', () => {
    const link = buildJoinLink('web|friends|gm', 'https://friends.example/game')
    expect(classifyJoinInvitation(`  ${link}  `)).toEqual({
      kind: 'link', url: link, origin: 'https://friends.example',
    })
  })

  it('preserves the remote deployment, backend and seat credential exactly', () => {
    const link = buildJoinLink('web|friends|gm', 'https://friends.example/game', 'seat-credential', 'https://backend.example/api-root')
    expect(classifyJoinInvitation(link)).toMatchObject({ kind: 'link', url: link })
  })

  it.each([
    'http://192.168.1.20:18000/#/join?game=room&share=1',
    'http://[fd00::1]:18000/subpath/#/join?game=room&share=1',
    'https://friends.example/#/play?game=room&share=1&user=player_1',
  ])('recognizes an existing player invitation: %s', link => {
    expect(classifyJoinInvitation(link)).toMatchObject({ kind: 'link', url: link })
  })

  it('recognizes a valid direct code and retains its assigned identity', () => {
    const code = peerCode()
    expect(classifyJoinInvitation(code)).toMatchObject({ kind: 'peer', code })
  })

  it('distinguishes expired direct codes from damaged codes', () => {
    expect(classifyJoinInvitation(peerCode('2000-01-01T00:00:00Z'))).toEqual({ kind: 'invalid', reason: 'expired-code' })
    expect(classifyJoinInvitation('DFP2-broken')).toEqual({ kind: 'invalid', reason: 'code' })
  })

  it.each([
    ['https://friends.example/#/join?share=1', 'missing-game'],
    ['https://friends.example/#/join?game=&share=1', 'missing-game'],
    ['https://friends.example/#/join?game=first&game=second', 'link'],
    ['https://friends.example/#/play?game=room', 'unsupported-link'],
    ['https://friends.example/#/play?game=room&share=1&peer=1', 'unsupported-link'],
    ['https://friends.example/#/settings', 'unsupported-link'],
    ['https://user:password@friends.example/#/join?game=room', 'link'],
    ['https://friends.example/\n#/join?game=room', 'link'],
    ['https://', 'link'],
    ['javascript:alert(1)', 'input'],
    ['file:///local/#/join?game=room', 'input'],
    ['web|friends|gm', 'input'],
  ])('explains why an input cannot be used: %s', (input, reason) => {
    expect(classifyJoinInvitation(input)).toEqual({ kind: 'invalid', reason })
  })

  it('keeps blank input neutral and rejects oversized input', () => {
    expect(classifyJoinInvitation('  ')).toEqual({ kind: 'empty' })
    expect(classifyJoinInvitation(`https://friends.example/#/join?game=${'a'.repeat(9000)}`)).toEqual({ kind: 'invalid', reason: 'link' })
  })
})

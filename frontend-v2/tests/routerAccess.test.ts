import { describe, expect, it, vi } from 'vitest'
import type { RouteLocationNormalized } from 'vue-router'
import { isPublicRoute, requireOwnerAccess } from '../src/router'

function route(
  name: string,
  fullPath: string,
  query: Record<string, string> = {},
): RouteLocationNormalized {
  return { name, fullPath, query } as unknown as RouteLocationNormalized
}

describe('owner route access', () => {
  it('redirects unauthenticated owner routes to login and preserves the destination', async () => {
    const result = await requireOwnerAccess(
      route('settings', '/settings?section=about'),
      vi.fn().mockResolvedValue('login-required'),
    )

    expect(result).toEqual({
      name: 'login',
      query: { redirect: '/#/settings?section=about' },
    })
  })

  it('allows owner routes when no password is configured or the owner is authenticated', async () => {
    await expect(requireOwnerAccess(
      route('settings', '/settings'),
      vi.fn().mockResolvedValue('allowed'),
    )).resolves.toBe(true)
  })

  it('keeps login, join, and player share routes public without probing owner access', async () => {
    const probe = vi.fn()
    const routes = [
      route('login', '/login'),
      route('join', '/join?game=demo', { game: 'demo' }),
      route('play', '/play?game=demo&user=player', { game: 'demo', user: 'player' }),
      route('play', '/play?game=demo&share=1', { game: 'demo', share: '1' }),
    ]

    for (const target of routes) {
      expect(isPublicRoute(target)).toBe(true)
      await expect(requireOwnerAccess(target, probe)).resolves.toBe(true)
    }
    expect(probe).not.toHaveBeenCalled()
  })

  it('does not mistake the owner play page for a public player link', () => {
    expect(isPublicRoute(route('play', '/play?game=demo', { game: 'demo' }))).toBe(false)
  })
})

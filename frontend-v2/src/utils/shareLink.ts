const URL_SCHEME_RE = /^[a-z][a-z0-9+.-]*:\/\//i
const fallbackOrigin = () => (typeof window === 'undefined' ? 'http://localhost' : window.location.origin)

export function normalizePublicBaseUrl(value?: string): string {
  const raw = String(value || '').trim()
  if (!raw) return fallbackOrigin()

  const candidate = URL_SCHEME_RE.test(raw) ? raw : `http://${raw}`
  try {
    const parsed = new URL(candidate)
    const path = parsed.pathname.replace(/\/+$/, '')
    return `${parsed.origin}${path}`
  } catch {
    return fallbackOrigin()
  }
}

export function buildJoinLink(gameKey: string, publicBaseUrl?: string, user?: string): string {
  const base = normalizePublicBaseUrl(publicBaseUrl).replace(/\/+$/, '')
  const url = new URL(`${base}/`)
  const params = new URLSearchParams({ game: gameKey, share: '1' })
  if (user) params.set('user', user)
  url.hash = `/join?${params.toString()}`
  return url.toString()
}

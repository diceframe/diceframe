import type { HonorsBadgeDefinition, LocalizedText } from '@/api/honors'

interface ThemePalette {
  ringDark: string
  ringMid: string
  ringLight: string
  dotColor: string
  metalLight: string
  metalMid: string
  metalDeep: string
  plateBase: string
}

const PALETTES: Record<string, ThemePalette> = {
  starlight: {
    ringDark: '#081330',
    ringMid: '#133172',
    ringLight: '#2c5bc0',
    dotColor: '#d9ecff',
    metalLight: '#fff3d1',
    metalMid: '#d9bc82',
    metalDeep: '#7c5d2c',
    plateBase: '#b59358',
  },
  lantern: {
    ringDark: '#26080d',
    ringMid: '#6b1826',
    ringLight: '#b33443',
    dotColor: '#ffd9c8',
    metalLight: '#ffe9d6',
    metalMid: '#dfa580',
    metalDeep: '#7c452d',
    plateBase: '#bc7d58',
  },
  celestial: {
    ringDark: '#09192b',
    ringMid: '#18446c',
    ringLight: '#3b84b8',
    dotColor: '#d8f1ff',
    metalLight: '#fff4d6',
    metalMid: '#d6b46f',
    metalDeep: '#735728',
    plateBase: '#b08f53',
  },
  crimson: {
    ringDark: '#2c0910',
    ringMid: '#7c1a28',
    ringLight: '#be3646',
    dotColor: '#ffd2c2',
    metalLight: '#ffe6bf',
    metalMid: '#d79e5e',
    metalDeep: '#7a4520',
    plateBase: '#9e2636',
  },
  jade: {
    ringDark: '#06201a',
    ringMid: '#125444',
    ringLight: '#269478',
    dotColor: '#d1fff0',
    metalLight: '#f4f7f2',
    metalMid: '#c6ccb8',
    metalDeep: '#627366',
    plateBase: '#99a896',
  },
  bronze: {
    ringDark: '#171920',
    ringMid: '#313946',
    ringLight: '#59687d',
    dotColor: '#ece0c8',
    metalLight: '#f2e0bd',
    metalMid: '#c5a474',
    metalDeep: '#6c5334',
    plateBase: '#9c7d54',
  },
}

export function resolveLocalizedText(
  map: LocalizedText | undefined,
  locale: string,
  fallback = '',
): string {
  if (!map) return fallback
  if (map[locale]) return map[locale]
  const base = locale.split('-')[0]
  if (base && map[base]) return map[base]
  return map['zh-CN'] || map.en || Object.values(map)[0] || fallback
}

function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function getPalette(theme: string): ThemePalette {
  return PALETTES[theme] || PALETTES.starlight
}

function hashSeed(input: string): number {
  let h = 2166136261
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) % 2147483640 + 7
}

function makeStardust(seed: number, dotColor: string, minR: number, maxR: number, count: number): string {
  let s = seed
  const rand = () => {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
  let dots = ''
  for (let i = 0; i < count; i += 1) {
    const angle = rand() * Math.PI * 2
    const r = minR + rand() * (maxR - minR)
    const cx = (150 + Math.cos(angle) * r).toFixed(1)
    const cy = (150 + Math.sin(angle) * r).toFixed(1)
    const size = (0.55 + rand() * 1.25).toFixed(2)
    const op = (0.28 + rand() * 0.72).toFixed(2)
    dots += `<circle cx="${cx}" cy="${cy}" r="${size}" fill="${dotColor}" opacity="${op}"/>`
  }
  return dots
}

function shapePath(shape: string): string {
  switch (shape) {
    case 'shield':
      return 'M150 14 L258 54 L258 152 C258 216 212 262 150 286 C88 262 42 216 42 152 L42 54 Z'
    case 'diamond':
      return 'M150 12 L276 150 L150 288 L24 150 Z'
    case 'wax-seal':
      return 'M150 16 C174 14 192 26 214 34 C236 42 258 58 266 82 C274 106 286 126 284 150 C282 174 272 194 264 218 C256 242 238 258 214 268 C190 278 174 286 150 284 C126 282 108 276 84 266 C60 256 42 238 34 214 C26 190 16 174 16 150 C16 126 28 106 36 82 C44 58 64 42 86 34 C108 26 126 18 150 16 Z'
    case 'hexagon':
      return 'M150 14 L268 82 L268 218 L150 286 L32 218 L32 82 Z'
    case 'compass':
    case 'coin':
    default:
      return 'M150 12 A138 138 0 1 1 149.9 12 Z'
  }
}

function shapeInnerPath(shape: string): string {
  switch (shape) {
    case 'shield':
      return 'M150 26 L246 62 L246 150 C246 206 205 248 150 271 C95 248 54 206 54 150 L54 62 Z'
    case 'diamond':
      return 'M150 26 L262 150 L150 274 L38 150 Z'
    case 'wax-seal':
      return 'M150 32 A118 118 0 1 1 149.9 32 Z'
    case 'hexagon':
      return 'M150 26 L256 88 L256 212 L150 274 L44 212 L44 88 Z'
    case 'compass':
    case 'coin':
    default:
      return 'M150 20 A130 130 0 1 1 149.9 20 Z'
  }
}

function shapeEmblemSvg(badgeId: string, shape: string, uid: string, palette: ThemePalette): string {
  if (badgeId === 'pioneer' || shape === 'shield') {
    return `
      <g filter="url(#emboss-${uid})">
        <polygon points="150,74 176,89 176,119 150,134 124,119 124,89" fill="none" stroke="url(#text-${uid})" stroke-width="2.6" stroke-linejoin="round"/>
        <polygon points="150,82 169,114 131,114" fill="none" stroke="url(#text-${uid})" stroke-width="1.9" stroke-linejoin="round"/>
        <path d="M150 74V82M176 89L169 114M124 89L131 114M176 119L169 114M124 119L131 114M150 134L169 114M150 134L131 114" stroke="url(#text-${uid})" stroke-width="1.4" opacity="0.88"/>
        <circle cx="150" cy="104" r="3.4" fill="${palette.metalLight}"/>
      </g>
    `
  }
  if (badgeId === 'lantern_keeper' || shape === 'diamond') {
    return `
      <g filter="url(#emboss-${uid})">
        <path d="M140 82 C140 76.5 144.5 72 150 72 C155.5 72 160 76.5 160 82" stroke="url(#text-${uid})" stroke-width="2.4" fill="none" stroke-linecap="round"/>
        <path d="M134 85 H166 L162 93 H138 Z" fill="url(#text-${uid})"/>
        <path d="M138 93 H162 L159 122 H141 Z" fill="none" stroke="url(#text-${uid})" stroke-width="2.4" stroke-linejoin="round"/>
        <path d="M150 99 C150 99 156 105.5 156 110.5 C156 113.8 153.3 116.5 150 116.5 C146.7 116.5 144 113.8 144 110.5 C144 105.5 150 99 150 99 Z" fill="${palette.metalLight}"/>
        <path d="M135 122 H165 L162 129 H138 Z" fill="url(#text-${uid})"/>
      </g>
    `
  }
  if (badgeId === 'world_weaver' || shape === 'coin') {
    return `
      <g filter="url(#emboss-${uid})">
        <circle cx="150" cy="103" r="24" fill="none" stroke="url(#text-${uid})" stroke-width="2.3"/>
        <ellipse cx="150" cy="103" rx="11.5" ry="24" fill="none" stroke="url(#text-${uid})" stroke-width="1.6" opacity="0.85"/>
        <path d="M126 103 H174" stroke="url(#text-${uid})" stroke-width="1.6" opacity="0.85"/>
        <ellipse cx="150" cy="103" rx="34" ry="10" transform="rotate(-24 150 103)" fill="none" stroke="${palette.metalLight}" stroke-width="2"/>
        <circle cx="179" cy="90" r="3.2" fill="${palette.metalLight}"/>
      </g>
    `
  }
  if (badgeId === 'chronicler' || shape === 'wax-seal') {
    return `
      <g filter="url(#emboss-${uid})">
        <path d="M130 82 H158 C162 82 165 85 165 89 V127 H137 C133 127 130 124 130 120 Z" fill="none" stroke="url(#text-${uid})" stroke-width="2.3" stroke-linejoin="round"/>
        <path d="M139 95 H156 M139 104 H154 M139 113 H148" stroke="url(#text-${uid})" stroke-width="1.8" stroke-linecap="round"/>
        <path d="M178 79 C168 83 160 93 157 107 L153 123 L167 119 C175 111 180 96 178 79 Z" fill="url(#text-${uid})" fill-opacity="0.32" stroke="${palette.metalLight}" stroke-width="2" stroke-linejoin="round"/>
        <path d="M153 123 L174 87" stroke="${palette.metalLight}" stroke-width="1.8" stroke-linecap="round"/>
      </g>
    `
  }
  if (badgeId === 'arcane_forger' || shape === 'hexagon') {
    return `
      <g filter="url(#emboss-${uid})">
        <path d="M136 86 L120 104 L136 122" stroke="url(#text-${uid})" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M164 86 L180 104 L164 122" stroke="url(#text-${uid})" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M150 79 L158 104 L150 129 L142 104 Z" fill="url(#text-${uid})"/>
        <circle cx="150" cy="104" r="3" fill="${palette.metalLight}"/>
      </g>
    `
  }
  // compass / fallback
  return `
    <g filter="url(#emboss-${uid})">
      <path d="M150 73 L156 97 L180 103 L156 109 L150 133 L144 109 L120 103 L144 97 Z" fill="url(#text-${uid})" fill-opacity="0.28" stroke="url(#text-${uid})" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M133 86 L144 97 M167 86 L156 97 M167 120 L156 109 M133 120 L144 109" stroke="url(#text-${uid})" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="150" cy="103" r="4.2" fill="${palette.metalLight}"/>
    </g>
  `
}

export function buildBadgeMaskDataUrl(badge: Pick<HonorsBadgeDefinition, 'shape' | 'mask_url' | 'image_url'>): string {
  if (badge.mask_url) return `url("${badge.mask_url}")`
  if (badge.image_url) return `url("${badge.image_url}")`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300"><path d="${shapePath(badge.shape)}" fill="#ffffff"/></svg>`
  return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`
}

export function buildBadgeFrontSvg(
  badge: HonorsBadgeDefinition,
  locale: string,
  uid: string,
  compact = false,
): string {
  const palette = getPalette(badge.theme)
  const outer = shapePath(badge.shape)
  const inner = shapeInnerPath(badge.shape)
  const seed = hashSeed(badge.id)
  const stardust = makeStardust(seed, palette.dotColor, 95, 134, compact ? 42 : 120)
  const title = escapeXml(resolveLocalizedText(badge.name, locale, badge.id))
  const subtitle = escapeXml(resolveLocalizedText(badge.subtitle, locale, ''))
  const enTitle = escapeXml((badge.name?.en || badge.id).toUpperCase())
  const isWax = badge.shape === 'wax-seal'

  const centerPlate = isWax
    ? `
      <circle cx="150" cy="150" r="102" fill="url(#enamel-${uid})" filter="url(#inset-${uid})"/>
      <circle cx="150" cy="150" r="95" fill="none" stroke="${palette.metalLight}" stroke-opacity="0.5" stroke-width="1.6"/>
      <circle cx="150" cy="150" r="89" fill="none" stroke="${palette.metalMid}" stroke-opacity="0.4" stroke-width="1" stroke-dasharray="3 3"/>
    `
    : `
      <circle cx="150" cy="150" r="92" fill="none" stroke="#000000" stroke-opacity="0.52" stroke-width="5.5"/>
      <circle cx="150" cy="150" r="89" fill="url(#plate-${uid})" filter="url(#inset-${uid})"/>
      <circle cx="150" cy="150" r="87" fill="url(#pat-${uid})"/>
      <circle cx="150" cy="150" r="82" fill="none" stroke="${palette.metalLight}" stroke-opacity="0.48" stroke-width="1.1"/>
      <circle cx="150" cy="150" r="78" fill="none" stroke="${palette.metalDeep}" stroke-opacity="0.34" stroke-width="0.9" stroke-dasharray="2.5 3"/>
    `

  const textBlock = compact
    ? ''
    : `
      <g filter="url(#emboss-${uid})" text-anchor="middle">
        <text x="150" y="164" font-size="18" font-weight="700" letter-spacing="1.6" fill="url(#text-${uid})" font-family="'Noto Serif SC','Songti SC',serif">${title}</text>
        ${subtitle ? `<text x="150" y="186" font-size="11.5" font-weight="600" letter-spacing="0.8" fill="url(#text-${uid})" font-family="'Noto Sans SC',system-ui,sans-serif" opacity="0.94">${subtitle}</text>` : ''}
        <text x="150" y="210" font-size="9" font-weight="600" letter-spacing="2.2" fill="url(#text-${uid})" font-family="ui-sans-serif,system-ui,sans-serif" opacity="0.82">${enTitle}</text>
      </g>
    `

  const emblemTransform = compact ? 'transform="translate(0 45) scale(1.18)" transform-origin="150 104"' : ''

  return `
    <svg viewBox="0 0 300 300" xmlns="http://www.w3.org/2000/svg" class="df-badge-svg">
      <defs>
        <clipPath id="clip-${uid}">
          <path d="${inner}"/>
        </clipPath>
        <linearGradient id="rim-${uid}" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="${palette.metalLight}"/>
          <stop offset="32%" stop-color="${palette.metalMid}"/>
          <stop offset="68%" stop-color="${palette.metalDeep}"/>
          <stop offset="100%" stop-color="${palette.metalLight}"/>
        </linearGradient>
        <radialGradient id="enamel-${uid}" cx="35%" cy="28%" r="72%">
          <stop offset="0%" stop-color="${palette.ringLight}"/>
          <stop offset="48%" stop-color="${palette.ringMid}"/>
          <stop offset="100%" stop-color="${palette.ringDark}"/>
        </radialGradient>
        <linearGradient id="gloss-${uid}" x1="15%" y1="5%" x2="85%" y2="95%">
          <stop offset="0%" stop-color="#ffffff" stop-opacity="0.34"/>
          <stop offset="28%" stop-color="#ffffff" stop-opacity="0.06"/>
          <stop offset="72%" stop-color="#000000" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="#ffffff" stop-opacity="0.18"/>
        </linearGradient>
        <linearGradient id="plate-${uid}" x1="12%" y1="10%" x2="88%" y2="90%">
          <stop offset="0%" stop-color="${palette.metalLight}"/>
          <stop offset="26%" stop-color="${palette.metalMid}"/>
          <stop offset="56%" stop-color="${palette.plateBase}"/>
          <stop offset="82%" stop-color="${palette.metalDeep}"/>
          <stop offset="100%" stop-color="${palette.metalMid}"/>
        </linearGradient>
        <linearGradient id="text-${uid}" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stop-color="#fff8e3"/>
          <stop offset="52%" stop-color="${palette.metalMid}"/>
          <stop offset="100%" stop-color="${palette.metalDeep}"/>
        </linearGradient>
        <pattern id="pat-${uid}" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="rotate(28)">
          <rect x="2" y="2" width="18" height="18" rx="3" fill="none" stroke="${palette.metalDeep}" stroke-opacity="0.22" stroke-width="0.9"/>
          <circle cx="11" cy="11" r="2.2" fill="none" stroke="${palette.metalLight}" stroke-opacity="0.24" stroke-width="0.8"/>
          <path d="M11 2V6M11 16V20M2 11H6M16 11H20" stroke="${palette.metalDeep}" stroke-opacity="0.2" stroke-width="0.8"/>
        </pattern>
        <filter id="emboss-${uid}" x="-18%" y="-18%" width="136%" height="136%">
          <feDropShadow dx="-0.7" dy="-0.8" stdDeviation="0.4" flood-color="#fff9e8" flood-opacity="0.72"/>
          <feDropShadow dx="1.2" dy="1.8" stdDeviation="1.1" flood-color="#221405" flood-opacity="0.72"/>
        </filter>
        <filter id="inset-${uid}" x="-12%" y="-12%" width="124%" height="124%">
          <feDropShadow dx="0" dy="2" stdDeviation="3.5" flood-color="#000000" flood-opacity="0.52"/>
        </filter>
      </defs>

      <path d="${outer}" fill="url(#rim-${uid})"/>
      <path d="${inner}" fill="url(#enamel-${uid})"/>
      <g clip-path="url(#clip-${uid})">
        ${stardust}
      </g>
      <path d="${inner}" fill="url(#gloss-${uid})"/>
      ${centerPlate}
      <g ${emblemTransform}>
        ${shapeEmblemSvg(badge.id, badge.shape, uid, palette)}
      </g>
      ${textBlock}
    </svg>
  `
}

export function buildBadgeBackSvg(
  badge: HonorsBadgeDefinition,
  options: {
    holderName: string
    holderId: string
    issuedLabel: string
    awardedPrefix: string
    uid: string
  },
): string {
  const palette = getPalette(badge.theme)
  const outer = shapePath(badge.shape)
  const inner = shapeInnerPath(badge.shape)
  const uid = options.uid

  return `
    <svg viewBox="0 0 300 300" xmlns="http://www.w3.org/2000/svg" class="df-badge-svg">
      <defs>
        <linearGradient id="brim-${uid}" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="${palette.metalLight}"/>
          <stop offset="50%" stop-color="${palette.metalMid}"/>
          <stop offset="100%" stop-color="${palette.metalDeep}"/>
        </linearGradient>
        <radialGradient id="bmetal-${uid}" cx="35%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#352a1d"/>
          <stop offset="55%" stop-color="#1d160f"/>
          <stop offset="100%" stop-color="#0f0b07"/>
        </radialGradient>
        <linearGradient id="btext-${uid}" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stop-color="${palette.metalLight}"/>
          <stop offset="100%" stop-color="${palette.metalMid}"/>
        </linearGradient>
      </defs>
      <path d="${outer}" fill="url(#brim-${uid})"/>
      <path d="${inner}" fill="url(#bmetal-${uid})"/>
      <circle cx="150" cy="150" r="88" fill="none" stroke="${palette.metalMid}" stroke-opacity="0.42" stroke-width="1.2"/>
      <circle cx="150" cy="150" r="82" fill="none" stroke="${palette.metalMid}" stroke-opacity="0.22" stroke-width="0.9" stroke-dasharray="3 4"/>
      <g text-anchor="middle" fill="url(#btext-${uid})">
        <text x="150" y="98" font-size="9.5" letter-spacing="2.8" opacity="0.72" font-family="ui-monospace,monospace">DICEFRAME HONORS</text>
        <line x1="104" y1="110" x2="196" y2="110" stroke="${palette.metalMid}" stroke-opacity="0.4" stroke-width="1"/>
        <text x="150" y="136" font-size="11" opacity="0.76" font-family="'Noto Sans SC',system-ui,sans-serif">${escapeXml(options.awardedPrefix)}</text>
        <text x="150" y="167" font-size="22" font-weight="700" letter-spacing="2" font-family="'Noto Serif SC','Songti SC',serif">${escapeXml(options.holderName)}</text>
        <text x="150" y="194" font-size="12.5" letter-spacing="2.2" font-family="ui-monospace,monospace" opacity="0.92">${escapeXml(options.holderId)}</text>
        <line x1="114" y1="208" x2="186" y2="208" stroke="${palette.metalMid}" stroke-opacity="0.34" stroke-width="1"/>
        <text x="150" y="226" font-size="9.5" letter-spacing="1.8" opacity="0.62" font-family="ui-monospace,monospace">${escapeXml(options.issuedLabel)}</text>
      </g>
    </svg>
  `
}

export async function exportHonorCardPng(params: {
  badge: HonorsBadgeDefinition
  locale: string
  holderName: string
  holderId: string
  issuedDate: string
  awardedPrefix: string
}): Promise<Blob> {
  const width = 960
  const height = 540
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas 2D context unavailable')

  // Background gradient
  const bg = ctx.createRadialGradient(width * 0.28, height * 0.48, 30, width * 0.5, height * 0.5, width * 0.72)
  bg.addColorStop(0, '#1e2c3d')
  bg.addColorStop(0.55, '#111924')
  bg.addColorStop(1, '#090e15')
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, width, height)

  // Gilded border & DiceFrame corner brackets
  ctx.strokeStyle = 'rgba(216, 173, 82, 0.36)'
  ctx.lineWidth = 2
  ctx.strokeRect(24, 24, width - 48, height - 48)
  ctx.strokeStyle = '#d8ad52'
  ctx.lineWidth = 3
  const b = 26
  const corners: [number, number, number, number][] = [
    [24, 24, 1, 1],
    [width - 24, 24, -1, 1],
    [width - 24, height - 24, -1, -1],
    [24, height - 24, 1, -1],
  ]
  for (const [cx, cy, dx, dy] of corners) {
    ctx.beginPath()
    ctx.moveTo(cx + dx * b, cy)
    ctx.lineTo(cx, cy)
    ctx.lineTo(cx, cy + dy * b)
    ctx.stroke()
  }

  // Render badge SVG or image onto left side
  const svgMarkup = buildBadgeFrontSvg(params.badge, params.locale, 'export', false)
  const imgSrc = params.badge.image_url
    ? params.badge.image_url
    : `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgMarkup)}`

  await new Promise<void>((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      ctx.save()
      ctx.shadowColor = 'rgba(0, 0, 0, 0.55)'
      ctx.shadowBlur = 32
      ctx.shadowOffsetY = 14
      ctx.drawImage(img, 86, 110, 320, 320)
      ctx.restore()
      resolve()
    }
    img.onerror = () => reject(new Error('Failed to load badge artwork for export'))
    img.src = imgSrc
  })

  // Right side typography
  const title = resolveLocalizedText(params.badge.name, params.locale, params.badge.id)
  const subtitle = resolveLocalizedText(params.badge.subtitle, params.locale, '')
  const craft = resolveLocalizedText(params.badge.craft, params.locale, '')

  ctx.fillStyle = '#c7a362'
  ctx.font = '600 15px Consolas, monospace'
  ctx.fillText('DICEFRAME · COMMEMORATIVE HONOR', 456, 132)

  ctx.fillStyle = '#f5e2b5'
  ctx.font = '700 40px "Noto Serif SC", "Songti SC", Georgia, serif'
  ctx.fillText(title, 456, 192)

  if (subtitle) {
    ctx.fillStyle = '#cfd4ca'
    ctx.font = '500 20px "Noto Sans SC", system-ui, sans-serif'
    ctx.fillText(subtitle, 456, 232)
  }

  ctx.strokeStyle = 'rgba(216, 173, 82, 0.35)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(456, 260)
  ctx.lineTo(886, 260)
  ctx.stroke()

  ctx.fillStyle = '#9aa6a0'
  ctx.font = '500 16px "Noto Sans SC", system-ui, sans-serif'
  ctx.fillText(params.awardedPrefix, 456, 302)

  ctx.fillStyle = '#f0eee5'
  ctx.font = '700 34px "Noto Serif SC", "Songti SC", Georgia, serif'
  ctx.fillText(params.holderName, 456, 352)

  ctx.fillStyle = '#d8ad52'
  ctx.font = '600 19px Consolas, monospace'
  ctx.fillText(`NO. ${params.holderId}   ·   ${params.issuedDate}`, 456, 396)

  if (craft) {
    ctx.fillStyle = '#8d9994'
    ctx.font = '400 15px "Noto Sans SC", system-ui, sans-serif'
    ctx.fillText(craft, 456, 444)
  }

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('PNG export returned empty blob'))
    }, 'image/png')
  })
}

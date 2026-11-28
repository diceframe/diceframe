/**
 * Web Audio API 轻量合成骰子滚动与落盘音效（零外部音频依赖）。
 * 浏览器不支持、自动播放受限或用户静音时安全静默跳过。
 */

const MUTE_STORAGE_KEY = 'diceframe:dice-sound-muted'

export type DiceSoundOutcome = 'normal' | 'critical' | 'fumble'

let sharedCtx: AudioContext | null = null

export function isDiceSoundMuted(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(MUTE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export function setDiceSoundMuted(muted: boolean): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(MUTE_STORAGE_KEY, muted ? '1' : '0')
    }
  } catch {
    // 隐私模式或受限环境忽略存储错误
  }
}

function getAudioContext(): AudioContext | null {
  if (typeof window === 'undefined') return null
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    if (!sharedCtx) sharedCtx = new Ctor()
    if (sharedCtx.state === 'suspended') {
      void sharedCtx.resume().catch(() => {})
    }
    return sharedCtx
  } catch {
    return null
  }
}

function scheduleWoodClick(ctx: AudioContext, at: number, gainPeak: number, freq: number): void {
  // 短促噪声脉冲 + 谐振带通：模拟树脂/亚克力骰子撞击木质绒布盘的清脆声
  const duration = 0.032
  const sampleCount = Math.max(1, Math.floor(ctx.sampleRate * duration))
  const buffer = ctx.createBuffer(1, sampleCount, ctx.sampleRate)
  const data = buffer.getChannelData(0)
  for (let i = 0; i < sampleCount; i++) {
    const decay = Math.exp(-i / (sampleCount * 0.28))
    data[i] = (Math.random() * 2 - 1) * decay
  }

  const src = ctx.createBufferSource()
  src.buffer = buffer

  const filter = ctx.createBiquadFilter()
  filter.type = 'bandpass'
  filter.frequency.setValueAtTime(freq, at)
  filter.Q.setValueAtTime(3.2, at)

  const gain = ctx.createGain()
  gain.gain.setValueAtTime(0.0001, at)
  gain.gain.linearRampToValueAtTime(gainPeak, at + 0.003)
  gain.gain.exponentialRampToValueAtTime(0.0001, at + duration)

  src.connect(filter)
  filter.connect(gain)
  gain.connect(ctx.destination)
  src.start(at)
  src.stop(at + duration + 0.005)
}

function scheduleThud(ctx: AudioContext, at: number, gainPeak: number): void {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = 'triangle'
  osc.frequency.setValueAtTime(165, at)
  osc.frequency.exponentialRampToValueAtTime(58, at + 0.085)

  gain.gain.setValueAtTime(0.0001, at)
  gain.gain.linearRampToValueAtTime(gainPeak, at + 0.006)
  gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.09)

  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(at)
  osc.stop(at + 0.095)
}

function scheduleOutcomeChime(ctx: AudioContext, at: number, outcome: DiceSoundOutcome): void {
  if (outcome === 'normal') return
  const notes = outcome === 'critical' ? [659.25, 987.77, 1318.51] : [220, 174.61]
  notes.forEach((freq, idx) => {
    const start = at + idx * (outcome === 'critical' ? 0.055 : 0.09)
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = outcome === 'critical' ? 'sine' : 'triangle'
    osc.frequency.setValueAtTime(freq, start)

    const peak = outcome === 'critical' ? 0.055 : 0.05
    const tail = outcome === 'critical' ? 0.36 : 0.24
    gain.gain.setValueAtTime(0.0001, start)
    gain.gain.linearRampToValueAtTime(peak, start + 0.015)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + tail)

    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(start)
    osc.stop(start + tail + 0.02)
  })
}

export function playDiceRollSound(diceCount = 1, outcome: DiceSoundOutcome = 'normal'): void {
  if (isDiceSoundMuted()) return
  const ctx = getAudioContext()
  if (!ctx) return

  const now = ctx.currentTime + 0.01
  const clatterHits = Math.min(7, Math.max(3, diceCount + 2))
  // 模拟抛掷滚动 → 减速弹跳 → 稳稳落盘
  let cursor = now
  for (let i = 0; i < clatterHits; i++) {
    const progress = i / Math.max(1, clatterHits - 1)
    const freq = 1450 + Math.random() * 850 - progress * 350
    const volume = 0.09 + (1 - progress * 0.45) * 0.07
    scheduleWoodClick(ctx, cursor, volume, freq)
    cursor += 0.055 + progress * 0.065 + Math.random() * 0.018
  }

  scheduleThud(ctx, cursor + 0.02, 0.13)
  scheduleWoodClick(ctx, cursor + 0.024, 0.11, 1150)

  if (outcome !== 'normal') {
    scheduleOutcomeChime(ctx, cursor + 0.12, outcome)
  }
}

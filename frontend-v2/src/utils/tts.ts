import { ref } from 'vue'
import { speechApi } from '@/api/speech'
import type { AppConfig } from '@/api/types'

export interface SpeakOptions {
  lang?: string
  rate?: number
  pitch?: number
  gameKey?: string
  role?: 'gm' | 'player'
  voice?: string
  onEnd?: () => void
}

interface RuntimeTtsConfig {
  provider: 'browser' | 'openai-compatible' | 'gpt-sovits'
  defaultVoice: string
  gmVoice: string
  playerVoice: string
}

const RATE_STORAGE_KEY = 'trpg_tts_rate'
export const DEFAULT_TTS_RATE = 1.0
export const ttsRuntimeConfig = ref<RuntimeTtsConfig>({
  provider: 'browser',
  defaultVoice: 'alloy',
  gmVoice: '',
  playerVoice: '',
})

let configPromise: Promise<void> | null = null

export async function initializeTts(force = false): Promise<void> {
  if (configPromise && !force) return configPromise
  configPromise = speechApi.publicConfig().then((config: AppConfig) => {
    const provider = config.tts_provider
    ttsRuntimeConfig.value = {
      provider: provider === 'openai-compatible' || provider === 'gpt-sovits' ? provider : 'browser',
      defaultVoice: String(config.tts_default_voice || 'alloy'),
      gmVoice: String(config.tts_gm_voice || ''),
      playerVoice: String(config.tts_player_voice || ''),
    }
  }).catch(() => {
    ttsRuntimeConfig.value = { provider: 'browser', defaultVoice: 'alloy', gmVoice: '', playerVoice: '' }
    configPromise = null
  })
  return configPromise
}

export function ttsRate(): number {
  try {
    const raw = Number(localStorage.getItem(RATE_STORAGE_KEY))
    if (Number.isFinite(raw) && raw >= 0.5 && raw <= 5) return raw
  } catch { /* localStorage unavailable: use the default */ }
  return DEFAULT_TTS_RATE
}

export function setTtsRate(rate: number): void {
  const clamped = Math.min(5, Math.max(0.5, Number(rate) || DEFAULT_TTS_RATE))
  try { localStorage.setItem(RATE_STORAGE_KEY, String(clamped)) } catch { /* ignore */ }
}

let voices: SpeechSynthesisVoice[] = []

function browserSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

function loadVoices(): void {
  if (!browserSupported()) return
  voices = window.speechSynthesis.getVoices()
}

if (typeof window !== 'undefined') {
  loadVoices()
  if (browserSupported()) {
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices)
  }
}

export function ttsSupported(): boolean {
  return browserSupported() || ttsRuntimeConfig.value.provider !== 'browser'
}

function pickVoice(lang: string): SpeechSynthesisVoice | null {
  const normalized = lang.toLowerCase()
  return voices.find(voice => voice.lang.toLowerCase().replace('_', '-').startsWith(normalized)) || null
}

export const speakingKey = ref<string>('')

export function stripHtml(text: string): string {
  const raw = String(text || '')
  if (!raw.includes('<') && !raw.includes('&')) return raw
  return raw
    .replace(/<[^>]+>/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .trim()
}

export function chunkSpeechText(text: string, maxChars = 1200): string[] {
  const plain = stripHtml(text).replace(/\s+/g, ' ').trim()
  if (!plain) return []
  const bounded = Math.max(200, Math.min(maxChars, 2000))
  const sentences = plain
    .replace(/([。！？.!?；;])/g, '$1\u0000')
    .split('\u0000')
    .map(part => part.trim())
    .filter(Boolean)
  const chunks: string[] = []
  let current = ''
  for (const sentence of sentences) {
    if (sentence.length > bounded) {
      if (current) chunks.push(current)
      for (let offset = 0; offset < sentence.length; offset += bounded) {
        chunks.push(sentence.slice(offset, offset + bounded))
      }
      current = ''
    } else if (!current || current.length + sentence.length + 1 <= bounded) {
      current = current ? `${current} ${sentence}` : sentence
    } else {
      chunks.push(current)
      current = sentence
    }
  }
  if (current) chunks.push(current)
  return chunks
}

let currentAudio: HTMLAudioElement | null = null
let currentAudioUrl = ''
let settleAudioPlayback: (() => void) | null = null
let playbackGeneration = 0

function cleanupAudio(): void {
  if (currentAudio) {
    currentAudio.onended = null
    currentAudio.onerror = null
    currentAudio.pause()
    currentAudio = null
  }
  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl)
    currentAudioUrl = ''
  }
  const settle = settleAudioPlayback
  settleAudioPlayback = null
  settle?.()
}

function voiceFor(options: SpeakOptions): string {
  if (options.voice) return options.voice
  if (options.role === 'gm' && ttsRuntimeConfig.value.gmVoice) return ttsRuntimeConfig.value.gmVoice
  if (options.role === 'player' && ttsRuntimeConfig.value.playerVoice) return ttsRuntimeConfig.value.playerVoice
  return ttsRuntimeConfig.value.defaultVoice
}

function speakInBrowser(text: string, key: string, options: SpeakOptions): void {
  if (!browserSupported()) {
    if (speakingKey.value === key) speakingKey.value = ''
    options.onEnd?.()
    return
  }
  const synth = window.speechSynthesis
  synth.cancel()
  speakingKey.value = key
  const utterance = new SpeechSynthesisUtterance(stripHtml(text))
  const voice = pickVoice(options.lang || 'zh-CN')
  if (voice) utterance.voice = voice
  utterance.lang = voice?.lang || options.lang || 'zh-CN'
  utterance.rate = options.rate ?? ttsRate()
  utterance.pitch = options.pitch ?? 1
  utterance.onend = () => finishSpeaking(key, options)
  utterance.onerror = () => finishSpeaking(key, options)
  synth.speak(utterance)
}

function finishSpeaking(key: string, options: SpeakOptions): void {
  if (speakingKey.value === key) speakingKey.value = ''
  options.onEnd?.()
}

async function playBlob(blob: Blob, generation: number): Promise<void> {
  if (generation !== playbackGeneration) return
  cleanupAudio()
  currentAudioUrl = URL.createObjectURL(blob)
  const audio = new Audio(currentAudioUrl)
  currentAudio = audio
  const ended = new Promise<void>((resolve, reject) => {
    settleAudioPlayback = resolve
    audio.onended = () => resolve()
    audio.onerror = () => reject(new Error('audio-playback-failed'))
  })
  await audio.play()
  await ended
  if (currentAudio === audio) cleanupAudio()
}

async function speakWithServer(text: string, key: string, options: SpeakOptions, generation: number): Promise<void> {
  const chunks = chunkSpeechText(text)
  let played = 0
  try {
    for (const chunk of chunks) {
      if (generation !== playbackGeneration || speakingKey.value !== key) return
      const blob = await speechApi.synthesize(options.gameKey || '', {
        text: chunk,
        voice: voiceFor(options),
        language: options.lang || 'zh-CN',
        speed: options.rate ?? ttsRate(),
      })
      if (generation !== playbackGeneration || speakingKey.value !== key) return
      await playBlob(blob, generation)
      played += 1
    }
    if (generation === playbackGeneration) finishSpeaking(key, options)
  } catch {
    cleanupAudio()
    if (generation !== playbackGeneration) return
    if (played === 0 && browserSupported()) {
      speakInBrowser(text, key, options)
    } else {
      finishSpeaking(key, options)
    }
  }
}

export function ttsSpeak(text: string, key: string, options: SpeakOptions = {}): void {
  if (!text.trim() || !ttsSupported()) return
  ttsStop()
  speakingKey.value = key
  const generation = playbackGeneration
  const useServer = ttsRuntimeConfig.value.provider !== 'browser' && Boolean(options.gameKey)
  if (useServer) {
    void speakWithServer(text, key, options, generation)
  } else {
    speakInBrowser(text, key, options)
  }
}

export function ttsStop(): void {
  playbackGeneration += 1
  cleanupAudio()
  if (browserSupported()) window.speechSynthesis.cancel()
  speakingKey.value = ''
}

export function ttsToggle(text: string, key: string, options: SpeakOptions = {}): void {
  if (speakingKey.value === key) ttsStop()
  else ttsSpeak(text, key, options)
}

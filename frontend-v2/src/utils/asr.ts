import { onUnmounted, ref } from 'vue'
import { ApiError, api } from '@/api/client'
import { speechApi } from '@/api/speech'
import type { AppConfig } from '@/api/types'
import { activePeerGameClient } from '@/peer/game/bridge'

export const MAX_RECORDING_SECONDS = 60

export type AsrErrorCode = 'asr-mic-denied' | 'asr-record-failed' | 'asr-failed'

interface AsrRuntimeConfig {
  provider: 'disabled' | 'openai-compatible'
  ready: boolean
}

const asrRuntimeConfig = ref<AsrRuntimeConfig>({ provider: 'disabled', ready: false })

let configPromise: Promise<void> | null = null

export async function initializeAsr(force = false): Promise<void> {
  if (configPromise && !force) return configPromise
  configPromise = api<AppConfig>('/config').then((config: AppConfig) => {
    const provider = config.asr_provider === 'openai-compatible' ? 'openai-compatible' : 'disabled'
    asrRuntimeConfig.value = {
      provider,
      ready: provider === 'openai-compatible' && Boolean(
        config.asr_provider_ref && config.ai_providers?.some(
          item => item.id === config.asr_provider_ref && String(item.base_url || '').trim(),
        ),
      ),
    }
  }).catch(() => {
    asrRuntimeConfig.value = { provider: 'disabled', ready: false }
    configPromise = null
  })
  return configPromise
}

function secureMicContext(): boolean {
  if (typeof window === 'undefined') return false
  // getUserMedia 仅在安全上下文（HTTPS / localhost）可用；局域网 HTTP 访问时直接隐藏麦克风。
  if (window.isSecureContext === false) return false
  return typeof navigator !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof window.MediaRecorder !== 'undefined'
}

function peerGameActive(): boolean {
  try {
    return activePeerGameClient() !== null
  } catch {
    return false
  }
}

export function voiceInputSupported(): boolean {
  // P2P 直连局的音频上传无法走数据通道白名单，联机模式下直接隐藏（与服务端 TTS 的先例一致）。
  return asrRuntimeConfig.value.ready && secureMicContext() && !peerGameActive()
}

export function asrLanguageFor(locale: string): string {
  if (locale.startsWith('en')) return 'en-US'
  if (locale.startsWith('ja')) return 'ja-JP'
  return 'zh-CN'
}

export function appendDictated(current: string, chunk: string): string {
  const piece = String(chunk || '').replace(/\s+/g, ' ').trim()
  if (!piece) return current
  const base = String(current || '').replace(/[ \t]+$/, '')
  if (!base.trim()) return piece
  const latinBoundary = /[A-Za-z0-9]$/.test(base) && /^[A-Za-z0-9]/.test(piece)
  return base + (latinBoundary ? ' ' : '') + piece
}

export function pickRecordingMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  for (const type of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
    if (MediaRecorder.isTypeSupported(type)) return type
  }
  return ''
}

export interface RecordingSession {
  stop(): Promise<Blob>
  cancel(): void
}

export async function startRecording(maxSeconds = MAX_RECORDING_SECONDS): Promise<RecordingSession> {
  if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    throw new Error('asr-record-failed')
  }
  const mimeType = pickRecordingMimeType()
  if (!mimeType) throw new Error('asr-record-failed')
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch {
    throw new Error('asr-mic-denied')
  }
  const chunks: Blob[] = []
  const recorder = new MediaRecorder(stream, { mimeType })
  const release = () => {
    clearTimeout(timer)
    stream.getTracks().forEach(track => track.stop())
  }
  const settle = () => {
    release()
    if (chunks.length) return new Blob(chunks, { type: recorder.mimeType || mimeType })
    throw new Error('asr-record-failed')
  }
  const timer = setTimeout(() => {
    try {
      if (recorder.state !== 'inactive') recorder.stop()
    } catch { /* already stopping */ }
  }, maxSeconds * 1000)
  recorder.ondataavailable = (event: BlobEvent) => {
    if (event.data && event.data.size > 0) chunks.push(event.data)
  }
  recorder.onerror = () => {
    release()
  }
  recorder.onstop = () => {
    release()
  }
  recorder.start(250)
  return {
    stop: () => new Promise<Blob>((resolve, reject) => {
      recorder.onstop = () => {
        try {
          resolve(settle())
        } catch (error) {
          reject(error instanceof Error ? error : new Error('asr-record-failed'))
        }
      }
      try {
        if (recorder.state === 'inactive') {
          resolve(settle())
        } else {
          recorder.stop()
        }
      } catch {
        try {
          resolve(settle())
        } catch (error) {
          reject(error instanceof Error ? error : new Error('asr-record-failed'))
        }
      }
    }),
    cancel: () => {
      try {
        if (recorder.state !== 'inactive') recorder.stop()
      } catch { /* already stopping */ }
      release()
    },
  }
}

export interface VoiceInputOptions {
  gameKey: string
  lang?: () => string
  onText: (chunk: string) => void
}

export function useVoiceInput(options: VoiceInputOptions) {
  const recording = ref(false)
  const transcribing = ref(false)
  const errorCode = ref<AsrErrorCode | ''>('')
  const serverMessage = ref('')
  const elapsedSeconds = ref(0)

  let session: RecordingSession | null = null
  let elapsedTimer: number | null = null

  function clearElapsedTimer(): void {
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer)
      elapsedTimer = null
    }
  }

  function reportError(error: unknown): void {
    if (error instanceof ApiError && error.message) {
      errorCode.value = ''
      serverMessage.value = error.message
      return
    }
    serverMessage.value = ''
    errorCode.value = error instanceof Error && (error.message === 'asr-mic-denied'
      || error.message === 'asr-record-failed')
      ? error.message as AsrErrorCode
      : 'asr-failed'
  }

  async function start(): Promise<void> {
    if (recording.value || transcribing.value) return
    errorCode.value = ''
    serverMessage.value = ''
    try {
      session = await startRecording()
    } catch (error) {
      reportError(error)
      return
    }
    recording.value = true
    elapsedSeconds.value = 0
    elapsedTimer = window.setInterval(() => { elapsedSeconds.value += 1 }, 1000)
  }

  async function stop(): Promise<void> {
    const active = session
    session = null
    clearElapsedTimer()
    recording.value = false
    if (!active) return
    let blob: Blob
    try {
      blob = await active.stop()
    } catch (error) {
      reportError(error)
      return
    }
    transcribing.value = true
    try {
      const text = await speechApi.transcribe(options.gameKey, blob, options.lang?.() || '')
      if (text.trim()) options.onText(text.trim())
    } catch (error) {
      reportError(error)
    } finally {
      transcribing.value = false
    }
  }

  async function toggle(): Promise<void> {
    if (transcribing.value) return
    if (recording.value) await stop()
    else await start()
  }

  function release(): void {
    session?.cancel()
    session = null
    clearElapsedTimer()
    recording.value = false
    transcribing.value = false
  }

  onUnmounted(release)

  return { recording, transcribing, errorCode, serverMessage, elapsedSeconds, start, stop, toggle, release }
}

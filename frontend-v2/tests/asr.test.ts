import { mount } from '@vue/test-utils'
import { createApp, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import ActionComposer from '../src/components/ActionComposer.vue'
import type { GameDetail } from '../src/api/types'
import { api } from '../src/api/client'
import { speechApi } from '../src/api/speech'
import { activePeerGameClient } from '../src/peer/game/bridge'
import {
  appendDictated,
  asrLanguageFor,
  initializeAsr,
  startRecording,
  useVoiceInput,
  voiceInputSupported,
} from '../src/utils/asr'

vi.mock('../src/api/client', () => {
  class ApiError extends Error {
    constructor(message: string, public status: number, public code?: string, public retryAfter?: number) { super(message) }
  }
  return { api: vi.fn(), apiBlob: vi.fn(), ApiError }
})
vi.mock('../src/api/speech', () => ({
  speechApi: {
    publicConfig: vi.fn(),
    transcribe: vi.fn(),
    transcribeTest: vi.fn(),
  },
}))
vi.mock('../src/peer/game/bridge', () => ({
  activePeerGameClient: vi.fn(() => null),
}))

const mockedApi = vi.mocked(api)
const mockedTranscribe = vi.mocked(speechApi.transcribe)
const mockedPeerClient = vi.mocked(activePeerGameClient)

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  static isTypeSupported(type: string): boolean {
    return type.startsWith('audio/webm') || type === 'audio/mp4'
  }
  mimeType = ''
  state: 'inactive' | 'recording' = 'inactive'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  stream: { getTracks: () => Array<{ stop: () => void }> }

  constructor(stream: FakeMediaRecorder['stream'], options?: { mimeType?: string }) {
    this.stream = stream
    this.mimeType = options?.mimeType || ''
    FakeMediaRecorder.instances.push(this)
  }

  start(): void {
    this.state = 'recording'
  }

  stop(): void {
    if (this.state === 'inactive') throw new Error('InvalidStateError')
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['clip'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

function stubMicAccess(): void {
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })) },
  })
}

function withSetup<T>(composable: () => T): T {
  let result!: T
  const app = createApp({ setup() { result = composable(); return () => h('div') } })
  app.mount(document.createElement('div'))
  return result
}

function detail(): GameDetail {
  return {
    game_key: 'web|room|bot',
    round_number: 3,
    solo_mode: false,
    multiplayer: {
      submitted_actions: [],
    },
  }
}

async function configureAsr(config: Record<string, unknown>): Promise<void> {
  mockedApi.mockReset()
  mockedApi.mockResolvedValue(config as never)
  await initializeAsr(true)
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  FakeMediaRecorder.instances = []
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  stubMicAccess()
  mockedTranscribe.mockReset()
  mockedPeerClient.mockReset()
  mockedPeerClient.mockReturnValue(null)
})

afterEach(() => {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'mediaDevices')
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
})

describe('appendDictated', () => {
  it('seeds empty drafts with the dictated chunk', () => {
    expect(appendDictated('', ' 推开石门 ')).toBe('推开石门')
    expect(appendDictated('   ', 'check')).toBe('check')
  })

  it('concatenates CJK chunks without separators and keeps existing text', () => {
    expect(appendDictated('我走向大门。', '侧耳倾听')).toBe('我走向大门。侧耳倾听')
  })

  it('inserts a single space between Latin words only', () => {
    expect(appendDictated('Hold the door', 'and listen')).toBe('Hold the door and listen')
    expect(appendDictated('check ', 'perception')).toBe('check perception')
    expect(appendDictated('look at', '石门')).toBe('look at石门')
  })

  it('ignores empty chunks', () => {
    expect(appendDictated('保持原样', '')).toBe('保持原样')
    expect(appendDictated('保持原样', '  ')).toBe('保持原样')
  })
})

describe('asrLanguageFor', () => {
  it('maps UI locales to BCP-47 recognition languages', () => {
    expect(asrLanguageFor('zh-CN')).toBe('zh-CN')
    expect(asrLanguageFor('en')).toBe('en-US')
    expect(asrLanguageFor('ja')).toBe('ja-JP')
  })
})

describe('voiceInputSupported', () => {
  it('stays hidden while the engine is disabled or unconfigured', async () => {
    await configureAsr({})
    expect(voiceInputSupported()).toBe(false)

    await configureAsr({ asr_provider: 'disabled' })
    expect(voiceInputSupported()).toBe(false)

    await configureAsr({ asr_provider: 'openai-compatible', asr_base_url: '' })
    expect(voiceInputSupported()).toBe(false)
  })

  it('shows up once a cloud engine is configured with mic access', async () => {
    await configureAsr({ asr_provider: 'openai-compatible', asr_base_url: 'https://api.example.com/v1' })
    expect(voiceInputSupported()).toBe(true)
  })

  it('stays hidden on insecure origins', async () => {
    await configureAsr({ asr_provider: 'openai-compatible', asr_base_url: 'https://api.example.com/v1' })
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    expect(voiceInputSupported()).toBe(false)
  })

  it('stays hidden in P2P peer games where the upload cannot be relayed', async () => {
    await configureAsr({ asr_provider: 'openai-compatible', asr_base_url: 'https://api.example.com/v1' })
    mockedPeerClient.mockReturnValue({} as never)
    expect(voiceInputSupported()).toBe(false)
  })
})

describe('startRecording', () => {
  it('rejects when the browser has no MediaRecorder support', async () => {
    vi.unstubAllGlobals()
    await expect(startRecording()).rejects.toThrow('asr-record-failed')
  })

  it('rejects with the mic-denied code when permission is refused', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(async () => { throw new DOMException('denied') }) },
    })
    await expect(startRecording()).rejects.toThrow('asr-mic-denied')
  })

  it('returns a session that yields a blob and releases the mic track', async () => {
    const session = await startRecording()

    expect(FakeMediaRecorder.instances).toHaveLength(1)
    const recorder = FakeMediaRecorder.instances[0]
    expect(recorder.state).toBe('recording')

    const blob = await session.stop()

    expect(blob).toBeInstanceOf(Blob)
    expect(recorder.state).toBe('inactive')
  })
})

describe('useVoiceInput', () => {
  it('appends transcribed text through the callback', async () => {
    mockedTranscribe.mockResolvedValue('推开石门，走进大厅')
    const onText = vi.fn()
    const voice = withSetup(() => useVoiceInput({ gameKey: 'web|room|bot', lang: () => 'zh-CN', onText }))

    await voice.start()
    expect(voice.recording.value).toBe(true)

    await voice.stop()
    expect(voice.recording.value).toBe(false)
    expect(voice.transcribing.value).toBe(false)
    expect(mockedTranscribe).toHaveBeenCalledWith('web|room|bot', expect.any(Blob), 'zh-CN')
    expect(onText).toHaveBeenCalledWith('推开石门，走进大厅')
  })

  it('surfaces server errors as messages and stable codes as codes', async () => {
    const { ApiError } = await import('../src/api/client')
    mockedTranscribe.mockRejectedValue(new ApiError('ASR 服务返回 HTTP 401', 400))
    const voice = withSetup(() => useVoiceInput({ gameKey: 'web|room|bot', onText: vi.fn() }))

    await voice.start()
    await voice.stop()

    expect(voice.serverMessage.value).toBe('ASR 服务返回 HTTP 401')
    expect(voice.errorCode.value).toBe('')

    mockedTranscribe.mockReset()
    mockedTranscribe.mockRejectedValue(new Error('boom'))
    await voice.start()
    await voice.stop()

    expect(voice.errorCode.value).toBe('asr-failed')
    expect(voice.serverMessage.value).toBe('')
  })
})

describe('ActionComposer voice input', () => {
  it('hides the mic button when the engine is not configured', async () => {
    await configureAsr({})
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail() },
    })

    expect(wrapper.find('.dictation-toggle').exists()).toBe(false)
    expect(wrapper.get('.composer-row').classes()).not.toContain('has-dictation')
  })

  it('toggles recording from the mic button once the engine is ready', async () => {
    await configureAsr({ asr_provider: 'openai-compatible', asr_base_url: 'https://api.example.com/v1' })
    const wrapper = mount(ActionComposer, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', userId: 'player-1', detail: detail() },
    })
    await vi.waitFor(() => expect(wrapper.find('.dictation-toggle').exists()).toBe(true))
    expect(wrapper.find('.composer-head .dictation-toggle').exists()).toBe(false)
    expect(wrapper.find('.composer-row .dictation-toggle').exists()).toBe(true)
    expect(wrapper.get('.composer-row').classes()).toContain('has-dictation')

    await wrapper.get('.dictation-toggle').trigger('click')
    await vi.waitFor(() => expect(wrapper.get('.dictation-toggle').classes()).toContain('active'))
    expect(FakeMediaRecorder.instances).toHaveLength(1)
    expect(wrapper.find('.dictation-status').exists()).toBe(true)

    mockedTranscribe.mockResolvedValue('推开石门')
    await wrapper.get('.dictation-toggle').trigger('click')
    await vi.waitFor(() => expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('推开石门'))
    expect(wrapper.find('.dictation-toggle').classes()).not.toContain('active')
  })
})

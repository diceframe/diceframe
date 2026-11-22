import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { useSettingsStore } from '../src/stores/useSettingsStore'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../src/api/client', async importOriginal => ({ ...await importOriginal<object>(), api: mocks.api }))
beforeEach(() => { setActivePinia(createPinia()); mocks.api.mockReset() })

it('does not overwrite input entered while the initial request is in flight', async () => {
  let finish!: (value: object) => void
  mocks.api.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const store = useSettingsStore()
  const loading = store.load()
  store.setConfigField('llm_generation_options', { temperature: .35 })
  finish({ llm_generation_options: {}, context_recent_rounds: 0 })
  await loading
  expect(store.config.llm_generation_options).toEqual({ temperature: .35 })
})

it('ignores an older configuration response that arrives after a newer one', async () => {
  let finish!: (value: object) => void
  mocks.api.mockReturnValueOnce(new Promise(resolve => { finish = resolve })).mockResolvedValueOnce({ model: 'new' })
  const store = useSettingsStore()
  const old = store.load()
  await store.load()
  finish({ model: 'old' })
  await old
  expect(store.config.model).toBe('new')
})

it('preserves unrelated unsaved fields when saving a section', async () => {
  mocks.api.mockResolvedValueOnce({ context_recent_rounds: 0, assistant_agent_max_tokens: 4096 })
  const store = useSettingsStore()
  await store.load()
  store.setConfigField('context_recent_rounds', 10)
  store.setConfigField('assistant_agent_max_tokens', 8192)
  mocks.api.mockResolvedValueOnce({}).mockResolvedValueOnce({ context_recent_rounds: 0, assistant_agent_max_tokens: 8192 })
  await store.saveSection(['assistant_agent_max_tokens'])
  expect(store.config.context_recent_rounds).toBe(10)
  expect(store.config.assistant_agent_max_tokens).toBe(8192)
})

it('refreshes the verified embedding selection and preserves unrelated drafts', async () => {
  const initial = {
    embedding_enabled: true, embedding_source: 'external', context_recent_rounds: 0,
    embedding_config_revision: 'before',
  }
  mocks.api.mockResolvedValueOnce(initial)
  const store = useSettingsStore()
  await store.load()
  store.setConfigField('context_recent_rounds', 10)
  const activation = { embedding_enabled: true, embedding_source: 'local', embedding_local_model_id: 'tiny' }
  const activate = store.prepareLocalEmbeddingActivation('tiny')!

  mocks.api.mockRejectedValueOnce(new Error('inference failed'))
  await expect(activate()).rejects.toThrow('inference failed')
  expect(store.config.embedding_source).toBe('external')

  mocks.api.mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({
    ...initial, ...activation, embedding_config_revision: 'after',
  })
  await expect(activate()).resolves.toBe(true)
  expect(mocks.api).toHaveBeenNthCalledWith(3, '/embedding/models/tiny/activate', {
    method: 'POST', body: JSON.stringify({ expected_revision: 'before' }),
  })
  expect(store.config).toMatchObject({ ...activation, context_recent_rounds: 10 })
})

it('skips automatic activation after the owner disables embeddings during download', async () => {
  mocks.api.mockResolvedValueOnce({
    embedding_enabled: true, embedding_source: 'external', embedding_config_revision: 'before',
  })
  const store = useSettingsStore()
  await store.load()
  const activate = store.prepareLocalEmbeddingActivation('tiny')!
  store.setConfigField('embedding_enabled', false)

  await expect(activate()).resolves.toBe(false)
  expect(mocks.api).toHaveBeenCalledTimes(1)
  expect(store.config.embedding_enabled).toBe(false)
})

it('uses the revision captured before download and skips activation after a server conflict', async () => {
  mocks.api.mockResolvedValueOnce({ embedding_config_revision: 'before' })
  const store = useSettingsStore()
  await store.load()
  const activate = store.prepareLocalEmbeddingActivation('tiny')!
  mocks.api.mockRejectedValueOnce(new ApiError('settings changed', 409, 'EMBEDDING_SETTINGS_CHANGED'))

  await expect(activate()).resolves.toBe(false)
  expect(mocks.api).toHaveBeenLastCalledWith('/embedding/models/tiny/activate', {
    method: 'POST', body: JSON.stringify({ expected_revision: 'before' }),
  })
})

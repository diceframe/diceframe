import { describe, expect, it } from 'vitest'
import { contentLanguageOf, filterByContentLanguage } from '@/utils/contentLanguage'

describe('content language helpers', () => {
  const items = [
    { id: 'zh', language: 'zh-CN' },
    { id: 'legacy' },
    { id: 'en', language: 'en' },
    { id: 'en-us', language: 'en-US' },
  ]

  it('treats legacy unmarked content as Chinese', () => {
    expect(contentLanguageOf(items[1])).toBe('zh-CN')
  })

  it('uses one normalized filter for templates and lorebooks', () => {
    expect(filterByContentLanguage(items, 'zh-CN').map(item => item.id)).toEqual(['zh', 'legacy'])
    expect(filterByContentLanguage(items, 'en').map(item => item.id)).toEqual(['en', 'en-us'])
  })
})

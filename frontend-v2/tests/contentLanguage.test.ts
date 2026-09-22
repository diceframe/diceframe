import { describe, expect, it } from 'vitest'
import {
  CONTENT_LANGUAGE_OPTIONS,
  contentLanguageLabelKey,
  contentLanguageOf,
  filterByContentLanguage,
} from '@/utils/contentLanguage'

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

  it('exposes the global Locale set as content language options', () => {
    expect(CONTENT_LANGUAGE_OPTIONS.map(option => option.value)).toEqual(['zh-CN', 'en', 'ja', 'de'])
  })

  it('maps every content language to a self-named label key', () => {
    expect(contentLanguageLabelKey('zh-CN')).toBe('contentLangZhCN')
    expect(contentLanguageLabelKey('en')).toBe('contentLangEn')
    expect(contentLanguageLabelKey('ja')).toBe('contentLangJa')
    expect(contentLanguageLabelKey('de')).toBe('contentLangDe')
  })

  it('filters ja and de content through the same helper', () => {
    const worlds = [
      { id: 'a', language: 'ja' },
      { id: 'b', language: 'de' },
      { id: 'c', language: 'zh-CN' },
    ]
    expect(filterByContentLanguage(worlds, 'ja').map(item => item.id)).toEqual(['a'])
    expect(filterByContentLanguage(worlds, 'de').map(item => item.id)).toEqual(['b'])
  })
})

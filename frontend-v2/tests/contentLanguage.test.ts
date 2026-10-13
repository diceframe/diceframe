import { describe, expect, it } from 'vitest'
import {
  CONTENT_LANGUAGE_OPTIONS,
  contentLanguageLabelKey,
  contentLanguageOf,
  filterByContentLanguage,
  localeLabel,
} from '@/utils/contentLanguage'
import { SUPPORTED_LOCALES, localeChain, localeEndonym, speechLocaleTag } from '@/i18n'

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
    expect(CONTENT_LANGUAGE_OPTIONS.map(option => option.value)).toEqual(['zh-CN', 'en', 'ja', 'de', 'ru'])
  })

  it('maps every content language to a self-named label key', () => {
    expect(contentLanguageLabelKey('zh-CN')).toBe('contentLangZhCN')
    expect(contentLanguageLabelKey('en')).toBe('contentLangEn')
    expect(contentLanguageLabelKey('ja')).toBe('contentLangJa')
    expect(contentLanguageLabelKey('de')).toBe('contentLangDe')
    expect(contentLanguageLabelKey('ru')).toBe('contentLangRu')
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

  it('prefers requested content and falls back to English then Chinese', () => {
    const withRu = [...items, { id: 'ru', language: 'ru' }]
    expect(filterByContentLanguage(withRu, 'ru').map(item => item.id)).toEqual(['ru'])
    expect(filterByContentLanguage(items, 'ru').map(item => item.id)).toEqual(['en', 'en-us'])
    const legacyOnly = [{ id: 'legacy' }] as Array<{ id: string; language?: unknown }>
    expect(filterByContentLanguage(legacyOnly, 'ru').map(item => item.id)).toEqual(['legacy'])
  })
})

describe('locale helpers', () => {
  it('keeps locale fallback and speech tags aligned with the backend', () => {
    expect(localeChain('ru')).toEqual(['ru', 'en', 'zh-CN'])
    expect(localeChain('en')).toEqual(['en', 'zh-CN'])
    expect(speechLocaleTag('ru')).toBe('ru-RU')
  })

  it('exposes every supported locale and an endonym', () => {
    expect(SUPPORTED_LOCALES).toEqual(['zh-CN', 'en', 'ja', 'de', 'ru'])
    for (const locale of SUPPORTED_LOCALES) expect(localeEndonym(locale)).not.toBe(locale)
    expect(localeLabel('ru')).toBe('Русский')
  })
})

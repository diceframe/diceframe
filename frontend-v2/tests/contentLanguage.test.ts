import { describe, expect, it } from 'vitest'
import { contentLanguageOf, filterByContentLanguage, localeLabel } from '@/utils/contentLanguage'
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

  it('prefers content tagged with the requested locale', () => {
    const withRu = [...items, { id: 'ru', language: 'ru' }]
    expect(filterByContentLanguage(withRu, 'ru').map(item => item.id)).toEqual(['ru'])
  })

  it('falls back to English when a locale has no content of its own', () => {
    for (const locale of ['ru', 'ja', 'de'] as const) {
      expect(filterByContentLanguage(items, locale).map(item => item.id)).toEqual(['en', 'en-us'])
    }
  })

  it('falls back to Chinese when there is no English either', () => {
    const chineseOnly = [{ id: 'zh', language: 'zh-CN' }, { id: 'legacy' }]
    expect(filterByContentLanguage(chineseOnly, 'ru').map(item => item.id)).toEqual(['zh', 'legacy'])
  })

  it('returns nothing rather than guessing when the list is empty', () => {
    expect(filterByContentLanguage([], 'ru')).toEqual([])
  })
})

describe('locale fallback chain', () => {
  it('mirrors the backend order: requested, English, Chinese', () => {
    expect(localeChain('ru')).toEqual(['ru', 'en', 'zh-CN'])
    expect(localeChain('ja')).toEqual(['ja', 'en', 'zh-CN'])
  })

  it('never repeats a locale already at the head of the chain', () => {
    expect(localeChain('en')).toEqual(['en', 'zh-CN'])
    expect(localeChain('zh-CN')).toEqual(['zh-CN', 'en'])
  })
})

describe('speech locale tags', () => {
  it('maps every supported locale to its own BCP-47 tag', () => {
    expect(speechLocaleTag('zh-CN')).toBe('zh-CN')
    expect(speechLocaleTag('en')).toBe('en-US')
    expect(speechLocaleTag('ja')).toBe('ja-JP')
    expect(speechLocaleTag('de')).toBe('de-DE')
    expect(speechLocaleTag('ru')).toBe('ru-RU')
  })
})

describe('content language label', () => {
  it('uses the endonym for locales without a message key', () => {
    expect(localeLabel('ru')).toBe('Русский')
    expect(localeLabel('ja')).toBe('日本語')
  })
})

describe('supported locale list', () => {
  it('is the single source every language picker renders from', () => {
    expect(SUPPORTED_LOCALES).toEqual(['zh-CN', 'en', 'ja', 'de', 'ru'])
  })

  it('gives every locale an endonym, so no picker can silently omit one', () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(localeEndonym(locale)).not.toBe(locale)
      expect(localeEndonym(locale).length).toBeGreaterThan(0)
    }
    expect(localeEndonym('ru')).toBe('Русский')
    expect(localeEndonym('de')).toBe('Deutsch')
  })

  it('covers every locale in the speech tag map', () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(speechLocaleTag(locale)).toMatch(/^[a-z]{2}-[A-Z]{2}$/)
    }
  })
})

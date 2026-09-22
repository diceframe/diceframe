import { normalizeLocale, type Locale, type MessageKey } from '@/i18n'

export interface LanguageTaggedContent {
  language?: unknown
}

/**
 * 内容语言选项契约：值就是全局 Locale，不在 Lorebook 里再养一套语言枚举。
 * 语言名统一显示自身名称（简体中文 / English / 日本語 / Deutsch），文案走 i18n。
 */
export const CONTENT_LANGUAGE_OPTIONS: ReadonlyArray<{ value: Locale; labelKey: MessageKey }> = [
  { value: 'zh-CN', labelKey: 'contentLangZhCN' },
  { value: 'en', labelKey: 'contentLangEn' },
  { value: 'ja', labelKey: 'contentLangJa' },
  { value: 'de', labelKey: 'contentLangDe' },
]

export function contentLanguageLabelKey(locale: Locale): MessageKey {
  return CONTENT_LANGUAGE_OPTIONS.find(option => option.value === locale)?.labelKey ?? 'contentLangZhCN'
}

/** Built-in legacy content without a language marker is Chinese. */
export function contentLanguageOf(item: LanguageTaggedContent | null | undefined): Locale {
  return normalizeLocale(item?.language)
}

export function filterByContentLanguage<T extends LanguageTaggedContent>(
  items: T[],
  language: Locale,
): T[] {
  return items.filter(item => contentLanguageOf(item) === language)
}
